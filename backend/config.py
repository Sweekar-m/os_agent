import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
ENV_FILE = BASE_DIR / ".env"

# Load from backend/.env or root .env if present
load_dotenv(BASE_DIR / ".env")
load_dotenv(ROOT_DIR / ".env")

class Settings:
    # LLM Gateway Configuration
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "nvidia")  # nvidia, openai, ollama, custom
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", os.getenv("NVIDIA_API_KEY", ""))
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"))
    LLM_MODEL: str = os.getenv("LLM_MODEL", os.getenv("DEFAULT_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"))
    LLM_VISION_MODEL: str = os.getenv("LLM_VISION_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")
    LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT", "45.0"))
    
    # Backward compatibility aliases
    NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", LLM_API_KEY)
    NVIDIA_BASE_URL: str = os.getenv("NVIDIA_BASE_URL", LLM_BASE_URL)
    DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", LLM_MODEL)
    
    # Server Networking
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    
    # Remote screen streaming
    STREAM_FPS: int = int(os.getenv("STREAM_FPS", "15"))
    STREAM_QUALITY: int = int(os.getenv("STREAM_QUALITY", "60"))
    STREAM_SCALE: float = float(os.getenv("STREAM_SCALE", "0.6"))
    
    # Phone Pairing Security
    REMOTE_PAIRING_PIN: str = os.getenv("REMOTE_PAIRING_PIN", "4826")
    REMOTE_AUTH_TOKEN: str = os.getenv("REMOTE_AUTH_TOKEN", secrets.token_hex(16))
    
    # Speech Services
    TTS_PROVIDER: str = os.getenv("TTS_PROVIDER", "edge_tts")
    STT_PROVIDER: str = os.getenv("STT_PROVIDER", "web_speech")
    VOICE_NAME: str = os.getenv("VOICE_NAME", "en-US-JennyNeural")
    VOICE_SPEED: float = float(os.getenv("VOICE_SPEED", "1.0"))
    VOICE_PITCH: str = os.getenv("VOICE_PITCH", "+0Hz")
    VOICE_VOLUME: str = os.getenv("VOICE_VOLUME", "+0%")

settings = Settings()

def update_env(key: str, value: str):
    setattr(settings, key, value)
    if key == "LLM_API_KEY":
        setattr(settings, "NVIDIA_API_KEY", value)
    elif key == "LLM_BASE_URL":
        setattr(settings, "NVIDIA_BASE_URL", value)
    elif key == "LLM_MODEL":
        setattr(settings, "DEFAULT_MODEL", value)
    
    lines = []
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
    updated = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            updated = True
        else:
            new_lines.append(line)
            
    if not updated:
        new_lines.append(f"{key}={value}\n")
        
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
