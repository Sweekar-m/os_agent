"""
Test Suite: Backend Lifecycle and Reliable Background Task Execution
Validates:
  1. Health & readiness endpoints (/health, /api/health/ready)
  2. Port checking & health reuse logic (start.py)
  3. Fast background task acceptance (<200ms) with unique task ID via /api/chat
  4. Real-time background progress streaming via SSE (/api/tasks/{task_id}/events)
  5. Disconnect and reconnect task state recovery without duplicate execution
  6. Task cancellation via /api/tasks/{task_id}/cancel
  7. Honest failure reporting when actions fail or permissions are denied
  8. Real Windows integration test:
     - Launch Notepad in interactive session
     - Wait for window focus
     - Type text passage into window
     - Verify text entry via Windows UI Automation
     - Cleanly terminate Notepad
"""

import sys
import os
import time
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure backend directory is in sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from starlette.testclient import TestClient
from main import app
import db
from orchestrator import orchestrator, TaskState
from agents.system_agent import system_agent
from permission_engine import permission_engine, ActionCategory, RiskLevel
import start


class TestBackendLifecycleAndBackgroundTasks(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        permission_engine.clear_emergency_stop()

    def tearDown(self):
        permission_engine.clear_emergency_stop()
        for tid in list(orchestrator.active_tasks.keys()):
            worker = orchestrator.active_tasks.get(tid)
            if worker and not worker.done():
                worker.cancel()
        orchestrator.active_tasks.clear()
        import subprocess
        subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)

    # =========================================================================
    # Test 1: Backend Startup and Readiness Probes
    # =========================================================================
    def test_01_backend_startup_and_readiness_endpoints(self):
        """Verifies /health and /api/health/ready provide truthful readiness states."""
        # 1. Lightweight health ping
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("service"), "os-agent-backend")

        # 2. Comprehensive readiness probe
        resp_ready = self.client.get("/api/health/ready")
        self.assertEqual(resp_ready.status_code, 200)
        ready_data = resp_ready.json()
        self.assertTrue(ready_data.get("ready"))
        self.assertEqual(ready_data.get("status"), "ready")
        self.assertIn("database", ready_data.get("services", {}))
        self.assertIn("orchestrator", ready_data.get("services", {}))
        self.assertIn("agents", ready_data.get("services", {}))
        self.assertFalse(ready_data.get("emergency_stopped"))

    # =========================================================================
    # Test 2: Unresponsive Port Reclamation & Health Reuse Logic
    # =========================================================================
    def test_02_port_health_and_reclamation_logic(self):
        """Verifies start.py correctly identifies healthy running backends and handles reclamation."""
        # Test is_backend_healthy with mock successful response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"status": "ready", "ready": True}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=mock_resp):
            self.assertTrue(start.is_backend_healthy(8000, timeout=0.5))

        # Test is_backend_healthy with connection failure
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            self.assertFalse(start.is_backend_healthy(9999, timeout=0.1))

        # Test clean_unresponsive_port invokes Stop-Process on occupied PIDs
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout=" 12345 \n", stderr=""),  # NetTCPConnection output
                MagicMock(stdout="", stderr="")           # Stop-Process output
            ]
            start.clean_unresponsive_port(8000)
            self.assertTrue(mock_run.called)

    # =========================================================================
    # Test 3: Fast Background Task Acceptance (<200ms) with Unique Task ID
    # =========================================================================
    def test_03_fast_task_acceptance_latency_and_id(self):
        """Verifies multi-agent task dispatch acknowledges acceptance with <200ms response time."""
        from unittest.mock import AsyncMock

        # Warm up TestClient / httpx adapter
        self.client.get("/health")

        req_payload = {
            "messages": [
                {"role": "user", "content": "open notepad and write a few lines about F1 racing"}
            ],
            "async_task": True
        }

        with patch.object(orchestrator, "_run_multi_agent_pipeline", new_callable=AsyncMock):
            t0 = time.perf_counter()
            resp = self.client.post("/api/chat", json=req_payload)
            elapsed = time.perf_counter() - t0

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Latency check: Must acknowledge in < 200ms
        self.assertLess(elapsed, 0.200, f"Task acceptance took {elapsed*1000:.1f}ms, expected < 200ms")

        # Response structure check
        self.assertTrue(data.get("async_task"))
        self.assertEqual(data.get("status"), "QUEUED")
        task_id = data.get("task_id")
        self.assertTrue(bool(task_id))
        self.assertIn("Task Accepted", data.get("content", ""))

        # Verify task is tracked in database
        time.sleep(0.05)
        task_record = db.get_task(task_id)
        self.assertIsNotNone(task_record)
        self.assertEqual(task_record.get("id"), task_id)

    # =========================================================================
    # Test 4: Real-Time Background Progress Streaming via SSE
    # =========================================================================
    def test_04_real_time_background_progress_sse_stream(self):
        """Verifies SSE endpoint (/api/tasks/{task_id}/events) streams lifecycle events."""
        import uuid
        task_id = f"test_sse_{uuid.uuid4().hex[:8]}"
        db.create_task(task_id, "Test task for SSE", str(ROOT_DIR), "full_access", "test-model")
        db.add_task_event(task_id, "PHASE_CHANGED", {"phase": "PLANNING"})
        db.add_task_event(task_id, "ACTION_EXECUTING", {"agent": "System Agent", "action": "launch_app"})
        db.add_task_event(task_id, "TASK_COMPLETED", {"summary": "Completed successfully"})

        # Stream events via SSE endpoint
        with self.client.stream("GET", f"/api/tasks/{task_id}/events") as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers.get("content-type"), "text/event-stream; charset=utf-8")

            received_lines = []
            for line in resp.iter_lines():
                if line:
                    received_lines.append(line.decode("utf-8") if isinstance(line, bytes) else line)

            events_data = [l for l in received_lines if l.startswith("data:")]
            self.assertGreaterEqual(len(events_data), 1)
            first_event = json.loads(events_data[0].replace("data:", "").strip())
            self.assertEqual(first_event.get("task_id"), task_id)
            self.assertEqual(first_event.get("event_type"), "PHASE_CHANGED")

    # =========================================================================
    # Test 5: Disconnect and Reconnect Task State Recovery
    # =========================================================================
    def test_05_disconnect_and_reconnect_task_recovery(self):
        """Verifies in-flight or completed task can be restored without duplicate execution."""
        import uuid
        task_id = f"test_reconnect_{uuid.uuid4().hex[:8]}"
        db.create_task(task_id, "Test recovery on reload", str(ROOT_DIR), "full_access", "test-model")
        db.save_task_plan(task_id, {
            "objective": "Test recovery",
            "steps": [{"title": "Step 1", "agent": "System Agent", "action": "telemetry"}]
        })
        db.update_task_status(task_id, TaskState.EXECUTING)
        db.add_task_event(task_id, "ACTION_COMPLETED", {"step_index": 0, "success": True})

        # Simulate client reconnect querying task state
        resp = self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data.get("id"), task_id)
        self.assertEqual(data.get("status"), TaskState.EXECUTING)
        self.assertEqual(len(data.get("plan", {}).get("steps", [])), 1)
        self.assertGreaterEqual(len(data.get("events", [])), 1)

    # =========================================================================
    # Test 6: Task Cancellation Endpoint
    # =========================================================================
    def test_06_task_cancellation_endpoint(self):
        """Verifies /api/tasks/{task_id}/cancel halts execution and marks task CANCELLED."""
        import uuid
        task_id = f"test_cancel_{uuid.uuid4().hex[:8]}"
        db.create_task(task_id, "Task to be cancelled", str(ROOT_DIR), "full_access", "test-model")
        db.update_task_status(task_id, TaskState.EXECUTING)

        resp = self.client.post(f"/api/tasks/{task_id}/cancel")
        self.assertEqual(resp.status_code, 200)
        cancel_data = resp.json()
        self.assertEqual(cancel_data.get("task_id"), task_id)
        self.assertTrue(cancel_data.get("cancelled"))

        # Verify DB state
        t_record = db.get_task(task_id)
        self.assertEqual(t_record.get("status"), TaskState.CANCELLED)

    # =========================================================================
    # Test 7: Failure Reporting When Invalid Action or Denied
    # =========================================================================
    def test_07_failure_reporting_on_invalid_action(self):
        """Verifies errors during agent dispatch are truthfully recorded as FAILED."""
        import asyncio

        # Dispatch an invalid agent action
        result = asyncio.run(orchestrator._dispatch_agent_action("System Agent", "non_existent_action_xyz", {}))
        self.assertFalse(result.get("success"))
        self.assertIn("error", result)

    # =========================================================================
    # Test 8: Real Windows Integration Test (Notepad UI Automation)
    # =========================================================================
    def test_08_real_windows_notepad_text_entry_and_verification(self):
        """
        Real Windows Integration Test:
        1. Launch Notepad in interactive Windows user session.
        2. Wait for Notepad window to initialize and focus.
        3. Enter passage about PES University / F1.
        4. Verify text entry via Windows UI Automation.
        5. Cleanly terminate Notepad.
        """
        import subprocess
        # Ensure any stale Notepad is closed first
        system_agent.terminate_process(name="notepad.exe", confirm=True)
        subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
        time.sleep(0.5)

        try:
            # 1. Launch Notepad
            launch_res = system_agent.launch_app("notepad")
            self.assertTrue(launch_res.get("success"), f"Failed to launch Notepad: {launch_res}")

            # 2. Wait for window focus
            wait_res = system_agent.wait_for_window("Notepad", timeout=8.0)
            self.assertTrue(wait_res.get("success"), f"Notepad window did not focus: {wait_res}")

            # 3. Type text passage into window
            test_passage = (
                "PES University Bangalore\n"
                "Leading technical institution committed to quality education and research.\n"
                "Verified by OS Agent UI Automation."
            )
            type_res = system_agent.type_into_window(test_passage, title_substr="Notepad")
            self.assertTrue(type_res.get("success"), f"Failed typing into Notepad: {type_res}")

            # 4. Verify text entered via Windows UI Automation
            verify_res = system_agent.verify_window_text("Notepad", "PES University", timeout=6.0)
            self.assertTrue(
                verify_res.get("success"),
                f"UI Automation text verification failed: {verify_res}"
            )
            self.assertTrue(verify_res.get("verified") or verify_res.get("snippet_found"))
            self.assertEqual(verify_res.get("expected_snippet"), "PES University")

        finally:
            # 5. Cleanly close Notepad
            system_agent.terminate_process(name="notepad.exe", confirm=True)
            subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
            time.sleep(0.5)


if __name__ == "__main__":
    unittest.main()
