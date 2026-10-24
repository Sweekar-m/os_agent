import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import tempfile
import shutil
import os
import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from main import app
from intent_router import intent_router, AgentIntent
from agents.browser_agent import browser_agent
from agents.file_agent import file_agent
from agents.system_agent import system_agent
from permission_engine import permission_engine
from orchestrator import orchestrator


class TestRealExecutionArchitecture(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        permission_engine.clear_emergency_stop()
        self.tmp_dir = tempfile.mkdtemp(prefix="test_real_arch_")

    def tearDown(self):
        permission_engine.clear_emergency_stop()
        if os.path.exists(self.tmp_dir):
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # 1. Listing open browser tabs
    def test_01_list_open_browser_tabs(self):
        mock_tabs = [
            {"index": 1, "title": "GitHub - Repository", "url": "https://github.com/project", "is_active": True},
            {"index": 2, "title": "Documentation - Guide", "url": "https://docs.python.org/3/", "is_active": False},
            {"index": 3, "title": "Stack Overflow", "url": "https://stackoverflow.com/questions", "is_active": False}
        ]
        with patch.object(browser_agent, "get_open_tabs", return_value={
            "success": True,
            "browser": "Brave",
            "hwnd": 12345,
            "total_tabs": 3,
            "tabs": mock_tabs,
            "message": "Found 3 open tabs in Brave"
        }):
            resp = self.client.post("/api/chat", json={
                "messages": [{"role": "user", "content": "List all my open browser tabs"}]
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("GitHub - Repository", data["content"])
            self.assertIn("https://github.com/project", data["content"])
            self.assertIn("Documentation - Guide", data["content"])
            self.assertEqual(len(data["actions"]), 1)
            act = data["actions"][0]
            self.assertEqual(act["agent"], "Browser Agent")
            self.assertEqual(act["action"], "list_tabs")
            self.assertEqual(act["status"], "success")
            self.assertEqual(act["verification"]["status"], "verified")

    # 2. Summarizing open browser tabs
    def test_02_summarize_open_browser_tabs(self):
        mock_summaries = [
            {
                "index": 1,
                "title": "Project Dashboard",
                "url": "https://app.example.com/dashboard",
                "accessible": True,
                "summary": "Live metrics dashboard displaying CPU, RAM, and active service health."
            },
            {
                "index": 2,
                "title": "Python Documentation",
                "url": "https://docs.python.org/3/library/asyncio.html",
                "accessible": True,
                "summary": "Official reference guide for asyncio event loops and coroutines."
            }
        ]
        with patch.object(browser_agent, "summarize_open_tabs", new_callable=AsyncMock) as mock_sum:
            mock_sum.return_value = {
                "success": True,
                "browser": "Chrome",
                "hwnd": 54321,
                "total_tabs": 2,
                "summaries": mock_summaries,
                "summary": "1. Project Dashboard: Live metrics\n2. Python Docs: Asyncio reference"
            }
            resp = self.client.post("/api/chat", json={
                "messages": [{"role": "user", "content": "Go through all the open tabs in my browser and give me a short summary of each tab."}]
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("Project Dashboard", data["content"])
            self.assertIn("https://app.example.com/dashboard", data["content"])
            self.assertIn("Live metrics dashboard", data["content"])
            self.assertIn("Python Documentation", data["content"])
            self.assertEqual(len(data["actions"]), 1)
            act = data["actions"][0]
            self.assertEqual(act["agent"], "Browser Agent")
            self.assertEqual(act["action"], "summarize_tabs")
            self.assertTrue(act["success"])
            self.assertEqual(act["verification"]["status"], "verified")
            self.assertEqual(act["verification"]["live_session"], True)

    # 3. Handling inaccessible or restricted tabs honestly
    def test_03_inaccessible_or_restricted_tabs(self):
        mock_summaries = [
            {
                "index": 1,
                "title": "Settings",
                "url": "chrome://settings",
                "accessible": False,
                "summary": "Internal browser page (chrome://settings). Content restricted by browser security policies."
            },
            {
                "index": 2,
                "title": "Web App",
                "url": "https://example.com",
                "accessible": True,
                "summary": "Public webpage showcasing standard web features."
            }
        ]
        with patch.object(browser_agent, "summarize_open_tabs", new_callable=AsyncMock) as mock_sum:
            mock_sum.return_value = {
                "success": True,
                "browser": "Chrome",
                "hwnd": 99999,
                "total_tabs": 2,
                "summaries": mock_summaries,
                "summary": "Tab 1: Internal browser page\nTab 2: Public webpage"
            }
            resp = self.client.post("/api/chat", json={
                "messages": [{"role": "user", "content": "Summarize all open browser tabs for me"}]
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("Internal browser page", data["content"])
            self.assertIn("Content restricted by browser security policies", data["content"])

    # 4. Browser integration disconnection / No browser running
    def test_04_browser_disconnected_or_no_browser(self):
        with patch.object(browser_agent, "summarize_open_tabs", new_callable=AsyncMock) as mock_sum:
            mock_sum.return_value = {
                "success": False,
                "error": "No active browser session (Chrome, Edge, Brave) found running. Please launch or switch to your browser.",
                "total_tabs": 0,
                "summaries": []
            }
            resp = self.client.post("/api/chat", json={
                "messages": [{"role": "user", "content": "Summarize my open browser tabs"}]
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("No active browser session", data["content"])
            self.assertEqual(data["actions"][0]["status"], "error")
            self.assertEqual(data["actions"][0]["verification"]["status"], "failed")

    # 5. Searching for recent documents and opening a selected result
    def test_05_search_recent_documents_and_open_selected(self):
        doc1 = Path(self.tmp_dir) / "Report_Q1.docx"
        doc2 = Path(self.tmp_dir) / "Resume_2026.pdf"
        doc1.write_text("Q1 report content", encoding="utf-8")
        doc2.write_text("Resume details", encoding="utf-8")

        mock_docs = [
            {"id": 1, "filename": "Report_Q1.docx", "path": str(doc1), "modified": "2026-10-22 14:00", "size_display": "17 B"},
            {"id": 2, "filename": "Resume_2026.pdf", "path": str(doc2), "modified": "2026-10-22 15:00", "size_display": "14 B"}
        ]
        file_agent.last_search_results = mock_docs

        with patch("os.startfile") as mock_startfile:
            res = file_agent.open_file("the second one")
            self.assertTrue(res["success"])
            self.assertTrue(res["opened"])
            self.assertEqual(res["filename"], "Resume_2026.pdf")
            mock_startfile.assert_called_once_with(str(doc2.resolve()))

    # 6. Multi-turn action context: "open the second one"
    def test_06_multi_turn_action_context(self):
        doc1 = Path(self.tmp_dir) / "DocA.docx"
        doc2 = Path(self.tmp_dir) / "DocB.pdf"
        doc1.write_text("A", encoding="utf-8")
        doc2.write_text("B", encoding="utf-8")

        file_agent.last_search_results = [
            {"id": 1, "filename": "DocA.docx", "path": str(doc1), "modified": "2026-10-22 12:00", "size_display": "1 B"},
            {"id": 2, "filename": "DocB.pdf", "path": str(doc2), "modified": "2026-10-22 13:00", "size_display": "1 B"}
        ]

        chat_history = [
            {"role": "user", "content": "Find my recent docs"},
            {"role": "assistant", "content": "I found 2 recent documents:\n• 1. `DocA.docx`\n• 2. `DocB.pdf`\nYou can say 'open the second one'."},
            {"role": "user", "content": "open the second one"}
        ]

        # Verify classification
        intent = intent_router.classify("open the second one", chat_history)
        self.assertEqual(intent, AgentIntent.FILE_OPERATION)

        target = intent_router.extract_open_file_target("open the second one", chat_history)
        self.assertEqual(target, "second one")

        with patch("os.startfile") as mock_startfile:
            resp = self.client.post("/api/chat", json={"messages": chat_history})
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("DocB.pdf", data["content"])
            mock_startfile.assert_called_once_with(str(doc2.resolve()))

    # 7. Desktop and Phone-Remote parity
    def test_07_desktop_and_phone_remote_parity(self):
        with patch.object(browser_agent, "summarize_open_tabs", new_callable=AsyncMock) as mock_sum:
            mock_sum.return_value = {
                "success": True,
                "browser": "Edge",
                "hwnd": 11111,
                "total_tabs": 1,
                "summaries": [{"index": 1, "title": "Portal", "url": "https://portal.azure.com", "accessible": True, "summary": "Cloud management portal"}],
                "summary": "1. Portal: Cloud management portal"
            }

            # Desktop Chat invocation
            d_resp = self.client.post("/api/chat", json={
                "messages": [{"role": "user", "content": "summarize open tabs"}]
            })
            self.assertEqual(d_resp.status_code, 200)
            d_json = d_resp.json()

            # Phone Remote WebSocket invocation
            with self.client.websocket_connect("/ws/remote") as ws:
                status_msg = ws.receive_json()
                self.assertEqual(status_msg["type"], "status")

                ws.send_json({"type": "prompt", "text": "summarize open tabs"})
                thinking_msg = None
                remote_resp = None
                for _ in range(25):
                    m = ws.receive_json()
                    if m.get("type") == "agent_thinking":
                        thinking_msg = m
                    elif m.get("type") == "agent_response":
                        remote_resp = m
                        break

                self.assertIsNotNone(thinking_msg, "Did not receive agent_thinking message")
                self.assertIsNotNone(remote_resp, "Did not receive agent_response message")
                self.assertIn("Portal", remote_resp["content"])
                self.assertIn("https://portal.azure.com", remote_resp["content"])
                self.assertEqual(len(remote_resp["actions"]), 1)
                self.assertEqual(remote_resp["actions"][0]["agent"], "Browser Agent")
                self.assertEqual(remote_resp["actions"][0]["action"], "summarize_tabs")

                # Verify parity between desktop and phone responses
                self.assertEqual(d_json["actions"][0]["browser"], remote_resp["actions"][0]["browser"])
                self.assertEqual(d_json["actions"][0]["total_tabs"], remote_resp["actions"][0]["total_tabs"])

    # 8. Tool failures and honest error responses (no fabrication)
    def test_08_tool_failures_and_honest_error_responses(self):
        res = file_agent.open_file("nonexistent_test_document_xyz123.docx")
        self.assertFalse(res["success"])
        self.assertFalse(res["opened"])
        self.assertFalse(res["file_found"])
        self.assertTrue(res["not_found"])
        self.assertIn("could not be found", res["error"])

    # 9. Permission denial and emergency stop
    def test_09_permission_denial_and_emergency_stop(self):
        permission_engine.trigger_emergency_stop()
        self.assertTrue(permission_engine.is_emergency_stopped())

        resp = self.client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "Summarize all open browser tabs"}]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("Emergency Stop is currently active", data["content"])

        file_res = file_agent.open_file("any_file.docx")
        self.assertFalse(file_res["success"])
        self.assertTrue(file_res["permission_denied"])

        permission_engine.clear_emergency_stop()
        self.assertFalse(permission_engine.is_emergency_stopped())

    # 10. Preventing LLM fallback from bypassing available tools
    def test_10_prevent_llm_fallback_bypassing_tools(self):
        test_prompts = [
            ("Go through all the open tabs in my browser and give me a short summary of each tab.", AgentIntent.BROWSER_OPERATION),
            ("summarize my open browser tabs", AgentIntent.BROWSER_OPERATION),
            ("list open tabs", AgentIntent.BROWSER_OPERATION),
            ("Can you check my CPU and RAM usage?", AgentIntent.SYSTEM_OPERATION),
            ("Find my recent documents", AgentIntent.FILE_OPERATION),
            ("Open the document I saved earlier", AgentIntent.FILE_OPERATION),
            ("What's on my screen right now?", AgentIntent.SCREEN_QUESTION),
        ]
        for prompt, expected_intent in test_prompts:
            actual = intent_router.classify(prompt)
            self.assertEqual(actual, expected_intent, f"Prompt '{prompt}' misclassified as {actual}, expected {expected_intent}")


if __name__ == "__main__":
    unittest.main()
