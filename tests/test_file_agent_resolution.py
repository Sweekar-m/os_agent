import unittest
import os
import sys
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from intent_router import intent_router, AgentIntent
from agents.file_agent import file_agent, resolve_windows_known_folders
from permission_engine import permission_engine
from main import app
from starlette.testclient import TestClient

class TestFileAgentResolutionAndSearch(unittest.TestCase):
    """
    Regression and integration tests for:
    - Real File Agent execution
    - Windows known folder & OneDrive Desktop resolution
    - Recursive search for .docx files
    - Honest 0-count and positive count reporting
    - Error and permission boundaries
    - Multi-turn conversation handling
    """

    def setUp(self):
        self.client = TestClient(app)
        self.temp_dir = Path(tempfile.mkdtemp(prefix="os_agent_test_"))

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Intent Routing Tests
    # -------------------------------------------------------------------------

    def test_file_search_intent_single_turn(self):
        prompts = [
            "I need to search for a docx file.",
            "search for docx file",
            "search for a docx file",
            "find docx files on desktop",
            "search for .docx files in Downloads",
            "locate file test.py",
            "search desktop for docx"
        ]
        for p in prompts:
            intent = intent_router.classify(p)
            self.assertEqual(
                intent,
                AgentIntent.FILE_OPERATION,
                f"Expected '{p}' to be FILE_OPERATION, got {intent}"
            )

    def test_multi_turn_folder_followup_routing(self):
        """
        User: 'I need to search for a docx file.'
        Assistant: 'I can search your PC. Should I scan your entire system or focus on specific folders?'
        User: 'Go for Desktop folder.'
        """
        history = [
            {"role": "user", "content": "I need to search for a docx file."},
            {"role": "assistant", "content": "I can search your PC. Should I scan your entire system or focus on specific folders?"}
        ]

        followups = [
            "Go for Desktop folder.",
            "Desktop folder",
            "Desktop",
            "go for downloads",
            "Documents folder",
            "search in Desktop"
        ]

        for follow in followups:
            intent = intent_router.classify(follow, chat_history=history)
            self.assertEqual(
                intent,
                AgentIntent.FILE_OPERATION,
                f"Expected follow-up '{follow}' in file search context to be FILE_OPERATION, got {intent}"
            )

    def test_extract_file_search_params(self):
        # Turn 1: query extracted, folder None
        q1, f1 = intent_router.extract_file_search_params("I need to search for a docx file.")
        self.assertEqual(q1, "docx")
        self.assertIsNone(f1)

        # Turn 2: query preserved from history, folder extracted from user message
        history = [
            {"role": "user", "content": "I need to search for a docx file."},
            {"role": "assistant", "content": "I can search your PC. Should I scan your entire system or focus on specific folders?"}
        ]
        q2, f2 = intent_router.extract_file_search_params("Go for Desktop folder.", history)
        self.assertEqual(q2, "docx")
        self.assertEqual(f2, "Desktop folder")

        # Single turn with both
        q3, f3 = intent_router.extract_file_search_params("Search for docx files in Desktop folder")
        self.assertEqual(q3, "docx")
        self.assertEqual(f3, "Desktop folder")

    # -------------------------------------------------------------------------
    # 2. Windows Known Folder Resolution Tests
    # -------------------------------------------------------------------------

    def test_desktop_folder_resolution(self):
        paths = resolve_windows_known_folders("Desktop folder")
        self.assertTrue(len(paths) >= 1, "Expected at least one Desktop path to resolve")
        for p in paths:
            self.assertTrue(p.exists(), f"Resolved path '{p}' should exist on Windows")
            self.assertIn("desktop", str(p).lower())

    def test_downloads_and_documents_resolution(self):
        dl_paths = resolve_windows_known_folders("Downloads folder")
        self.assertTrue(len(dl_paths) >= 1)
        self.assertTrue(dl_paths[0].exists())

        doc_paths = resolve_windows_known_folders("Documents folder")
        self.assertTrue(len(doc_paths) >= 1)
        self.assertTrue(doc_paths[0].exists())

    # -------------------------------------------------------------------------
    # 3. FileAgent Search Execution Tests
    # -------------------------------------------------------------------------

    def test_search_files_successful_match(self):
        # Create nested directory with a real .docx and non-matching files
        sub = self.temp_dir / "reports" / "2026"
        sub.mkdir(parents=True, exist_ok=True)

        target_file = sub / "Quarterly_Financial_Report.docx"
        target_file.write_text("Mock docx binary/xml content", encoding="utf-8")

        other_file = sub / "notes.txt"
        other_file.write_text("plain text", encoding="utf-8")

        res = file_agent.search_files(query="docx", search_dir=str(self.temp_dir))
        self.assertTrue(res["success"])
        self.assertEqual(res["total_matches"], 1)
        self.assertEqual(res["matches"][0]["filename"], "Quarterly_Financial_Report.docx")
        self.assertEqual(res["matches"][0]["path"], str(target_file.resolve()))
        self.assertTrue(res["matches"][0]["size_bytes"] > 0)

    def test_search_files_empty_results_honest_reporting(self):
        # Directory has no .xyz999 files
        res = file_agent.search_files(query="xyz999", search_dir=str(self.temp_dir))
        self.assertTrue(res["success"])
        self.assertEqual(res["total_matches"], 0)
        self.assertEqual(len(res["matches"]), 0)

    def test_search_files_nonexistent_directory(self):
        fake_dir = str(self.temp_dir / "non_existent_folder_xyz")
        res = file_agent.search_files(query="docx", search_dir=fake_dir)
        self.assertFalse(res["success"])
        self.assertIn("does not exist", res["error"])

    def test_search_files_forbidden_system_directory(self):
        res = file_agent.search_files(query="docx", search_dir=r"C:\Windows\System32")
        self.assertFalse(res["success"])
        self.assertIn("prohibited", res["error"].lower())

    # -------------------------------------------------------------------------
    # 4. End-to-End Chat API Integration Tests
    # -------------------------------------------------------------------------

    def test_chat_api_reproduce_and_verify_failure_fixed(self):
        """
        Tests the exact failure scenario from the prompt:
        Turn 1: User says 'I need to search for a docx file.'
        Assistant asks which folder to scan.
        Turn 2: User says 'Go for Desktop folder.'
        Assistant executes real search on Desktop, finds 0 .docx files, and reports honestly.
        """
        # Turn 1
        t1_payload = {
            "messages": [
                {"role": "user", "content": "I need to search for a docx file."}
            ]
        }
        resp1 = self.client.post("/api/chat", json=t1_payload)
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.json()
        self.assertIn("search your pc", data1["content"].lower())
        self.assertNotIn("cannot access", data1["content"].lower())
        self.assertNotIn("windows search", data1["content"].lower())

        # Turn 2
        t2_payload = {
            "messages": [
                {"role": "user", "content": "I need to search for a docx file."},
                {"role": "assistant", "content": data1["content"]},
                {"role": "user", "content": "Go for Desktop folder."}
            ]
        }
        resp2 = self.client.post("/api/chat", json=t2_payload)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()

        # Check honesty and correctness:
        # 1. Never claim filesystem access is unavailable
        self.assertNotIn("cannot access", data2["content"].lower())
        self.assertNotIn("windows search", data2["content"].lower())
        self.assertNotIn("as an ai", data2["content"].lower())

        # 2. Honest reporting for Desktop search
        self.assertIn("desktop", data2["content"].lower())
        # The user's Desktop currently has 0 .docx files
        self.assertIn("no", data2["content"].lower())
        self.assertIn(".docx", data2["content"].lower())

        # 3. Actions trace shows real File Agent search execution
        self.assertTrue(len(data2.get("actions", [])) >= 1)
        action = data2["actions"][0]
        self.assertEqual(action["type"], "file_search")
        self.assertEqual(action["query"], "docx")
        self.assertEqual(action["status"], "success")

    def test_chat_api_search_with_matching_files(self):
        """Verify chat endpoint when matching files exist in a directory."""
        # Create a test folder with a matching docx file
        test_folder = self.temp_dir / "DocxProject"
        test_folder.mkdir(parents=True, exist_ok=True)
        (test_folder / "Thesis_Draft.docx").write_text("Sample Word Doc", encoding="utf-8")

        payload = {
            "messages": [
                {"role": "user", "content": f"Search for docx files in {test_folder}"}
            ]
        }
        resp = self.client.post("/api/chat", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertIn("Thesis_Draft.docx", data["content"])
        self.assertIn("Found 1 matching", data["content"])
        self.assertTrue(len(data["actions"]) >= 1)
        self.assertEqual(data["actions"][0]["total_matches"], 1)

    def test_emergency_stop_blocks_file_operations(self):
        permission_engine.trigger_emergency_stop()
        try:
            payload = {
                "messages": [
                    {"role": "user", "content": "search for docx file in Desktop folder"}
                ]
            }
            resp = self.client.post("/api/chat", json=payload)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("Emergency Stop", data["content"])
        finally:
            permission_engine.clear_emergency_stop()

if __name__ == "__main__":
    unittest.main()
