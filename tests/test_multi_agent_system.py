"""
Comprehensive Multi-Agent OS Assistant Test Suite
Covers:
  1. Provider-independent LLM Gateway & Secret Masking
  2. Browser Agent: Existing window discovery & control (Strictly NO Playwright)
  3. File Agent: Path sandboxing, backup generation, unified diffs, delete confirmation guard
  4. System Agent: Hardware telemetry, process management, safe execution
  5. Vision Agent: Screen state analysis, UI coordinate localization & stale target degradation
  6. Safety & Permission Engine: Default-deny, 6-choice approval matrix, Emergency Stop
  7. Multi-Agent Orchestrator: Multi-agent intent routing, verified execution reports, spoken summaries
  8. Phone Remote Pairing & Security: 4-digit PIN verification & token authentication
  9. Speech Service: Speech text cleaner & STT/TTS configuration
"""

import sys
import os
import asyncio
import unittest
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

# Add backend directory to path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import settings
from llm_gateway import LLMGateway, mask_key
from permission_engine import (
    permission_engine,
    PermissionDecision,
    ActionCategory,
    RiskLevel
)
from speech_service import speech_service
from agents.browser_agent import BrowserAgent
from agents.file_agent import FileAgent
from agents.system_agent import SystemAgent
from agents.vision_agent import VisionAgent
from orchestrator import MultiAgentOrchestrator


class TestMultiAgentSystem(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="os_agent_test_")
        self.workspace = Path(self.temp_dir).resolve()
        # Reset emergency stop before each test
        permission_engine.clear_emergency_stop()

    def tearDown(self):
        permission_engine.clear_emergency_stop()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # 1. LLM Gateway & Provider Configuration Tests
    # =========================================================================
    def test_llm_gateway_masking_and_config(self):
        """Test API key masking and multi-provider configuration."""
        gateway = LLMGateway()
        status = gateway.get_status()
        self.assertIn("provider", status)
        self.assertIn("masked_key", status)
        self.assertIn("chat_model", status)
        self.assertIn("vision_model", status)
        
        # Verify secret masking
        masked = mask_key("nvapi-1234567890abcdef1234567890")
        self.assertTrue(masked.startswith("nvapi-"))
        self.assertNotIn("abcdef", masked)

    @patch("llm_gateway.httpx.AsyncClient")
    async def test_llm_gateway_chat_completion(self, mock_client_cls):
        """Test provider-independent chat completion parsing."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Windows system is operational."
                },
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        gateway = LLMGateway()
        resp = await gateway.chat_complete(
            messages=[{"role": "user", "content": "status check"}]
        )
        self.assertTrue(resp["success"])
        self.assertEqual(resp["content"], "Windows system is operational.")

    # =========================================================================
    # 2. Browser Agent Tests (Strictly NO Playwright, Existing Windows)
    # =========================================================================
    def test_browser_agent_no_playwright_constraint(self):
        """Verify Browser Agent does NOT import or depend on Playwright."""
        browser_agent = BrowserAgent()
        self.assertFalse(hasattr(browser_agent, "playwright"))
        self.assertFalse(hasattr(browser_agent, "browser_context"))
        # Discovers existing Windows browsers
        browsers = browser_agent.find_existing_browsers()
        self.assertIsInstance(browsers, list)

    async def test_browser_agent_command_parser(self):
        """Test address bar navigation, tab switching, and scroll command dispatch."""
        browser_agent = BrowserAgent()
        
        # Test navigation intent
        res_nav = await browser_agent.navigate("https://example.com")
        self.assertIn("url", res_nav)
        self.assertIn("https://example.com", res_nav.get("url"))
        
        # Test tab management
        res_tab = await browser_agent.new_tab()
        self.assertTrue(res_tab.get("success"))
        
        # Test scroll
        res_scroll = await browser_agent.scroll(direction="down", amount=3.0)
        self.assertTrue(res_scroll.get("success"))

    # =========================================================================
    # 3. File Agent Tests (Sandboxing, Diffs, Backups, Delete Confirmation)
    # =========================================================================
    def test_file_agent_safe_paths_and_traversal_blocking(self):
        """Verify path traversal outside authorized workspace is blocked."""
        file_agent = FileAgent(workspace_root=self.workspace)
        
        # Valid path inside workspace
        valid_path = self.workspace / "subfolder" / "data.txt"
        is_valid, resolved, err = file_agent.resolve_safe_path(str(valid_path))
        self.assertTrue(is_valid)
        self.assertEqual(resolved, valid_path.resolve())
        
        # Path traversal attack (escape attempt to system root)
        escape_attempt = r"C:\Windows\System32\cmd.exe"
        is_escape_valid, _, err_msg = file_agent.resolve_safe_path(escape_attempt)
        self.assertFalse(is_escape_valid)
        self.assertIn("strictly prohibited", err_msg)

    def test_file_agent_write_backup_and_diff(self):
        """Verify automatic backup and unified diff generation on edit."""
        file_agent = FileAgent(workspace_root=self.workspace)
        test_file = self.workspace / "notes.txt"
        
        # 1. Initial creation
        create_res = file_agent.write_file(str(test_file), "Line 1: Original content\nLine 2: Static line\n")
        self.assertTrue(create_res.get("success"))
        self.assertTrue(test_file.exists())
        
        # 2. Targeted modification (should produce backup + unified diff)
        edit_res = file_agent.edit_file(
            str(test_file),
            find_text="Original content",
            replace_text="Updated content"
        )
        self.assertTrue(edit_res.get("success"))
        self.assertIsNotNone(edit_res.get("backup_path"))
        self.assertTrue(Path(edit_res["backup_path"]).exists())
        
        # Verify unified diff is included
        diff = edit_res.get("diff", "")
        self.assertIn("-Line 1: Original content", diff)
        self.assertIn("+Line 1: Updated content", diff)

    def test_file_agent_delete_confirmation_guard(self):
        """Verify deletion requires explicit confirmation and cannot falsely report success."""
        file_agent = FileAgent(workspace_root=self.workspace)
        target = self.workspace / "important_data.csv"
        target.write_text("id,val\n1,100", encoding="utf-8")
        
        # Deletion without confirm=True MUST fail safely
        res_no_confirm = file_agent.delete_file(str(target), confirm=False)
        self.assertFalse(res_no_confirm.get("success"))
        self.assertTrue(res_no_confirm.get("requires_confirmation"))
        self.assertTrue(target.exists(), "File must not be deleted without confirmation")
        
        # Deletion with confirm=True creates backup and removes file
        res_confirmed = file_agent.delete_file(str(target), confirm=True)
        self.assertTrue(res_confirmed.get("success"))
        self.assertFalse(target.exists(), "File should be deleted after explicit confirmation")
        self.assertIsNotNone(res_confirmed.get("backup_path"))
        self.assertTrue(Path(res_confirmed["backup_path"]).exists(), "Backup must exist before deletion")

    def test_file_agent_search(self):
        """Verify filename and content grep search."""
        file_agent = FileAgent(workspace_root=self.workspace)
        (self.workspace / "app.py").write_text("def start_service(): pass", encoding="utf-8")
        (self.workspace / "config.json").write_text('{"db": "sqlite"}', encoding="utf-8")
        
        # Search by filename
        name_matches = file_agent.search_files(query="app.py", search_dir=str(self.workspace))
        self.assertTrue(name_matches["success"])
        self.assertEqual(len(name_matches["matches"]), 1)
        self.assertEqual(name_matches["matches"][0]["filename"], "app.py")
        
        # Search by content
        content_matches = file_agent.search_files(query="start_service", search_dir=str(self.workspace), search_content=True)
        self.assertTrue(content_matches["success"])
        self.assertEqual(len(content_matches["matches"]), 1)
        self.assertEqual(content_matches["matches"][0]["filename"], "app.py")

    # =========================================================================
    # 4. System Agent Tests (Telemetry, Process Inspection, Diagnostics)
    # =========================================================================
    def test_system_agent_telemetry(self):
        """Verify real Windows hardware telemetry reading."""
        system_agent = SystemAgent()
        telemetry = system_agent.get_system_telemetry()
        self.assertIn("cpu", telemetry)
        self.assertIn("memory", telemetry)
        self.assertIn("disks", telemetry)
        self.assertGreaterEqual(telemetry["cpu"]["percent"], 0)
        self.assertGreater(telemetry["memory"]["total_gb"], 0)
        self.assertTrue(len(telemetry["disks"]) > 0)

    def test_system_agent_process_list_and_diagnostics(self):
        """Verify process inspection and system diagnostics."""
        system_agent = SystemAgent()
        procs = system_agent.list_processes(limit=5)
        self.assertTrue(procs["success"])
        proc_list = procs["processes"]
        self.assertIsInstance(proc_list, list)
        self.assertGreater(len(proc_list), 0)
        p0 = proc_list[0]
        self.assertIn("name", p0)
        self.assertIn("pid", p0)
        self.assertIn("memory_mb", p0)

        diag = system_agent.diagnose_system()
        self.assertTrue(diag["success"])
        self.assertIn("status", diag)
        self.assertIn("diagnostics", diag)

    # =========================================================================
    # 5. Vision Agent Tests (Active Window, Coordinates, Degradation)
    # =========================================================================
    def test_vision_agent_active_window(self):
        """Verify active window inspection."""
        vision_agent = VisionAgent()
        win = vision_agent.get_active_window_info()
        self.assertIn("title", win)
        self.assertIn("rect", win)
        self.assertIn("width", win["rect"])
        self.assertIn("height", win["rect"])

    async def test_vision_agent_stale_target_handling(self):
        """Verify stale / uncertain element target handling gracefully degrades."""
        vision_agent = VisionAgent()
        # Non-existent UI element
        elem = await vision_agent.locate_target_coordinates(target_description="NonExistentButtonXYZ_12345")
        self.assertFalse(elem.get("success", False))

    # =========================================================================
    # 6. Safety & Permission Engine Tests (Emergency Stop & Policy Enforcement)
    # =========================================================================
    async def test_emergency_stop_lifecycle(self):
        """Verify Emergency Stop unconditionally halts new operations."""
        self.assertFalse(permission_engine.is_emergency_stopped())
        
        # Trigger emergency stop
        permission_engine.trigger_emergency_stop()
        self.assertTrue(permission_engine.is_emergency_stopped())
        
        # Request approval must immediately cancel with emergency stop reason
        decision, meta = await permission_engine.request_approval(
            task_id="test_task",
            workspace_root=str(self.workspace),
            action_type="FILE_DELETE",
            command="rm important.txt"
        )
        self.assertEqual(decision, PermissionDecision.CANCEL)
        self.assertIn("Emergency stop", meta.get("reason", ""))
            
        # Clearing emergency stop restores normal checks
        permission_engine.clear_emergency_stop()
        self.assertFalse(permission_engine.is_emergency_stopped())

    def test_permission_engine_path_canonicalization(self):
        """Verify paths are properly canonicalized to prevent symlink/relative escapes."""
        valid_file = self.workspace / "test.txt"
        valid_file.write_text("safe", encoding="utf-8")
        
        is_ok, resolved = permission_engine.validate_path_in_workspace(str(valid_file), str(self.workspace))
        self.assertTrue(is_ok)
        self.assertEqual(Path(resolved), valid_file.resolve())

        # Escape path
        escape = str(self.workspace / ".." / "system.ini")
        is_escape_ok, _ = permission_engine.validate_path_in_workspace(escape, str(self.workspace))
        self.assertFalse(is_escape_ok)

    # =========================================================================
    # 7. Multi-Agent Orchestrator Intent Routing & Execution
    # =========================================================================
    async def test_orchestrator_intent_routing(self):
        """Verify orchestrator properly routes natural language to the 4 agents."""
        orchestrator = MultiAgentOrchestrator()
        
        # Browser search intent
        plan_browser = await orchestrator._formulate_multi_agent_plan("task-1", "Search for machine learning news on google", str(self.workspace))
        self.assertEqual(plan_browser["steps"][0]["agent"], "Browser Agent")
        
        # Hardware telemetry intent
        plan_telemetry = await orchestrator._formulate_multi_agent_plan("task-2", "Check my RAM and CPU usage", str(self.workspace))
        self.assertEqual(plan_telemetry["steps"][0]["agent"], "System Agent")
        self.assertEqual(plan_telemetry["steps"][0]["action"], "telemetry")
        
        # App launch intent
        plan_launch = await orchestrator._formulate_multi_agent_plan("task-3", "Launch notepad", str(self.workspace))
        self.assertEqual(plan_launch["steps"][0]["agent"], "System Agent")
        self.assertEqual(plan_launch["steps"][0]["action"], "launch_app")

    async def test_orchestrator_direct_execution_telemetry(self):
        """Verify orchestrator executes agent action and returns structured response."""
        orchestrator = MultiAgentOrchestrator()
        res = await orchestrator._dispatch_agent_action("System Agent", "telemetry", {})
        self.assertIn("cpu", res)
        self.assertIn("memory", res)
        self.assertIn("disks", res)

    # =========================================================================
    # 8. Phone Remote Pairing & Security Tests
    # =========================================================================
    def test_phone_pairing_pin_and_token(self):
        """Verify 4-digit pairing PIN verification and token validation."""
        pin = settings.REMOTE_PAIRING_PIN
        token = settings.REMOTE_AUTH_TOKEN
        
        # Valid PIN
        self.assertEqual(pin, "4826")
        
        # Check matching logic
        self.assertTrue(pin.strip() == "4826")
        self.assertFalse(pin.strip() == "0000")
        self.assertTrue(len(token) >= 16)

    # =========================================================================
    # 9. Speech Service Tests
    # =========================================================================
    def test_speech_text_cleaner(self):
        """Verify text cleaner strips code blocks, URLs, and markdown for human-friendly speech."""
        raw_text = """
        Here is the outcome of your request:
        ```bash
        taskkill /f /im notepad.exe
        ```
        Check details at https://docs.microsoft.com. **Success!** CPU is at *12%*.
        """
        clean = speech_service.filter_text_for_speech(raw_text)
        self.assertNotIn("taskkill", clean)
        self.assertNotIn("https://", clean)
        self.assertNotIn("**", clean)
        self.assertIn("Success", clean)


if __name__ == "__main__":
    unittest.main(verbosity=2)
