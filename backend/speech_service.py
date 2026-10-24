import io
import os
import time
import hashlib
import logging
from typing import Dict, Any, Optional, Tuple, List
from config import settings
from speech_normalizer import normalize_for_speech, normalizer

logger = logging.getLogger("speech_service")
logger.setLevel(logging.INFO)

# High-fidelity Edge-TTS Neural Voice Presets
RECOMMENDED_NEURAL_VOICES = [
    {"id": "en-US-JennyNeural", "name": "Jenny (US Natural Female)", "gender": "Female", "locale": "en-US"},
    {"id": "en-US-GuyNeural", "name": "Guy (US Natural Male)", "gender": "Male", "locale": "en-US"},
    {"id": "en-US-AriaNeural", "name": "Aria (US Expressive Female)", "gender": "Female", "locale": "en-US"},
    {"id": "en-US-ChristopherNeural", "name": "Christopher (US Formal Male)", "gender": "Male", "locale": "en-US"},
    {"id": "en-GB-SoniaNeural", "name": "Sonia (UK Professional Female)", "gender": "Female", "locale": "en-GB"},
    {"id": "en-AU-NatNeural", "name": "Nat (AU Friendly Female)", "gender": "Female", "locale": "en-AU"},
]

class SpeechService:
    """
    Production-grade Speech Service coordinating Neural Text-to-Speech (Edge-TTS),
    cloud OpenAI TTS, client-side Web Speech fallback, and speech deduplication.
    """

    def __init__(self):
        self.tts_provider = getattr(settings, "TTS_PROVIDER", "edge_tts")
        self.stt_provider = getattr(settings, "STT_PROVIDER", "web_speech")
        self.voice_name = getattr(settings, "VOICE_NAME", "en-US-JennyNeural")
        self.voice_speed = float(getattr(settings, "VOICE_SPEED", 1.0))
        self.voice_pitch = getattr(settings, "VOICE_PITCH", "+0Hz")
        self.voice_volume = getattr(settings, "VOICE_VOLUME", "+0%")

        # Audio deduplication cache: hash -> timestamp
        self._spoken_cache: Dict[str, float] = {}
        self._cache_ttl = 30.0  # Seconds before same exact phrase can be repeated

    def get_status(self) -> Dict[str, Any]:
        return {
            "tts_provider": self.tts_provider,
            "stt_provider": self.stt_provider,
            "voice_name": self.voice_name,
            "voice_speed": self.voice_speed,
            "voice_pitch": self.voice_pitch,
            "voice_volume": self.voice_volume,
            "available_voices": RECOMMENDED_NEURAL_VOICES,
            "web_speech_supported": True,
            "edge_tts_available": True
        }

    def update_config(self, provider: Optional[str] = None, voice: Optional[str] = None, speed: Optional[float] = None) -> Dict[str, Any]:
        if provider:
            self.tts_provider = provider.strip()
        if voice:
            self.voice_name = voice.strip()
        if speed is not None:
            self.voice_speed = max(0.5, min(2.0, float(speed)))
        return self.get_status()

    def filter_text_for_speech(self, text: str, user_prompt: Optional[str] = None) -> str:
        """Emoji-aware normalization delegating to SpeechNormalizer."""
        return normalize_for_speech(text, user_prompt=user_prompt)

    def _get_hash(self, text: str) -> str:
        return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()

    def is_duplicate(self, text: str) -> bool:
        """Checks if the exact speech text was synthesized recently."""
        if not text:
            return True
        h = self._get_hash(text)
        now = time.time()
        last_time = self._spoken_cache.get(h)
        if last_time and (now - last_time) < self._cache_ttl:
            return True
        return False

    def mark_spoken(self, text: str):
        """Records speech text in the deduplication cache."""
        if not text:
            return
        h = self._get_hash(text)
        now = time.time()
        self._spoken_cache[h] = now

        # Prune stale cache entries
        stale = [k for k, ts in self._spoken_cache.items() if now - ts > self._cache_ttl * 2]
        for k in stale:
            self._spoken_cache.pop(k, None)

    async def synthesize_speech(
        self,
        text: str,
        user_prompt: Optional[str] = None,
        voice: Optional[str] = None,
        rate: Optional[float] = None,
        ignore_duplicate: bool = False
    ) -> Dict[str, Any]:
        """
        Synthesizes high-fidelity neural audio.
        Returns audio bytes (MP3 format), normalized speech text, provider, and media type.
        """
        normalized = self.filter_text_for_speech(text, user_prompt=user_prompt)
        if not normalized:
            return {
                "success": False,
                "error": "Normalized text is empty",
                "normalized_text": "",
                "audio_bytes": b"",
                "media_type": "audio/mpeg",
                "provider": self.tts_provider
            }

        # Check deduplication
        if not ignore_duplicate and self.is_duplicate(normalized):
            logger.info("Skipping duplicate speech synthesis within TTL window")
            return {
                "success": True,
                "is_duplicate": True,
                "normalized_text": normalized,
                "audio_bytes": b"",
                "media_type": "audio/mpeg",
                "provider": self.tts_provider,
                "message": "Duplicate speech prevented."
            }

        selected_voice = voice or self.voice_name
        current_speed = rate if rate is not None else self.voice_speed
        
        # Calculate rate offset percentage for Edge-TTS (+10%, -15%, etc.)
        rate_diff = int(round((current_speed - 1.0) * 100))
        rate_str = f"{rate_diff:+d}%"

        # 1. Primary: Edge-TTS Neural Voice
        if self.tts_provider == "edge_tts":
            try:
                import edge_tts
                comm = edge_tts.Communicate(
                    text=normalized,
                    voice=selected_voice,
                    rate=rate_str,
                    volume=self.voice_volume,
                    pitch=self.voice_pitch
                )
                audio_chunks = []
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        audio_chunks.append(chunk["data"])
                
                audio_bytes = b"".join(audio_chunks)
                if audio_bytes:
                    self.mark_spoken(normalized)
                    return {
                        "success": True,
                        "audio_bytes": audio_bytes,
                        "media_type": "audio/mpeg",
                        "normalized_text": normalized,
                        "provider": "edge_tts",
                        "voice": selected_voice
                    }
            except Exception as e:
                logger.warning(f"Edge-TTS synthesis note: {e}, falling back to Web Speech")

        # 2. Secondary: OpenAI-compatible TTS if configured
        if self.tts_provider == "openai" and settings.LLM_API_KEY:
            try:
                import httpx
                headers = {
                    "Authorization": f"Bearer {settings.LLM_API_KEY}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "tts-1",
                    "input": normalized,
                    "voice": "alloy",
                    "speed": current_speed
                }
                url = f"{settings.LLM_BASE_URL.rstrip('/')}/audio/speech"
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        self.mark_spoken(normalized)
                        return {
                            "success": True,
                            "audio_bytes": resp.content,
                            "media_type": "audio/mpeg",
                            "normalized_text": normalized,
                            "provider": "openai",
                            "voice": "alloy"
                        }
            except Exception as oai_err:
                logger.warning(f"OpenAI TTS fallback note: {oai_err}")

        # 3. Client-side Web Speech fallback instruction
        self.mark_spoken(normalized)
        return {
            "success": True,
            "audio_bytes": b"",
            "media_type": "audio/mpeg",
            "normalized_text": normalized,
            "provider": "web_speech",
            "message": "Use client Web Speech API with normalized speech text."
        }

    async def transcribe_audio(self, audio_data: bytes, filename: str = "audio.wav") -> Dict[str, Any]:
        """Server-side audio transcription fallback using Whisper if available."""
        if not audio_data:
            return {"success": False, "error": "No audio data received", "transcript": ""}

        if settings.LLM_API_KEY and "openai" in settings.LLM_BASE_URL.lower():
            try:
                import httpx
                url = f"{settings.LLM_BASE_URL.rstrip('/')}/audio/transcriptions"
                headers = {"Authorization": f"Bearer {settings.LLM_API_KEY}"}
                files = {"file": (filename, audio_data, "audio/wav")}
                data = {"model": "whisper-1"}
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, headers=headers, files=files, data=data)
                    if resp.status_code == 200:
                        res_json = resp.json()
                        return {
                            "success": True,
                            "transcript": res_json.get("text", "").strip(),
                            "method": "server_whisper"
                        }
            except Exception as e:
                logger.warning(f"Server transcription note: {e}")

        return {
            "success": True,
            "transcript": "",
            "method": "web_speech",
            "message": "Use client Web Speech API for low-latency native microphone transcription."
        }

speech_service = SpeechService()
