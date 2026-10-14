import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
ENV_FILE = BASE_DIR / ".env"

# Load from backend/.env or root .env if present
load_dotenv(BASE_DIR / ".env")
load_dotenv(ROOT_DIR / ".env")

class Settings:
    NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", "")
    NVIDIA_BASE_URL: str = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "meta/llama-3.2-11b-vision-instruct")
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    
    # Remote stream quality settings
    STREAM_FPS: int = int(os.getenv("STREAM_FPS", "15"))
    STREAM_QUALITY: int = int(os.getenv("STREAM_QUALITY", "60"))
    STREAM_SCALE: float = float(os.getenv("STREAM_SCALE", "0.6"))

settings = Settings()

def update_env(key: str, value: str):
    setattr(settings, key, value)
    
    # Read existing or create
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
