import unittest
import os
import sys
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from starlette.testclient import TestClient
from main import app
from intent_router import intent_router, AgentIntent
from agents.file_agent import file_agent
from permission_engine import permission_engine

class TestDesktopAndRemoteFileOpening(unittest.TestCase):
    """
    Regression test suite verifying:
    1. Desktop chat flow: 'Find my resume PDF.' -> 'Open it.'
    2. Phone remote WebSocket flow: 'Find my resume PDF.' -> 'Open it.'
    3. Direct opening: 'Open my resume PDF.'
    4. Honest error reporting when files are not found or cannot be launched
    5. Truthful desktop session detection (interactive vs Session 0)
    6. Complete separation of outcomes (file_found, open_requested, command_dispatched, visibly_opened)
    7. No false desktop limitation disclaimers
    """

    def setUp(self):
        self.client = TestClient(app)
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_desktop_open_"))
        # Create a sample resume PDF
        self.resume_file = self.test_dir / "Sweekar_Resume.pdf"
        self.resume_file.write_bytes(b"%PDF-1.4 mock resume content")

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)
        # Clear emergency stop if any test touched it
        permission_engine.clear_emergency_stop()

    def test_acceptance_flow_desktop_chat(self):
        """
        Acceptance Test:
        User: 'Find my resume PDF.'
        Assistant: returns a verified filename and path.
        User: 'Open it.'
        Assistant: invokes the Windows open tool and reports the actual outcome.
        """
        # Patch search_files to include our test directory
        orig_search_files = file_agent.search_files

        def patched_search(query, search_dir=None, max_results=50, extension=None, search_content=False):
            if "resume" in query.lower():
                return {
                    "success": True,
                    "query": query,
                    "search_directory": str(self.test_dir),
                    "scanned_directories": [str(self.test_dir)],
                    "total_matches": 1,
                    "matches": [{
                        "filename": "Sweekar_Resume.pdf",
                        "path": str(self.resume_file.resolve()),
                        "relative_path": "Sweekar_Resume.pdf",
                        "match_type": "filename",
                        "size_bytes": self.resume_file.stat().st_size,
                        "size_display": "29 B",
                        "modified": "2026-10-22 14:00"
                    }]
                }
            return orig_search_files(query, search_dir, max_results, extension, search_content)

        with patch.object(file_agent, "search_files", side_effect=patched_search):
            # Turn 1: Find my resume PDF.
            t1_resp = self.client.post("/api/chat", json={
                "messages": [
                    {"role": "user", "content": "Find my resume PDF."}
                ]
            })
            self.assertEqual(t1_resp.status_code, 200)
            t1_data = t1_resp.json()

            # Verify verified filename and full path are returned
            self.assertIn("Sweekar_Resume.pdf", t1_data["content"])
            self.assertIn(str(self.resume_file.resolve()), t1_data["content"])
            # Verify NO false desktop limitation disclaimer
            self.assertNotIn("can't actually interact with the desktop", t1_data["content"].lower())
            self.assertNotIn("beyond search", t1_data["content"].lower())
            self.assertNotIn("cannot open", t1_data["content"].lower())

            # Verify file_found outcome in actions
            self.assertTrue(len(t1_data.get("actions", [])) >= 1)
            t1_action = t1_data["actions"][0]
            self.assertEqual(t1_action["type"], "file_search")
            self.assertTrue(t1_action.get("file_found", False))

            # Turn 2: Open it.
            with patch("os.startfile") as mock_startfile:
                t2_resp = self.client.post("/api/chat", json={
                    "messages": [
                        {"role": "user", "content": "Find my resume PDF."},
                        {"role": "assistant", "content": t1_data["content"]},
                        {"role": "user", "content": "Open it."}
                    ]
                })
                self.assertEqual(t2_resp.status_code, 200)
                t2_data = t2_resp.json()

                # Verify Windows open tool (os.startfile) was actually invoked with the verified path
                mock_startfile.assert_called_once_with(str(self.resume_file.resolve()))

                # Verify response reports real outcome without false disclaimers
                self.assertIn("Opened `Sweekar_Resume.pdf`", t2_data["content"])
                self.assertNotIn("can't actually interact with the desktop", t2_data["content"].lower())

                # Verify separated outcome fields in actions
                self.assertTrue(len(t2_data.get("actions", [])) >= 1)
                t2_action = t2_data["actions"][0]
                self.assertEqual(t2_action["type"], "open_file")
                self.assertEqual(t2_action["status"], "success")
                self.assertTrue(t2_action["file_found"])
                self.assertTrue(t2_action["open_requested"])
                self.assertTrue(t2_action["command_dispatched"])
                self.assertIn("visibly_opened", t2_action)
                self.assertIn("desktop_session", t2_action)

    def test_direct_open_resume_pdf(self):
        """Test user directly saying 'Open my resume PDF'."""
        with patch.object(file_agent, "open_file") as mock_open:
            mock_open.return_value = {
                "success": True,
                "filename": "Sweekar_Resume.pdf",
                "path": str(self.resume_file.resolve()),
                "file_found": True,
                "open_requested": True,
                "command_dispatched": True,
                "visibly_opened": False,
                "desktop_session": {"is_interactive": True, "session_id": 1},
                "message": "Dispatched open command."
            }

            resp = self.client.post("/api/chat", json={
                "messages": [
                    {"role": "user", "content": "Open my resume PDF"}
                ]
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            mock_open.assert_called_once()
            self.assertIn("Opened `Sweekar_Resume.pdf`", data["content"])
            action = data["actions"][0]
            self.assertEqual(action["type"], "open_file")
            self.assertTrue(action["file_found"])
            self.assertTrue(action["command_dispatched"])

    def test_phone_remote_websocket_acceptance_flow(self):
        """
        Regression Test: Phone Remote WebSocket connection preserves context across turns
        and dispatches real file open actions on the PC.
        """
        # Patch search to return the test resume
        def patched_search(query, search_dir=None, max_results=50, extension=None, search_content=False):
            return {
                "success": True,
                "query": query,
                "search_directory": str(self.test_dir),
                "scanned_directories": [str(self.test_dir)],
                "total_matches": 1,
                "matches": [{
                    "filename": "Sweekar_Resume.pdf",
                    "path": str(self.resume_file.resolve()),
                    "relative_path": "Sweekar_Resume.pdf",
                    "match_type": "filename",
                    "size_bytes": 29,
                    "size_display": "29 B",
                    "modified": "2026-10-22 14:00"
                }]
            }

        def receive_non_frame(ws):
            while True:
                msg = ws.receive_json()
                if msg.get("type") != "frame":
                    return msg

        with patch.object(file_agent, "search_files", side_effect=patched_search):
            with patch("os.startfile") as mock_startfile:
                with self.client.websocket_connect("/ws/remote") as ws:
                    # Initial connection status frame
                    init_frame = receive_non_frame(ws)
                    self.assertEqual(init_frame["type"], "status")

                    # Turn 1: Find my resume PDF.
                    ws.send_json({"type": "prompt", "text": "Find my resume PDF."})
                    f1 = receive_non_frame(ws)
                    self.assertEqual(f1["type"], "agent_thinking")
                    f2 = receive_non_frame(ws)
                    self.assertEqual(f2["type"], "agent_response")
                    self.assertIn("Sweekar_Resume.pdf", f2["content"])
                    self.assertNotIn("can't actually interact with the desktop", f2["content"].lower())

                    # Turn 2: Open it.
                    ws.send_json({"type": "prompt", "text": "Open it."})
                    f3 = receive_non_frame(ws)
                    self.assertEqual(f3["type"], "agent_thinking")
                    f4 = receive_non_frame(ws)
                    self.assertEqual(f4["type"], "agent_response")

                    # Verify open was dispatched on the PC
                    mock_startfile.assert_called_once_with(str(self.resume_file.resolve()))
                    self.assertIn("Opened `Sweekar_Resume.pdf`", f4["content"])
                    self.assertNotIn("can't actually interact with the desktop", f4["content"].lower())
                    actions = f4.get("actions", [])
                    self.assertTrue(len(actions) >= 1)
                    self.assertTrue(actions[0]["command_dispatched"])

    def test_truthful_failure_when_file_not_found(self):
        """
        Verify that nonexistent files produce honest failure without pretending success.
        """
        resp = self.client.post("/api/chat", json={
            "messages": [
                {"role": "user", "content": "Open completely_nonexistent_doc_9999.docx"}
            ]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("could not be found", data["content"].lower())
        self.assertNotIn("Opened `completely_nonexistent_doc_9999.docx`", data["content"])
        action = data["actions"][0]
        self.assertEqual(action["status"], "not_found")
        self.assertFalse(action["file_found"])
        self.assertFalse(action["command_dispatched"])

    def test_truthful_failure_when_startfile_raises_error(self):
        """
        Verify that OS execution errors are truthfully reported and not masked.
        """
        with patch("os.startfile", side_effect=OSError("No application is associated with this file.")):
            resp = self.client.post("/api/chat", json={
                "messages": [
                    {"role": "user", "content": f"Open {str(self.resume_file.resolve())}"}
                ]
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("Failed to open", data["content"])
            action = data["actions"][0]
            self.assertEqual(action["status"], "error")
            self.assertFalse(action["command_dispatched"])

    def test_desktop_session_detection_interactivity(self):
        """
        Verify check_desktop_interactivity detects Windows session details.
        """
        session_info = file_agent.check_desktop_interactivity()
        self.assertIn("is_interactive", session_info)
        self.assertIn("is_session_zero", session_info)
        self.assertIn("session_id", session_info)
        self.assertIn("window_station", session_info)

    def test_emergency_stop_blocks_open(self):
        """
        Verify emergency stop prevents file opening and reports blocked state.
        """
        permission_engine.trigger_emergency_stop()
        resp = self.client.post("/api/chat", json={
            "messages": [
                {"role": "user", "content": f"Open {str(self.resume_file.resolve())}"}
            ]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("Emergency Stop is currently active", data["content"])

if __name__ == "__main__":
    unittest.main()
