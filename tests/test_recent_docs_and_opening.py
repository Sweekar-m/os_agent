import os
import sys
import time
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add backend directory to sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.file_agent import FileAgent, file_agent, DOCUMENT_EXTENSIONS
from intent_router import IntentRouter, AgentIntent, intent_router
from orchestrator import MultiAgentOrchestrator, orchestrator
from permission_engine import permission_engine
from os_toolkit import os_toolkit
from main import app, handle_file_operation
from starlette.testclient import TestClient

class TestRecentDocsAndOpening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        permission_engine.clear_emergency_stop()
        file_agent.last_search_results = []
        self.temp_dir = tempfile.mkdtemp(prefix="test_recent_docs_")
        self.test_root = Path(self.temp_dir)

    def tearDown(self):
        permission_engine.clear_emergency_stop()
        file_agent.last_search_results = []
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_recent_document_search_returns_actual_metadata(self):
        """1. Recent-document search returns actual temporary test files with accurate metadata."""
        agent = FileAgent(workspace_root=str(self.test_root))
        doc_path = self.test_root / "Project_Summary.docx"
        doc_path.write_text("Project summary content", encoding="utf-8")

        res = agent.find_recent_documents(folders=[str(self.test_root)], limit=10)
        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 1)
        doc = res["documents"][0]
        self.assertEqual(doc["filename"], "Project_Summary.docx")
        self.assertEqual(doc["path"], str(doc_path.resolve()))
        self.assertEqual(doc["extension"], ".docx")
        self.assertGreater(doc["size_bytes"], 0)
        self.assertIn("modified", doc)
        self.assertEqual(doc["id"], 1)

    def test_02_results_sorted_newest_to_oldest(self):
        """2. Results are sorted newest to oldest based on actual st_mtime."""
        agent = FileAgent(workspace_root=str(self.test_root))
        doc_old = self.test_root / "Old_Draft.txt"
        doc_mid = self.test_root / "Mid_Report.pdf"
        doc_new = self.test_root / "New_Summary.docx"

        now = time.time()
        doc_old.write_text("old", encoding="utf-8")
        os.utime(doc_old, (now - 300, now - 300))

        doc_mid.write_text("mid", encoding="utf-8")
        os.utime(doc_mid, (now - 150, now - 150))

        doc_new.write_text("new", encoding="utf-8")
        os.utime(doc_new, (now, now))

        res = agent.find_recent_documents(folders=[str(self.test_root)], limit=10)
        self.assertEqual(res["count"], 3)
        filenames = [d["filename"] for d in res["documents"]]
        self.assertEqual(filenames, ["New_Summary.docx", "Mid_Report.pdf", "Old_Draft.txt"])
        self.assertEqual(res["documents"][0]["id"], 1)
        self.assertEqual(res["documents"][1]["id"], 2)
        self.assertEqual(res["documents"][2]["id"], 3)

    def test_03_unsupported_files_are_excluded(self):
        """3. Unsupported files (.exe, .py, .png, .zip) are excluded from recent document search."""
        agent = FileAgent(workspace_root=str(self.test_root))
        (self.test_root / "program.exe").write_text("binary", encoding="utf-8")
        (self.test_root / "script.py").write_text("print()", encoding="utf-8")
        (self.test_root / "photo.png").write_text("image", encoding="utf-8")
        (self.test_root / "archive.zip").write_text("zip", encoding="utf-8")
        (self.test_root / "Valid_Document.pdf").write_text("pdf content", encoding="utf-8")

        res = agent.find_recent_documents(folders=[str(self.test_root)], limit=10)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["documents"][0]["filename"], "Valid_Document.pdf")

    def test_04_open_docs_preserves_search_context(self):
        """4. 'Open docs' preserves the previous search context and presents real options."""
        doc1 = self.test_root / "Project_Summary.docx"
        doc2 = self.test_root / "Meeting_Notes.txt"
        doc1.write_text("summary", encoding="utf-8")
        doc2.write_text("notes", encoding="utf-8")

        # Step 1: Recent docs search
        file_agent.find_recent_documents(folders=[str(self.test_root)], limit=10)
        self.assertEqual(len(file_agent.last_search_results), 2)

        # Step 2: User says "Open docs"
        resp = self.client.post("/api/chat", json={
            "messages": [
                {"role": "user", "content": "Hey, can you find me the recent docs that I saved?"},
                {"role": "assistant", "content": "Found 2 files"},
                {"role": "user", "content": "Open docs."}
            ]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("Project_Summary.docx", data["content"])
        self.assertIn("Meeting_Notes.txt", data["content"])
        self.assertIn("Which doc", data["content"])

    def test_05_open_the_project_summary_resolves_to_real_search_result(self):
        """5. 'Open the project summary' resolves to a real prior search result."""
        doc = self.test_root / "Project_Summary.docx"
        doc.write_text("summary content", encoding="utf-8")

        file_agent.find_recent_documents(folders=[str(self.test_root)], limit=10)
        self.assertEqual(len(file_agent.last_search_results), 1)

        with patch("os.startfile") as mock_startfile:
            res = file_agent.open_file("Project summary")
            self.assertTrue(res["success"])
            self.assertTrue(res["opened"])
            self.assertEqual(res["filename"], "Project_Summary.docx")
            mock_startfile.assert_called_once_with(str(doc.resolve()))

    def test_06_ambiguous_names_prompt_user_to_select_real_matching_file(self):
        """6. Ambiguous names prompt the user to select between real matching files."""
        doc_docx = self.test_root / "Project_Summary.docx"
        doc_pdf = self.test_root / "Project_Summary.pdf"
        doc_docx.write_text("docx", encoding="utf-8")
        doc_pdf.write_text("pdf", encoding="utf-8")

        file_agent.find_recent_documents(folders=[str(self.test_root)], limit=10)
        self.assertEqual(len(file_agent.last_search_results), 2)

        res = file_agent.open_file("Project summary")
        self.assertFalse(res["success"])
        self.assertTrue(res.get("ambiguous"))
        self.assertIn("Project_Summary.docx", res["candidates"])
        self.assertIn("Project_Summary.pdf", res["candidates"])
        self.assertIn("Multiple files matched", res["message"])

    def test_07_missing_or_stale_paths_fail_honestly(self):
        """7. Missing or stale paths fail honestly without inventing candidates."""
        # Query for non-existent file
        res = file_agent.open_file("Nonexistent_Document_12345.docx")
        self.assertFalse(res["success"])
        self.assertFalse(res["opened"])
        self.assertTrue(res.get("not_found"))
        self.assertIn("could not be found", res["error"])

        # Create file, index it, then delete it to simulate stale path
        stale_file = self.test_root / "Stale_Doc.docx"
        stale_file.write_text("content", encoding="utf-8")
        file_agent.find_recent_documents(folders=[str(self.test_root)], limit=10)
        self.assertEqual(len(file_agent.last_search_results), 1)

        # Delete file from disk
        stale_file.unlink()

        res_stale = file_agent.open_file("Stale_Doc")
        self.assertFalse(res_stale["success"])
        self.assertFalse(res_stale["opened"])
        self.assertTrue(res_stale.get("not_found"))
        self.assertIn("no longer exists on disk", res_stale["error"])

    def test_08_open_operation_is_actually_invoked(self):
        """8. The open operation is actually invoked with os.startfile for the selected file."""
        doc = self.test_root / "Annual_Report.xlsx"
        doc.write_text("excel data", encoding="utf-8")
        file_agent.find_recent_documents(folders=[str(self.test_root)], limit=10)

        with patch("os.startfile") as mock_startfile:
            res = file_agent.open_file(1)
            self.assertTrue(res["success"])
            self.assertTrue(res["opened"])
            mock_startfile.assert_called_once_with(str(doc.resolve()))

    def test_09_permission_denied_files_cannot_be_opened(self):
        """9. Permission-denied files cannot be opened."""
        with patch.object(file_agent, "resolve_safe_path", return_value=(False, Path("C:/Windows/System32/cmd.exe"), "Access to system critical path is strictly prohibited.")):
            res = file_agent.open_file("C:/Windows/System32/cmd.exe")
            self.assertFalse(res["success"])
            self.assertFalse(res["opened"])
            self.assertTrue(res.get("permission_denied"))

    def test_10_assistant_never_claims_success_after_failed_open(self):
        """10. The assistant never claims success after a failed open (e.g. OSError from OS)."""
        doc = self.test_root / "Corrupt_Doc.docx"
        doc.write_text("data", encoding="utf-8")
        file_agent.find_recent_documents(folders=[str(self.test_root)], limit=10)

        with patch("os.startfile", side_effect=OSError("No application is associated with the specified file for this operation.")):
            res = file_agent.open_file("Corrupt_Doc.docx")
            self.assertFalse(res["success"])
            self.assertFalse(res["opened"])
            self.assertIn("Failed to open", res["error"])

    def test_11_emergency_stop_blocks_file_opening(self):
        """11. Emergency stop blocks opening files."""
        doc = self.test_root / "Safe_Doc.docx"
        doc.write_text("data", encoding="utf-8")
        file_agent.find_recent_documents(folders=[str(self.test_root)], limit=10)

        # Trigger emergency stop
        permission_engine.trigger_emergency_stop()
        self.assertTrue(permission_engine.is_emergency_stopped())

        with patch("os.startfile") as mock_startfile:
            res = file_agent.open_file("Safe_Doc.docx")
            self.assertFalse(res["success"])
            self.assertFalse(res["opened"])
            self.assertTrue(res.get("permission_denied"))
            mock_startfile.assert_not_called()

    def test_12_full_conversation_reproduction_flow(self):
        """12. Complete 3-turn workflow reproduction:
        User: 'Hey, can you find me the recent docs that I saved?'
        Assistant: returns real documents
        User: 'Open docs.'
        Assistant: prompts with real options
        User: 'Project summary.'
        Assistant: opens real file and confirms!
        """
        doc = self.test_root / "Project_Summary.docx"
        doc.write_text("Verified project summary", encoding="utf-8")

        # Mock resolve_windows_known_folders to include our temporary directory
        with patch("agents.file_agent.resolve_windows_known_folders", return_value=[self.test_root]):
            with patch("os.startfile") as mock_startfile:
                # Turn 1: Find recent docs
                resp1 = self.client.post("/api/chat", json={
                    "messages": [
                        {"role": "user", "content": "Hey, can you find me the recent docs that I saved?"}
                    ]
                })
                self.assertEqual(resp1.status_code, 200)
                d1 = resp1.json()
                self.assertIn("Project_Summary.docx", d1["content"])
                self.assertIn("recent document", d1["spoken_summary"])

                # Turn 2: Open docs
                resp2 = self.client.post("/api/chat", json={
                    "messages": [
                        {"role": "user", "content": "Hey, can you find me the recent docs that I saved?"},
                        {"role": "assistant", "content": d1["content"]},
                        {"role": "user", "content": "Open docs."}
                    ]
                })
                self.assertEqual(resp2.status_code, 200)
                d2 = resp2.json()
                self.assertIn("Which doc would you like me to open?", d2["content"])
                self.assertIn("Project_Summary.docx", d2["content"])

                # Turn 3: Project summary.
                resp3 = self.client.post("/api/chat", json={
                    "messages": [
                        {"role": "user", "content": "Hey, can you find me the recent docs that I saved?"},
                        {"role": "assistant", "content": d1["content"]},
                        {"role": "user", "content": "Open docs."},
                        {"role": "assistant", "content": d2["content"]},
                        {"role": "user", "content": "Project summary."}
                    ]
                })
                self.assertEqual(resp3.status_code, 200)
                d3 = resp3.json()
                self.assertIn("Opened `Project_Summary.docx`", d3["content"])
                self.assertIn("Opening your Project Summary now", d3["spoken_summary"])
                mock_startfile.assert_called_once_with(str(doc.resolve()))

    def test_13_os_toolkit_honest_failure_on_nonexistent(self):
        """13. OSToolkit does not falsely report success when launching nonexistent files or apps."""
        res = os_toolkit.open_app_or_file("nonexistent_fake_app_12345")
        self.assertFalse(res["success"])
        self.assertIn("does not exist", res["error"])

if __name__ == "__main__":
    unittest.main()
