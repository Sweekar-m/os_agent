import unittest
import asyncio
from pathlib import Path
import sys

# Ensure backend directory is in path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from os_toolkit import os_toolkit
from llm_client import nim_client

class TestOSAgentCapabilities(unittest.TestCase):

    def test_system_info_telemetry(self):
        info = os_toolkit.get_system_info()
        self.assertIn("cpu", info)
        self.assertIn("memory", info)
        self.assertIn("disks", info)
        self.assertGreaterEqual(info["cpu"]["percent"], 0)
        self.assertGreater(info["memory"]["total_gb"], 0)
        self.assertTrue(len(info["disks"]) > 0)
        print("✓ Real OS Telemetry test passed:", info["cpu"]["percent"], "% CPU,", info["memory"]["used_gb"], "GB RAM used")

    def test_process_inspection(self):
        procs = os_toolkit.get_running_processes(limit=5)
        self.assertIsInstance(procs, list)
        self.assertTrue(len(procs) > 0)
        p0 = procs[0]
        self.assertIn("pid", p0)
        self.assertIn("name", p0)
        self.assertIn("memory_mb", p0)
        self.assertGreater(p0["memory_mb"], 0)
        print("✓ Process Inspection test passed. Top process:", p0["name"], f"({p0['memory_mb']} MB)")

    def test_file_read_write(self):
        test_file = BACKEND_DIR / "test_scratch_file.txt"
        test_content = "Hello from Antigravity OS Agent test\nLine 2: Zero hallucination"
        write_res = os_toolkit.write_file(str(test_file), test_content, overwrite=True)
        self.assertTrue(write_res.get("success"))

        read_res = os_toolkit.read_file(str(test_file))
        self.assertEqual(read_res.get("total_lines"), 2)
        self.assertIn("Zero hallucination", read_res.get("content"))

        # Clean up
        if test_file.exists():
            test_file.unlink()
        print("✓ File Read/Write test passed.")

    def test_powershell_execution(self):
        res = os_toolkit.run_command("Write-Output 'OS Agent Active'", shell_type="powershell")
        self.assertEqual(res.get("exit_code"), 0)
        self.assertIn("OS Agent Active", res.get("stdout"))
        print("✓ PowerShell Execution test passed.")

    def test_detect_os_intents(self):
        t1 = nim_client.detect_os_intent("check my ram and cpu")
        self.assertIsNotNone(t1)
        self.assertEqual(t1.get("type"), "os_telemetry")

        t2 = nim_client.detect_os_intent("what processes are running on my PC?")
        self.assertIsNotNone(t2)
        self.assertEqual(t2.get("type"), "os_processes")

        t3 = nim_client.detect_os_intent("how much disk space is left?")
        self.assertIsNotNone(t3)
        self.assertEqual(t3.get("type"), "os_disks")

        t4 = nim_client.detect_os_intent("what is in my clipboard?")
        self.assertIsNotNone(t4)
        self.assertEqual(t4.get("type"), "os_clipboard")
        print("✓ Direct OS Intent Detection test passed.")

    def test_execute_os_actions(self):
        async def run_actions():
            sample_action_content = '```action\n[{"type": "system_info"}, {"type": "shell", "command": "echo test_agent"}]\n```'
            executed = await nim_client.execute_parsed_actions(sample_action_content)
            self.assertEqual(len(executed), 2)
            self.assertIn("system_info", executed[0])
            self.assertEqual(executed[1]["exit_code"], 0)
            self.assertIn("test_agent", executed[1]["stdout"])

        asyncio.run(run_actions())
        print("✓ Parse & Execute OS Actions test passed.")

    def test_conversational_intents(self):
        # 1. Greetings
        for greeting in ["HEY", "hey", "hi", "hello", "good morning", "yo", "sup", "heyy"]:
            res = nim_client.detect_conversational_intent(greeting)
            self.assertIsNotNone(res, f"Greeting '{greeting}' should be detected as conversational")
            self.assertEqual(res["type"], "conversation")
            self.assertEqual(len(res["actions"]), 0)
            self.assertIn("Hey", res["content"])

        # 2. Status inquiry
        res_status = nim_client.detect_conversational_intent("how are you doing?")
        self.assertIsNotNone(res_status)
        self.assertEqual(res_status["type"], "conversation")
        self.assertEqual(len(res_status["actions"]), 0)

        # 3. Identity inquiry
        res_who = nim_client.detect_conversational_intent("who are you")
        self.assertIsNotNone(res_who)
        self.assertEqual(res_who["type"], "conversation")
        self.assertEqual(len(res_who["actions"]), 0)

        # 4. Gratitude & Bye
        res_thanks = nim_client.detect_conversational_intent("thank you")
        self.assertIsNotNone(res_thanks)
        res_bye = nim_client.detect_conversational_intent("bye")
        self.assertIsNotNone(res_bye)

        # 5. Must NOT intercept explicit OS/PPT commands
        self.assertIsNone(nim_client.detect_conversational_intent("hey check my cpu usage"))
        self.assertIsNone(nim_client.detect_conversational_intent("create a ppt on project_a"))
        self.assertIsNone(nim_client.detect_conversational_intent("kill process chrome"))
        print("✓ Conversational Intent Detection & Task Separation test passed.")

    def test_chat_complete_greeting(self):
        async def run_chat():
            res = await nim_client.chat_complete([{"role": "user", "content": "HEY"}])
            self.assertEqual(len(res.get("actions", [])), 0, "HEY must NOT produce actions or telemetry")
            self.assertNotIn("CPU Usage", res.get("content", ""))
            self.assertNotIn("system_info", res.get("content", ""))
            self.assertIn("Hey", res.get("content", ""))

        asyncio.run(run_chat())
        print("✓ Chat Complete Greeting test passed (zero actions, friendly chat).")

if __name__ == "__main__":
    unittest.main()
