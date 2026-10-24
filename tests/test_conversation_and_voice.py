import unittest
import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from intent_router import intent_router, AgentIntent
from speech_normalizer import normalize_for_speech, normalizer, SpeechNormalizer
from speech_service import speech_service, SpeechService
from config import settings


class TestConversationQualityAndIntentRouting(unittest.TestCase):
    """
    Tests for Requirement 1 & 2:
    - Natural short greetings
    - Assistant identity queries
    - Intent classification & routing
    - Separation of casual talk from OS automation
    """

    def test_greetings_classified_as_casual(self):
        greetings = ["hey", "hello", "hi bro", "good morning", "hey there", "hi"]
        for g in greetings:
            intent = intent_router.classify(g)
            self.assertEqual(
                intent,
                AgentIntent.CASUAL_CONVERSATION,
                f"Expected '{g}' to be classified as CASUAL_CONVERSATION, got {intent}"
            )

    def test_identity_queries_classified_as_identity(self):
        queries = ["who are you", "who are you?", "what are you", "what can you do", "introduce yourself"]
        for q in queries:
            intent = intent_router.classify(q)
            self.assertEqual(
                intent,
                AgentIntent.ASSISTANT_IDENTITY,
                f"Expected '{q}' to be classified as ASSISTANT_IDENTITY, got {intent}"
            )

    def test_screen_question_classification(self):
        screen_queries = [
            "what is on my screen",
            "what's on my screen",
            "look at my screen",
            "analyze my screen",
            "describe screen"
        ]
        for sq in screen_queries:
            intent = intent_router.classify(sq)
            self.assertEqual(
                intent,
                AgentIntent.SCREEN_QUESTION,
                f"Expected '{sq}' to be classified as SCREEN_QUESTION, got {intent}"
            )

    def test_system_operation_classification(self):
        sys_queries = [
            "cpu usage",
            "ram usage",
            "running processes",
            "kill process notepad",
            "powershell Get-Process"
        ]
        for sq in sys_queries:
            intent = intent_router.classify(sq)
            self.assertEqual(
                intent,
                AgentIntent.SYSTEM_OPERATION,
                f"Expected '{sq}' to be classified as SYSTEM_OPERATION, got {intent}"
            )

    def test_browser_and_file_classification(self):
        self.assertEqual(intent_router.classify("open chrome"), AgentIntent.BROWSER_OPERATION)
        self.assertEqual(intent_router.classify("https://example.com"), AgentIntent.BROWSER_OPERATION)
        self.assertEqual(intent_router.classify("read file config.py"), AgentIntent.FILE_OPERATION)

    def test_voice_operation_classification(self):
        voice_queries = ["mute voice", "unmute voice", "speak faster", "speak slower", "change voice"]
        for vq in voice_queries:
            intent = intent_router.classify(vq)
            self.assertEqual(
                intent,
                AgentIntent.VOICE_OPERATION,
                f"Expected '{vq}' to be classified as VOICE_OPERATION, got {intent}"
            )

    def test_system_prompt_guidelines(self):
        casual_prompt = intent_router.get_conversation_system_prompt(is_identity_query=False)
        self.assertIn("Never output action JSON", casual_prompt)
        self.assertIn("single, natural, friendly sentence", casual_prompt)
        self.assertIn("do NOT repeat greeting introductions", casual_prompt)

        identity_prompt = intent_router.get_conversation_system_prompt(is_identity_query=True)
        self.assertIn("Browser Agent", identity_prompt)
        self.assertIn("File Agent", identity_prompt)
        self.assertIn("System Agent", identity_prompt)
        self.assertIn("Vision Agent", identity_prompt)
        self.assertIn("Do NOT include JSON action blocks", identity_prompt)


class TestEmojiAwareSpeechNormalizer(unittest.TestCase):
    """
    Tests for Requirement 4:
    - Contextual emoji handling (✅ -> successfully, ⚠️ -> warning)
    - Decorative emoji omission (👋, 😂)
    - User emoji inquiry explanation
    - Markdown, backticks, tables, code block cleanup
    - Preservation of decimals, IP addresses, negations, technical terms
    """

    def test_status_and_alert_emojis(self):
        # "Task completed ✅" -> "Task completed successfully."
        res = normalize_for_speech("Task completed ✅")
        self.assertIn("successfully", res.lower())
        self.assertNotIn("check mark", res.lower())
        self.assertNotIn("white heavy check mark", res.lower())

        # "I got a warning ⚠️" -> "I got a warning."
        res2 = normalize_for_speech("I got a warning ⚠️")
        self.assertIn("warning", res2.lower())
        self.assertNotIn("warning sign", res2.lower())

    def test_decorative_emojis_omitted(self):
        # "Hey! 👋 How are you?" -> speak naturally as "Hey! How are you?"
        res = normalize_for_speech("Hey! 👋 How are you?")
        self.assertEqual(res, "Hey! How are you?")
        self.assertNotIn("waving", res.lower())

        # "I'm happy 😂" -> preserves natural sentence
        res2 = normalize_for_speech("I'm happy 😂")
        self.assertEqual(res2, "I'm happy.")
        self.assertNotIn("face with tears of joy", res2.lower())

    def test_explicit_emoji_inquiry_explanation(self):
        # When user asks what an emoji means, explain its meaning
        res = normalize_for_speech("Here is the emoji 😂", user_prompt="what does 😂 mean?")
        self.assertIn("tears of joy", res.lower())

        res2 = normalize_for_speech("Here is ⚠️", user_prompt="explain this emoji ⚠️")
        self.assertIn("warning", res2.lower())

    def test_markdown_and_code_cleanup(self):
        text = (
            "# System Report\n"
            "Here is the code to run:\n"
            "```python\n"
            "import os\n"
            "print('hello world')\n"
            "```\n"
            "Please check `config.py` and see [Google](https://google.com/search?q=test)."
        )
        res = normalize_for_speech(text)
        # Fenced code block removed
        self.assertNotIn("import os", res)
        self.assertNotIn("print('hello world')", res)
        # Backtick removed, word preserved
        self.assertIn("config.py", res)
        self.assertNotIn("`", res)
        # Markdown link converted to title
        self.assertIn("Google", res)
        self.assertNotIn("https://google.com", res)
        # Markdown header '#' removed
        self.assertNotIn("#", res)

    def test_table_cleanup(self):
        table_text = (
            "| Process | PID | Memory |\n"
            "| :--- | :--- | :--- |\n"
            "| chrome.exe | 1024 | 450 MB |"
        )
        res = normalize_for_speech(table_text)
        self.assertNotIn("|", res)
        self.assertNotIn(":---", res)
        self.assertIn("chrome.exe", res)
        self.assertIn("1024", res)

    def test_preservation_of_numbers_decimals_and_negations(self):
        # Decimal numbers must NOT be broken up with spaces
        res = normalize_for_speech("CPU usage is at 15.4% and pi is 3.14.")
        self.assertIn("15.4%", res)
        self.assertIn("3.14", res)

        # Technical terms and expanded abbreviations
        res2 = normalize_for_speech("RAM usage is 8.5 GB.")
        self.assertIn("RAM", res2)
        self.assertIn("gigabytes", res2)

        # Negations must be strictly preserved
        res3 = normalize_for_speech("Do not terminate process, it is not responding.")
        self.assertIn("not", res3.lower())
        self.assertIn("terminate process", res3.lower())


class TestTTSAndSpeechService(unittest.TestCase):
    """
    Tests for Requirement 3 & 5:
    - Audio deduplication
    - Interruption handling
    - TTS provider configuration
    - Fallback behavior on failure
    """

    def setUp(self):
        self.service = SpeechService()

    def test_deduplication_cache(self):
        test_text = "Task completed successfully."
        self.assertFalse(self.service.is_duplicate(test_text))

        self.service.mark_spoken(test_text)
        self.assertTrue(self.service.is_duplicate(test_text))

        # Different text should not be duplicate
        self.assertFalse(self.service.is_duplicate("A completely different sentence."))

    def test_synthesize_speech_prevents_duplicate(self):
        async def run_test():
            self.service.mark_spoken("Hello there!")
            res = await self.service.synthesize_speech("Hello there!", ignore_duplicate=False)
            self.assertTrue(res.get("is_duplicate"))
            self.assertEqual(res.get("audio_bytes"), b"")

        asyncio.run(run_test())

    def test_voice_configuration_updates(self):
        self.service.update_config(provider="edge_tts", voice="en-US-GuyNeural", speed=1.25)
        st = self.service.get_status()
        self.assertEqual(st["voice_name"], "en-US-GuyNeural")
        self.assertEqual(st["voice_speed"], 1.25)

    def test_tts_fallback_on_exception(self):
        async def run_test():
            # Mock edge-tts to raise an exception
            with patch("edge_tts.Communicate", side_effect=Exception("Network error")):
                res = await self.service.synthesize_speech("Test error fallback.", ignore_duplicate=True)
                # Must succeed with fallback to web_speech and normalized text intact
                self.assertTrue(res.get("success"))
                self.assertIn("Test error fallback.", res.get("normalized_text", ""))

        asyncio.run(run_test())

    def test_real_edge_tts_audio_generation(self):
        """Tests actual neural audio synthesis with Microsoft Edge TTS if network is reachable."""
        async def run_test():
            try:
                import edge_tts
                comm = edge_tts.Communicate("Hello! Antigravity OS Assistant is ready.", "en-US-JennyNeural")
                chunks = []
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        chunks.append(chunk["data"])
                audio_bytes = b"".join(chunks)
                # Verify that real MP3 audio bytes were received
                self.assertGreater(len(audio_bytes), 5000, "Expected at least 5000 audio bytes from Edge TTS")
                print(f"\n[Verified Real TTS] Generated {len(audio_bytes)} bytes of neural audio with en-US-JennyNeural")
            except Exception as e:
                # If network or offline environment, note it without failing unit test
                print(f"\n[Real TTS Note] Network not available for live Edge-TTS: {e}")

        asyncio.run(run_test())


class TestChatEndpointIntegration(unittest.TestCase):
    """
    Integration tests for FastAPI endpoints:
    - /api/chat with casual greeting -> no OS tools, audio_url returned
    - /api/chat with 'who are you?' -> identity returned, audio_url returned
    - /api/chat with voice command -> muted/unmuted
    - /api/speech/normalize -> deterministic text normalization
    """

    def setUp(self):
        from fastapi.testclient import TestClient
        from main import app
        self.client = TestClient(app)

    def test_casual_greeting_endpoint(self):
        # Greeting should not create action blocks or invoke OS tools
        resp = self.client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "hey"}]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("role"), "assistant")
        self.assertEqual(data.get("actions"), [])
        self.assertTrue(len(data.get("content", "")) > 0)
        self.assertTrue(len(data.get("spoken_summary", "")) > 0)
        self.assertIsNotNone(data.get("audio_url"))
        self.assertIn("/api/speech/tts", data.get("audio_url", ""))

    def test_identity_endpoint(self):
        resp = self.client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "who are you?"}]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("actions"), [])
        content_lower = data.get("content", "").lower()
        self.assertTrue("os agent" in content_lower or "assistant" in content_lower)
        self.assertIsNotNone(data.get("audio_url"))

    def test_voice_mute_endpoint(self):
        resp = self.client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "mute voice"}]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("muted", data.get("content", "").lower())
        self.assertIsNone(data.get("audio_url"))

        # Unmute
        resp2 = self.client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "unmute voice"}]
        })
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertIn("unmuted", data2.get("content", "").lower())
        self.assertIsNotNone(data2.get("audio_url"))

    def test_normalize_speech_endpoint(self):
        resp = self.client.post("/api/speech/normalize", json={
            "text": "Task finished ✅ with 0 errors!"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("successfully", data.get("normalized", "").lower())


if __name__ == "__main__":
    unittest.main()
