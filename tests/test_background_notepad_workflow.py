"""
Test Suite: Background Notepad Workflow Lifecycle & Regression Verification
Verifies:
  1. Worker claims and executes a queued task (QUEUED -> EXECUTING -> COMPLETED).
  2. Each step transitions through correct status with start_time, end_time, and structured results.
  3. Launch failure is reported honestly and prevents downstream steps from executing.
  4. Window activation timeout halts the task without hanging.
  5. Typing failure prevents false success (halts immediately).
  6. Verification failure marks task FAILED and prevents completion.
  7. Approval required in ask_permission mode transitions task to AWAITING_APPROVAL with approval_id,
     and decision resumes execution.
  8. Cancellation halts execution cleanly.
  9. Emergency stop halts task immediately.
 10. Reconnect replayed SSE events contain both payload and data matching live event schema.
 11. Concurrent tasks do not corrupt shared state.
"""

import sys
import os
import asyncio
import time
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

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
from semantic_planner import PlanStep, SemanticPlan
from permission_engine import permission_engine, ActionCategory, RiskLevel, PermissionDecision


class TestBackgroundNotepadWorkflow(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.client = TestClient(app)
        permission_engine.clear_emergency_stop()

    def tearDown(self):
        permission_engine.clear_emergency_stop()
        for tid, worker in list(orchestrator.active_tasks.items()):
            if worker and not worker.done():
                worker.cancel()
        orchestrator.active_tasks.clear()

    def _mock_plan(self):
        return SemanticPlan(
            goal="Open notepad and write about f1",
            strategy="System Agent Notepad automation",
            steps=[
                PlanStep(
                    step_number=1,
                    title="Launch Notepad application",
                    agent="System Agent",
                    tool_name="system_launch_app",
                    params={"app": "notepad"},
                    category=ActionCategory.SYSTEM_LAUNCH,
                    risk_level=RiskLevel.MEDIUM,
                    reason="Launch text editor",
                    expected_outcome="Notepad application launched"
                ),
                PlanStep(
                    step_number=2,
                    title="Wait for Notepad window to be active",
                    agent="System Agent",
                    tool_name="system_wait_for_window",
                    params={"title": "Notepad"},
                    category=ActionCategory.SYSTEM_LAUNCH,
                    risk_level=RiskLevel.LOW,
                    reason="Ensure window is focused",
                    expected_outcome="Notepad window active"
                ),
                PlanStep(
                    step_number=3,
                    title="Type F1 racing content into Notepad",
                    agent="System Agent",
                    tool_name="system_type_into_window",
                    params={"text": "Formula 1 is the pinnacle of motorsport."},
                    category=ActionCategory.SYSTEM_LAUNCH,
                    risk_level=RiskLevel.LOW,
                    reason="Type note content",
                    expected_outcome="Text typed into Notepad"
                ),
                PlanStep(
                    step_number=4,
                    title="Verify text was entered correctly",
                    agent="System Agent",
                    tool_name="system_verify_window_text",
                    params={"title": "Notepad", "expected_snippet": "Formula 1"},
                    category=ActionCategory.SAFE_READ,
                    risk_level=RiskLevel.LOW,
                    reason="Verify text presence",
                    expected_outcome="Text verified in window"
                ),
            ]
        )

    # =========================================================================
    # Test 1: Worker claims and executes a queued task to COMPLETED
    # =========================================================================
    async def test_01_worker_claims_and_executes_queued_task(self):
        plan = self._mock_plan()
        with patch.object(orchestrator, "_formulate_multi_agent_plan", return_value=plan):
            with patch.object(orchestrator, "_dispatch_agent_action", new_callable=AsyncMock) as mock_dispatch:
                mock_dispatch.side_effect = [
                    {"success": True, "app": "notepad", "pid": 1234, "message": "Launched Notepad"},
                    {"success": True, "hwnd": 5678, "title": "Untitled - Notepad", "message": "Window focused"},
                    {"success": True, "characters_typed": 42, "message": "Typed content"},
                    {"success": True, "verified": True, "snippet_found": True, "message": "Verified snippet"},
                ]

                task_id = await orchestrator.create_and_run_task(
                    prompt="Open notepad and write about f1",
                    workspace=str(ROOT_DIR),
                    permission_mode="full_access"
                )

                worker = orchestrator.active_tasks.get(task_id)
                self.assertIsNotNone(worker)
                await worker

                t_rec = db.get_task(task_id)
                self.assertEqual(t_rec.get("status"), TaskState.COMPLETED)
                self.assertIn("Task Complete", t_rec.get("result", ""))

                # Verify all 4 steps recorded start_time, end_time, and status
                plan_data = t_rec.get("plan", {})
                recorded_steps = plan_data.get("steps", [])
                self.assertEqual(len(recorded_steps), 4)
                for s in recorded_steps:
                    self.assertEqual(s.get("status"), "COMPLETED")
                    self.assertIsNotNone(s.get("start_time"))
                    self.assertIsNotNone(s.get("end_time"))
                    self.assertGreaterEqual(s.get("duration_seconds", 0), 0)
                    self.assertTrue(s.get("result", {}).get("success"))

    # =========================================================================
    # Test 2: Launch failure is reported honestly and halts downstream steps
    # =========================================================================
    async def test_02_launch_failure_reported_honestly_and_halts(self):
        plan = self._mock_plan()
        with patch.object(orchestrator, "_formulate_multi_agent_plan", return_value=plan):
            with patch.object(orchestrator, "_attempt_action_recovery", new_callable=AsyncMock, return_value=None):
                with patch.object(orchestrator, "_dispatch_agent_action", new_callable=AsyncMock) as mock_dispatch:
                    # Step 1 fails
                    mock_dispatch.return_value = {"success": False, "error": "Application executable not found: notepad"}

                    task_id = await orchestrator.create_and_run_task(
                        prompt="Open notepad and write about f1",
                        workspace=str(ROOT_DIR),
                        permission_mode="full_access"
                    )

                    worker = orchestrator.active_tasks.get(task_id)
                    await worker

                    t_rec = db.get_task(task_id)
                    self.assertEqual(t_rec.get("status"), TaskState.FAILED)
                    self.assertIn("Application executable not found", t_rec.get("result", ""))

                    # Downstream steps not invoked
                    self.assertEqual(mock_dispatch.call_count, 1)

                    # Check steps: step 1 is FAILED, steps 2-4 remain PENDING
                    recorded_steps = t_rec.get("plan", {}).get("steps", [])
                    self.assertEqual(recorded_steps[0].get("status"), "FAILED")
                    for downstream in recorded_steps[1:]:
                        self.assertEqual(downstream.get("status"), "PENDING")

    # =========================================================================
    # Test 3: Window activation timeout halts the task honestly
    # =========================================================================
    async def test_03_window_activation_timeout_halts_task(self):
        plan = self._mock_plan()
        with patch.object(orchestrator, "_formulate_multi_agent_plan", return_value=plan):
            with patch.object(orchestrator, "_dispatch_agent_action", new_callable=AsyncMock) as mock_dispatch:
                mock_dispatch.side_effect = [
                    {"success": True, "app": "notepad", "pid": 1234},
                    {"success": False, "error": "Window focus timed out after 8.0s"},
                ]

                task_id = await orchestrator.create_and_run_task(
                    prompt="Open notepad and write about f1",
                    workspace=str(ROOT_DIR),
                    permission_mode="full_access"
                )

                worker = orchestrator.active_tasks.get(task_id)
                await worker

                t_rec = db.get_task(task_id)
                self.assertEqual(t_rec.get("status"), TaskState.FAILED)
                self.assertIn("Window focus timed out", t_rec.get("result", ""))
                self.assertEqual(mock_dispatch.call_count, 2)

    # =========================================================================
    # Test 4: Typing failure prevents false success
    # =========================================================================
    async def test_04_typing_failure_prevents_false_success(self):
        plan = self._mock_plan()
        with patch.object(orchestrator, "_formulate_multi_agent_plan", return_value=plan):
            with patch.object(orchestrator, "_dispatch_agent_action", new_callable=AsyncMock) as mock_dispatch:
                mock_dispatch.side_effect = [
                    {"success": True, "app": "notepad"},
                    {"success": True, "hwnd": 1111},
                    {"success": False, "error": "UIAutomation target edit control not accessible"},
                ]

                task_id = await orchestrator.create_and_run_task(
                    prompt="Open notepad and write about f1",
                    workspace=str(ROOT_DIR),
                    permission_mode="full_access"
                )

                worker = orchestrator.active_tasks.get(task_id)
                await worker

                t_rec = db.get_task(task_id)
                self.assertEqual(t_rec.get("status"), TaskState.FAILED)
                # Step 4 verification was NOT called
                self.assertEqual(mock_dispatch.call_count, 3)

    # =========================================================================
    # Test 5: Verification failure prevents task completion
    # =========================================================================
    async def test_05_verification_failure_prevents_completion(self):
        plan = self._mock_plan()
        with patch.object(orchestrator, "_formulate_multi_agent_plan", return_value=plan):
            with patch.object(orchestrator, "_dispatch_agent_action", new_callable=AsyncMock) as mock_dispatch:
                mock_dispatch.side_effect = [
                    {"success": True, "app": "notepad"},
                    {"success": True, "hwnd": 1111},
                    {"success": True, "characters_typed": 40},
                    {"success": False, "verified": False, "error": "Snippet 'Formula 1' not found in window text"},
                ]

                task_id = await orchestrator.create_and_run_task(
                    prompt="Open notepad and write about f1",
                    workspace=str(ROOT_DIR),
                    permission_mode="full_access"
                )

                worker = orchestrator.active_tasks.get(task_id)
                await worker

                t_rec = db.get_task(task_id)
                self.assertEqual(t_rec.get("status"), TaskState.FAILED)
                self.assertIn("Snippet 'Formula 1' not found", t_rec.get("result", ""))

    # =========================================================================
    # Test 6: Approval required in ask_permission mode emits approval_id and pauses
    # =========================================================================
    async def test_06_approval_lifecycle_pause_and_resume(self):
        plan = self._mock_plan()
        captured_events = []
        async def mock_broadcast(task_id, event_type, payload):
            captured_events.append((event_type, payload))

        with patch.object(orchestrator, "_formulate_multi_agent_plan", return_value=plan):
            with patch.object(orchestrator, "broadcast_event", side_effect=mock_broadcast):
                with patch.object(orchestrator, "_dispatch_agent_action", new_callable=AsyncMock) as mock_dispatch:
                    mock_dispatch.return_value = {"success": True, "app": "notepad"}

                    task_id = await orchestrator.create_and_run_task(
                        prompt="Open notepad and write about f1",
                        workspace=str(ROOT_DIR),
                        permission_mode="ask_permission"
                    )

                    worker = orchestrator.active_tasks.get(task_id)

                    # Give it a short moment to reach approval requirement
                    await asyncio.sleep(0.1)

                    # Verify APPROVAL_REQUIRED was emitted with an approval_id
                    appr_events = [e for e in captured_events if e[0] == "APPROVAL_REQUIRED"]
                    self.assertGreaterEqual(len(appr_events), 1)
                    approval_id = appr_events[0][1].get("approval_id")
                    self.assertIsNotNone(approval_id)
                    self.assertTrue(approval_id.startswith("appr-"))

                    # Resolve the approval via permission_engine with allow_full_access
                    permission_engine.resolve_approval(approval_id, "allow_full_access")

                    # Worker should now resume and complete remaining steps
                    await worker

                    t_rec = db.get_task(task_id)
                    self.assertEqual(t_rec.get("status"), TaskState.COMPLETED)

    # =========================================================================
    # Test 7: Task cancellation halts execution
    # =========================================================================
    async def test_07_task_cancellation_halts_cleanly(self):
        plan = self._mock_plan()
        with patch.object(orchestrator, "_formulate_multi_agent_plan", return_value=plan):
            with patch.object(orchestrator, "_dispatch_agent_action", new_callable=AsyncMock) as mock_dispatch:
                async def slow_wait(*args, **kwargs):
                    await asyncio.sleep(2.0)
                    return {"success": True}
                mock_dispatch.side_effect = [
                    {"success": True, "app": "notepad"},
                    slow_wait
                ]

                task_id = await orchestrator.create_and_run_task(
                    prompt="Open notepad and write about f1",
                    workspace=str(ROOT_DIR),
                    permission_mode="full_access"
                )

                worker = orchestrator.active_tasks.get(task_id)
                await asyncio.sleep(0.05)

                cancel_success = await orchestrator.cancel_task(task_id)
                self.assertTrue(cancel_success)

                await worker

                t_rec = db.get_task(task_id)
                self.assertEqual(t_rec.get("status"), TaskState.CANCELLED)

    # =========================================================================
    # Test 8: Emergency Stop halts task execution immediately
    # =========================================================================
    async def test_08_emergency_stop_halts_task(self):
        permission_engine.trigger_emergency_stop()

        with self.assertRaises(RuntimeError) as ctx:
            await orchestrator.create_and_run_task(
                prompt="Open notepad and write about f1",
                workspace=str(ROOT_DIR),
                permission_mode="full_access"
            )
        self.assertIn("Emergency stop is currently active", str(ctx.exception))

    # =========================================================================
    # Test 9: Replayed SSE events match live event format (data + payload)
    # =========================================================================
    async def test_09_reconnect_replayed_sse_events_schema(self):
        task_id = f"test_wf_sse_{uuid.uuid4().hex[:8]}"
        db.create_task(task_id, "Open notepad and write about f1", str(ROOT_DIR), "full_access", "test-model")
        db.add_task_event(task_id, "TASK_STARTED", {"task_id": task_id, "status": "EXECUTING"})
        db.add_task_event(task_id, "ACTION_EXECUTING", {"task_id": task_id, "step_index": 0, "action": "system_launch_app"})

        from main import sse_task_events
        mock_req = MagicMock()
        mock_req.is_disconnected = AsyncMock(return_value=False)
        resp = await sse_task_events(task_id, mock_req)
        gen = resp.body_iterator
        first_event = await gen.__anext__()
        import json
        lines = first_event.strip().split("\n")
        data_line = [l for l in lines if l.startswith("data: ")][0]
        event_dict = json.loads(data_line[6:])

        self.assertEqual(event_dict.get("event_type"), "TASK_STARTED")
        # Ensure both data and payload are present for frontend reconciliation
        self.assertIn("data", event_dict)
        self.assertIn("payload", event_dict)
        self.assertEqual(event_dict["data"].get("task_id"), task_id)

    # =========================================================================
    # Test 10: Concurrent tasks do not corrupt shared state
    # =========================================================================
    async def test_10_concurrent_tasks_isolation(self):
        plan1 = self._mock_plan()
        plan1.steps = plan1.steps[:2]
        plan2 = self._mock_plan()
        plan2.steps = plan2.steps[:2]

        with patch.object(orchestrator, "_formulate_multi_agent_plan", side_effect=[plan1, plan2]):
            with patch.object(orchestrator, "_dispatch_agent_action", new_callable=AsyncMock) as mock_dispatch:
                mock_dispatch.return_value = {"success": True}

                t1 = await orchestrator.create_and_run_task("Task 1", str(ROOT_DIR), "full_access")
                t2 = await orchestrator.create_and_run_task("Task 2", str(ROOT_DIR), "full_access")

                w1 = orchestrator.active_tasks.get(t1)
                w2 = orchestrator.active_tasks.get(t2)

                await asyncio.gather(w1, w2)

                r1 = db.get_task(t1)
                r2 = db.get_task(t2)

                self.assertEqual(r1.get("status"), TaskState.COMPLETED)
                self.assertEqual(r2.get("status"), TaskState.COMPLETED)
                self.assertNotEqual(r1.get("id"), r2.get("id"))


if __name__ == "__main__":
    unittest.main()
