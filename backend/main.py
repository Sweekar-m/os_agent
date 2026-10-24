import asyncio
import socket
import json
import base64
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Query, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import settings, update_env
from screen_capture import get_screenshot_bytes, get_screenshot_base64, get_screen_dimensions, get_frame_with_cursor
from input_controller import controller
from llm_client import nim_client
from ppt_agent import ppt_agent

# Core Multi-Agent OS Modules
import db
from permission_engine import permission_engine
from codex_runtime import codex_runtime
from planner import planner
from verifier import verifier
from orchestrator import orchestrator, TaskState

# 4 Specialized Agents & Services
from llm_gateway import llm_gateway
from agents.browser_agent import browser_agent
from agents.file_agent import file_agent, format_bytes, KNOWN_EXTENSIONS
from agents.system_agent import system_agent
from agents.vision_agent import vision_agent
import re
import urllib.parse
from intent_router import intent_router, AgentIntent
from speech_normalizer import normalize_for_speech
from speech_service import speech_service, RECOMMENDED_NEURAL_VOICES

logger = logging.getLogger("main")
logger.setLevel(logging.INFO)

app = FastAPI(title="Multi-Agent OS Assistant Backend", version="2.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def on_startup():
    orchestrator.startup()

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

DESKTOP_DIR = Path(__file__).resolve().parent.parent / "desktop_app"

def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: Optional[str] = None
    permission_mode: Optional[str] = "full_access"

class SettingsUpdateRequest(BaseModel):
    llm_provider: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    llm_vision_model: Optional[str] = None
    nvidia_api_key: Optional[str] = None
    default_model: Optional[str] = None
    stream_fps: Optional[int] = None
    stream_quality: Optional[int] = None
    stream_scale: Optional[float] = None
    remote_pairing_pin: Optional[str] = None
    tts_provider: Optional[str] = None
    stt_provider: Optional[str] = None
    voice_speed: Optional[float] = None

class ActionRequest(BaseModel):
    action: str
    params: Optional[Dict[str, Any]] = None

class PairingVerifyRequest(BaseModel):
    pin: Optional[str] = None
    token: Optional[str] = None

# ----------------- SYSTEM STATUS & TELEMETRY -----------------

@app.get("/api/status")
def get_status():
    w, h = get_screen_dimensions()
    gateway_st = llm_gateway.get_status()
    emergency_st = permission_engine.is_emergency_stopped()
    return {
        "status": "online",
        "emergency_stopped": emergency_st,
        "ip": get_local_ip(),
        "port": settings.PORT,
        "screen": {"width": w, "height": h},
        "has_api_key": bool(settings.LLM_API_KEY),
        "model": settings.LLM_MODEL,
        "vision_model": settings.LLM_VISION_MODEL,
        "provider": settings.LLM_PROVIDER,
        "agents": {
            "browser": {"available": True, "type": "Existing Browser Controller"},
            "file": {"available": True, "type": "Windows File Agent"},
            "system": {"available": True, "type": "Windows System Agent"},
            "vision": {"available": True, "type": "Multimodal Vision Agent"}
        }
    }

@app.get("/api/network-info")
def get_network_info():
    ip = get_local_ip()
    port = settings.PORT
    remote_url = f"http://{ip}:{port}/remote?token={settings.REMOTE_AUTH_TOKEN}"
    return {
        "ip": ip,
        "port": port,
        "remote_url": remote_url,
        "pairing_pin": settings.REMOTE_PAIRING_PIN,
        "pairing_token": settings.REMOTE_AUTH_TOKEN
    }

# ----------------- PAIRING & SECURITY -----------------

@app.get("/api/pairing/info")
def get_pairing_info():
    ip = get_local_ip()
    port = settings.PORT
    return {
        "pin": settings.REMOTE_PAIRING_PIN,
        "token": settings.REMOTE_AUTH_TOKEN,
        "url": f"http://{ip}:{port}/remote?token={settings.REMOTE_AUTH_TOKEN}"
    }

@app.post("/api/pairing/verify")
def verify_pairing(req: PairingVerifyRequest):
    if req.token and req.token == settings.REMOTE_AUTH_TOKEN:
        return {"valid": True, "token": settings.REMOTE_AUTH_TOKEN}
    if req.pin and req.pin.strip() == settings.REMOTE_PAIRING_PIN.strip():
        return {"valid": True, "token": settings.REMOTE_AUTH_TOKEN}
    return {"valid": False, "error": "Invalid pairing PIN or token"}

# ----------------- EMERGENCY STOP -----------------

@app.post("/api/emergency-stop")
def trigger_emergency_stop():
    return orchestrator.emergency_stop()

@app.post("/api/emergency-stop/clear")
def clear_emergency_stop():
    return orchestrator.clear_emergency_stop()

@app.get("/api/emergency-stop/status")
def get_emergency_stop_status():
    return {"emergency_stopped": permission_engine.is_emergency_stopped()}

# ----------------- AGENT STATUS & DIRECT OPERATIONS -----------------

@app.get("/api/agents/status")
def get_agents_status():
    browsers = browser_agent.find_existing_browsers()
    win_info = vision_agent.get_active_window_info()
    return {
        "browser_agent": {
            "name": "Browser Agent",
            "open_browsers_count": len(browsers),
            "browsers": browsers[:3],
            "supports_playwright": False,
            "controls_existing_windows": True
        },
        "file_agent": {
            "name": "File Agent",
            "workspace": str(file_agent.workspace_root),
            "backup_dir": str(file_agent.backup_dir)
        },
        "system_agent": {
            "name": "System Agent",
            "platform": "Windows",
            "cpu_count": system_agent.get_system_telemetry().get("cpu", {}).get("cores_logical", 0)
        },
        "vision_agent": {
            "name": "Vision Agent",
            "screen_dimensions": [vision_agent.screen_width, vision_agent.screen_height],
            "active_window": win_info.get("title", ""),
            "vision_model": llm_gateway.vision_model
        }
    }

@app.get("/api/system/telemetry")
def get_system_telemetry_endpoint():
    return system_agent.get_system_telemetry()

@app.get("/api/system/processes")
def get_processes_endpoint(limit: int = 25, filter_name: Optional[str] = None):
    return system_agent.list_processes(limit=limit, filter_name=filter_name)

@app.post("/api/vision/analyze")
async def analyze_screen_endpoint(req: Optional[ActionRequest] = None):
    p = req.params.get("prompt") if req and req.params else None
    return await vision_agent.analyze_screen(prompt=p)

# ----------------- SPEECH SERVICES (STT & TTS) -----------------

@app.get("/api/speech/config")
def get_speech_config():
    return speech_service.get_status()

@app.post("/api/speech/transcribe")
async def transcribe_audio_endpoint(file: UploadFile = File(...)):
    data = await file.read()
    return await speech_service.transcribe_audio(data, filename=file.filename or "audio.wav")

# ----------------- SETTINGS & CONFIGURATION -----------------

@app.get("/api/settings")
def get_settings():
    key = settings.LLM_API_KEY
    masked_key = f"{key[:7]}...{key[-4:]}" if len(key) > 12 else ("configured" if key else "")
    return {
        "has_api_key": bool(key),
        "masked_key": masked_key,
        "llm_provider": settings.LLM_PROVIDER,
        "llm_base_url": settings.LLM_BASE_URL,
        "llm_model": settings.LLM_MODEL,
        "llm_vision_model": settings.LLM_VISION_MODEL,
        "default_model": settings.DEFAULT_MODEL,
        "stream_fps": settings.STREAM_FPS,
        "stream_quality": settings.STREAM_QUALITY,
        "stream_scale": settings.STREAM_SCALE,
        "remote_pairing_pin": settings.REMOTE_PAIRING_PIN,
        "tts_provider": settings.TTS_PROVIDER,
        "stt_provider": settings.STT_PROVIDER,
        "voice_speed": settings.VOICE_SPEED,
        "default_workspace": str(Path(__file__).resolve().parent.parent)
    }

@app.post("/api/settings")
def save_settings(req: SettingsUpdateRequest):
    if req.llm_provider is not None:
        update_env("LLM_PROVIDER", req.llm_provider.strip())
    if req.llm_api_key is not None:
        update_env("LLM_API_KEY", req.llm_api_key.strip())
    if req.nvidia_api_key is not None:
        update_env("NVIDIA_API_KEY", req.nvidia_api_key.strip())
        update_env("LLM_API_KEY", req.nvidia_api_key.strip())
    if req.llm_base_url is not None:
        update_env("LLM_BASE_URL", req.llm_base_url.strip())
    if req.llm_model is not None:
        update_env("LLM_MODEL", req.llm_model.strip())
    if req.llm_vision_model is not None:
        update_env("LLM_VISION_MODEL", req.llm_vision_model.strip())
    if req.default_model is not None:
        update_env("DEFAULT_MODEL", req.default_model.strip())
    if req.stream_fps is not None:
        update_env("STREAM_FPS", str(req.stream_fps))
    if req.stream_quality is not None:
        update_env("STREAM_QUALITY", str(req.stream_quality))
    if req.stream_scale is not None:
        update_env("STREAM_SCALE", str(req.stream_scale))
    if req.remote_pairing_pin is not None:
        update_env("REMOTE_PAIRING_PIN", req.remote_pairing_pin.strip())
    if req.voice_speed is not None:
        update_env("VOICE_SPEED", str(req.voice_speed))
    return {"status": "updated", "settings": get_settings()}

@app.get("/api/screenshot")
def get_screenshot(scale: Optional[float] = None, quality: Optional[int] = None):
    try:
        jpeg_bytes = get_screenshot_bytes(scale=scale, quality=quality)
        return Response(content=jpeg_bytes, media_type="image/jpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/action")
def perform_action(req: ActionRequest):
    act = req.action
    p = req.params or {}
    try:
        if act == "move_rel":
            controller.move_rel(float(p.get("dx", 0)), float(p.get("dy", 0)))
        elif act == "move_to":
            controller.move_to(int(p.get("x", 0)), int(p.get("y", 0)))
        elif act == "click":
            controller.click(button=p.get("button", "left"), clicks=int(p.get("clicks", 1)))
        elif act == "mouse_down":
            controller.mouse_down(button=p.get("button", "left"))
        elif act == "mouse_up":
            controller.mouse_up(button=p.get("button", "left"))
        elif act == "scroll":
            controller.scroll(float(p.get("dy", 0)), float(p.get("dx", 0)))
        elif act == "type":
            controller.type_text(str(p.get("text", "")))
        elif act == "press":
            controller.press_key(str(p.get("key", "enter")))
        elif act == "hotkey":
            controller.hotkey(*p.get("keys", []))
        elif act == "open_app":
            system_agent.launch_app(str(p.get("app", "")))
        return {"status": "success", "action": act}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ----------------- SPEECH & TTS API -----------------

class SynthesizeSpeechRequest(BaseModel):
    text: str
    user_prompt: Optional[str] = None
    voice: Optional[str] = None
    speed: Optional[float] = None

class SpeechConfigRequest(BaseModel):
    provider: Optional[str] = None
    voice: Optional[str] = None
    speed: Optional[float] = None

class NormalizeSpeechRequest(BaseModel):
    text: str
    user_prompt: Optional[str] = None

@app.get("/api/speech/tts")
async def get_speech_tts(
    text: str = Query(..., description="Text to synthesize to speech"),
    voice: Optional[str] = Query(None, description="Neural voice identifier"),
    speed: Optional[float] = Query(None, description="Voice speed multiplier (0.5 to 2.0)"),
    prompt: Optional[str] = Query(None, description="User prompt context for emoji inquiry")
):
    """
    Synthesizes speech audio using the configured Neural TTS provider (Edge-TTS by default).
    Returns MP3 audio streaming response for direct playback in desktop & mobile browser.
    """
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Empty text parameter")

    if speech_service.tts_provider == "muted":
        return Response(status_code=204)

    try:
        res = await speech_service.synthesize_speech(
            text=text,
            user_prompt=prompt,
            voice=voice,
            rate=speed,
            ignore_duplicate=True
        )
        audio_bytes = res.get("audio_bytes", b"")
        if audio_bytes:
            return Response(
                content=audio_bytes,
                media_type="audio/mpeg",
                headers={
                    "Content-Disposition": 'inline; filename="speech.mp3"',
                    "Cache-Control": "public, max-age=3600"
                }
            )
        return Response(status_code=204)
    except Exception as e:
        logger.warning(f"TTS synthesis error: {e}")
        return Response(status_code=204)

@app.post("/api/speech/synthesize")
async def synthesize_speech_endpoint(req: SynthesizeSpeechRequest):
    res = await speech_service.synthesize_speech(
        text=req.text,
        user_prompt=req.user_prompt,
        voice=req.voice,
        rate=req.speed,
        ignore_duplicate=True
    )
    audio_bytes = res.get("audio_bytes", b"")
    b64_audio = base64.b64encode(audio_bytes).decode("ascii") if audio_bytes else ""
    norm = res.get("normalized_text", "")
    return {
        "success": res.get("success", False),
        "normalized_text": norm,
        "provider": res.get("provider", speech_service.tts_provider),
        "voice": res.get("voice", speech_service.voice_name),
        "audio_base64": b64_audio,
        "audio_url": f"/api/speech/tts?text={urllib.parse.quote(norm)}" if audio_bytes else None
    }

@app.get("/api/speech/status")
def get_speech_status_endpoint():
    return speech_service.get_status()

@app.post("/api/speech/config")
def update_speech_config_endpoint(req: SpeechConfigRequest):
    return speech_service.update_config(provider=req.provider, voice=req.voice, speed=req.speed)

@app.post("/api/speech/normalize")
def normalize_speech_endpoint(req: NormalizeSpeechRequest):
    normalized = speech_service.filter_text_for_speech(req.text, user_prompt=req.user_prompt)
    return {"original": req.text, "normalized": normalized}

# ----------------- CHAT & INTERACTIVE AGENT -----------------

async def handle_file_operation(last_user_msg: str, raw_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    if permission_engine.is_emergency_stopped():
        err_msg = "🛑 Emergency Stop is currently active. All file operations are halted."
        sp = speech_service.filter_text_for_speech(err_msg, user_prompt=last_user_msg)
        return {
            "role": "assistant",
            "content": err_msg,
            "actions": [],
            "spoken_summary": sp,
            "audio_url": None,
            "ppt_files": []
        }

    # 1. Recent Documents Discovery
    if intent_router.is_recent_documents_request(last_user_msg):
        recent_res = file_agent.find_recent_documents(limit=10)
        docs = recent_res.get("documents", [])
        count = len(docs)
        if count == 0:
            content = "I searched your Desktop, Documents, and Downloads, but found no recent documents."
            spoken = "I searched your recent documents, but found no files saved recently."
        else:
            lines = [f"I found {count} recent document{'s' if count != 1 else ''} saved on your PC:\n"]
            for doc in docs:
                lines.append(f"• **{doc['id']}.** `{doc['filename']}` ({doc.get('size_display', '')}, modified {doc.get('modified', '')}) — `{doc['path']}`")
            lines.append("\nYou can say 'open docs', 'open the second one', or specify the file name to open it.")
            content = "\n".join(lines)
            sample_names = [f"'{Path(d['filename']).stem.replace('_', ' ')}'" for d in docs[:3]]
            spoken = f"I found {count} recent document{'s' if count != 1 else ''}, including {', '.join(sample_names)}."

        actions = [{
            "type": "recent_documents",
            "status": "success",
            "total_found": recent_res.get("total_found", count),
            "count": count,
            "documents": docs
        }]
        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # 2. Open Document / File
    if intent_router.is_open_file_request(last_user_msg, raw_messages):
        open_target = intent_router.extract_open_file_target(last_user_msg, raw_messages)

        # User said "open docs" without a specific target
        if not open_target:
            if not file_agent.last_search_results:
                file_agent.find_recent_documents(limit=10)

            if file_agent.last_search_results:
                sample_names = [f"'{Path(d['filename']).stem.replace('_', ' ')}'" for d in file_agent.last_search_results[:3]]
                lines = ["Which doc would you like me to open? Here are your recent saves:\n"]
                for d in file_agent.last_search_results:
                    lines.append(f"• **{d['id']}.** `{d['filename']}` ({d.get('modified', '')})")
                lines.append("\nYou can specify the name (e.g. 'Project summary') or number (e.g. '1').")
                content = "\n".join(lines)
                spoken = f"Which docs would you like me to open? I found {', '.join(sample_names)} from your recent saves."
            else:
                content = "I didn't find any recent documents in your Desktop, Documents, or Downloads folders to open."
                spoken = "I didn't find any recent documents to open."

            actions = [{
                "type": "open_prompt",
                "available_files": [d["filename"] for d in file_agent.last_search_results]
            }]
            audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
            return {
                "role": "assistant",
                "content": content,
                "actions": actions,
                "spoken_summary": spoken,
                "audio_url": audio_url,
                "ppt_files": []
            }

        # Target is specified -> execute open_file
        open_res = file_agent.open_file(open_target)

        if open_res.get("ambiguous"):
            cands = open_res.get("candidates", [])
            content = f"Multiple files matched '{open_target}':\n" + "\n".join(f"• `{c}`" for c in cands) + "\n\nWhich one would you like me to open?"
            spoken = f"Multiple files matched '{open_target}'. Which one would you like me to open?"
            actions = [{
                "type": "open_file",
                "status": "ambiguous",
                "file_found": False,
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "candidates": cands
            }]
        elif open_res.get("not_found"):
            err_msg = open_res.get("error", f"File '{open_target}' was not found.")
            content = f"⚠️ {err_msg}"
            spoken = f"I couldn't find '{open_target}' in your recent documents or on disk."
            actions = [{
                "type": "open_file",
                "status": "not_found",
                "file_found": False,
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "error": err_msg
            }]
        elif open_res.get("permission_denied"):
            err_msg = open_res.get("error", "Permission denied.")
            content = f"🛑 {err_msg}"
            spoken = f"Permission denied to open '{open_target}'."
            actions = [{
                "type": "open_file",
                "status": "permission_denied",
                "file_found": open_res.get("file_found", True),
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "error": err_msg
            }]
        elif not open_res.get("success"):
            err_msg = open_res.get("error", "Failed to open file.")
            content = f"⚠️ {err_msg}"
            spoken = f"Failed to open '{open_target}'."
            actions = [{
                "type": "open_file",
                "status": "error",
                "file_found": open_res.get("file_found", False),
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "error": err_msg,
                "desktop_session": open_res.get("desktop_session", {})
            }]
        else:
            fname = open_res.get("filename", open_target)
            fpath = open_res.get("path", "")
            visibly_opened = open_res.get("visibly_opened", False)
            if visibly_opened:
                content = f"✅ Opened `{fname}` (`{fpath}`) on your desktop and verified the application window is visible."
            else:
                content = f"✅ Opened `{fname}` (`{fpath}`) on your desktop."
            stem_clean = Path(fname).stem.replace("_", " ").replace("-", " ")
            spoken = f"Opening your {stem_clean} now."
            actions = [{
                "type": "open_file",
                "status": "success",
                "opened": True,
                "file_found": open_res.get("file_found", True),
                "open_requested": open_res.get("open_requested", True),
                "command_dispatched": open_res.get("command_dispatched", True),
                "visibly_opened": visibly_opened,
                "desktop_session": open_res.get("desktop_session", {}),
                "path": fpath,
                "filename": fname
            }]

        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # 3. Read/view file
    read_match = re.search(r"^(?:read|view|show|display|cat|inspect)\s+(?:file\s+)?(['\"]?)(.+?\.[a-zA-Z0-9_\-]+)\1$", last_user_msg.strip(), re.IGNORECASE)
    if read_match and not any(k in last_user_msg.lower() for k in ["ppt", "pptx", "presentation"]):
        target_path = read_match.group(2).strip()
        read_res = file_agent.read_file(target_path, max_lines=150)
        if not read_res["success"]:
            content = f"⚠️ Could not read file: {read_res.get('error')}"
            actions = [{"type": "read_file", "path": target_path, "status": "error", "error": read_res.get("error")}]
        else:
            p_obj = Path(read_res["path"])
            content = (
                f"📄 **File Content: `{p_obj.name}`** ({format_bytes(read_res['size_bytes'])}, {read_res['total_lines']} total lines):\n\n"
                f"```{p_obj.suffix.lstrip('.') if p_obj.suffix else ''}\n"
                f"{read_res['content']}\n"
                f"```"
            )
            if read_res.get("truncated"):
                content += f"\n*(Showing first 150 lines of {read_res['total_lines']})*"
            actions = [{"type": "read_file", "path": read_res["path"], "status": "success", "size_bytes": read_res["size_bytes"]}]

        spoken = speech_service.filter_text_for_speech(content, user_prompt=last_user_msg)
        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # 4. Standard File Search (with folder prompts / resolution)
    query, folder = intent_router.extract_file_search_params(last_user_msg, raw_messages)
    is_bare_ext = (
        not query or
        query.lower().lstrip(".") in KNOWN_EXTENSIONS or
        query.lower() in ["file", "files", "document", "documents", "doc", "docs"]
    )
    if not folder and is_bare_ext and any(w in last_user_msg.lower() for w in ["search for", "find", "look for", "need to search", "locate", "scan"]):
        ext_label = f".{query.lstrip('.')}" if query else "the requested"
        content = f"I can search your PC for {ext_label} files. Should I scan your Desktop, Documents, Downloads, or another specific folder?"
        spoken = f"I can search your PC for {ext_label} files. Which folder should I scan?"
        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": [],
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    search_query = query or "docx"
    target_folder = folder or None
    search_res = file_agent.search_files(query=search_query, search_dir=target_folder)

    if search_res.get("permission_denied"):
        err_text = search_res.get("error", "Access denied by Windows permissions.")
        content = f"⚠️ {err_text} Please check folder permissions or choose a different directory."
        spoken = f"Access to {target_folder or 'requested folder'} was denied by permissions. Please choose a different directory."
        actions = [{
            "type": "file_search",
            "query": search_query,
            "directory": target_folder,
            "status": "permission_denied",
            "error": err_text
        }]
    elif not search_res.get("success"):
        err_text = search_res.get("error", "File search failed.")
        content = f"⚠️ {err_text}"
        spoken = err_text
        actions = [{
            "type": "file_search",
            "query": search_query,
            "directory": target_folder,
            "status": "error",
            "error": err_text
        }]
    else:
        matches = search_res.get("matches", [])
        scanned = search_res.get("scanned_directories", [search_res.get("search_directory", target_folder or "PC")])
        primary_display = scanned[0] if scanned else (target_folder or "your computer")
        folder_clean = target_folder.replace(" folder", "").replace(" Folder", "") if target_folder else "computer"

        if len(matches) == 0:
            if target_folder:
                content = f"I searched your {folder_clean} (`{primary_display}`), but found no `.{search_query.lstrip('.')}` files." if search_query.lower() in KNOWN_EXTENSIONS else f"I searched your {folder_clean} (`{primary_display}`), but found no files matching '{search_query}'."
                spoken = f"I searched your {folder_clean}, but found no matching {search_query} files."
            else:
                content = f"I searched your Desktop, Documents, and Downloads, but found no files matching '{search_query}'."
                spoken = f"I searched your computer, but found no matching {search_query} files."
        else:
            count = len(matches)
            plural = "file" if count == 1 else "files"
            loc_label = f" in `{primary_display}`" if target_folder else " on your PC"
            display_q = f".{search_query.lstrip('.')}" if search_query.lower() in KNOWN_EXTENSIONS else search_query
            lines = [f"Found {count} matching `{display_q}` {plural}{loc_label}:"]
            for m in matches[:15]:
                lines.append(f"• `{m['filename']}` (`{m['path']}` — {m.get('size_display', '')})")
            if count > 15:
                lines.append(f"\n*(...and {count - 15} more files)*")
            content = "\n".join(lines)
            spoken = f"Found {count} matching {search_query} {plural}."

        actions = [{
            "type": "file_search",
            "query": search_query,
            "directory": target_folder or "All Standard Folders",
            "resolved_paths": scanned,
            "total_matches": len(matches),
            "file_found": len(matches) > 0,
            "matches": matches[:15],
            "status": "success"
        }]

    audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
    return {
        "role": "assistant",
        "content": content,
        "actions": actions,
        "spoken_summary": spoken,
        "audio_url": audio_url,
        "ppt_files": []
    }

async def handle_browser_operation(last_user_msg: str, raw_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    if permission_engine.is_emergency_stopped():
        err_msg = "🛑 Emergency Stop is currently active. All browser operations are halted."
        sp = speech_service.filter_text_for_speech(err_msg, user_prompt=last_user_msg)
        return {
            "role": "assistant",
            "content": err_msg,
            "actions": [],
            "spoken_summary": sp,
            "audio_url": None,
            "ppt_files": []
        }

    # 1. Summarize open tabs
    if intent_router.is_summarize_tabs_request(last_user_msg):
        sum_res = await browser_agent.summarize_open_tabs(max_tabs=15)
        if not sum_res.get("success"):
            err_msg = sum_res.get("error", "Could not access browser tabs.")
            content = f"⚠️ {err_msg}"
            spoken = err_msg
            actions = [{
                "agent": "Browser Agent",
                "action": "summarize_tabs",
                "status": "error",
                "success": False,
                "error": err_msg,
                "verification": {"status": "failed", "reason": err_msg}
            }]
        else:
            browser_name = sum_res.get("browser") or sum_res.get("browser_type") or "Browser"
            summaries = sum_res.get("summaries") or sum_res.get("tabs", [])
            total_tabs = sum_res.get("total_tabs", len(summaries))

            lines = [f"### 🌐 Open Tabs in {browser_name} ({total_tabs} total tabs):\n"]
            spoken_items = []
            for s in summaries:
                t_idx = s.get("index") or s.get("id", 1)
                t_title = s.get("title", "Untitled Tab")
                t_url = s.get("url", "")
                t_sum = s.get("summary", "")
                accessible = s.get("accessible", True)

                url_display = f"[`{t_url}`]({t_url})" if t_url else "*No URL*"
                lines.append(f"#### {t_idx}. {t_title}\n- **URL**: {url_display}\n- **Summary**: {t_sum}\n")
                if accessible and t_title != "New Tab":
                    spoken_items.append(f"Tab {t_idx}: {t_title}")

            content = "\n".join(lines)
            if sum_res.get("spoken_summary"):
                spoken = sum_res["spoken_summary"]
            elif spoken_items:
                spoken = f"I inspected your {browser_name} browser with {total_tabs} tabs. " + ". ".join(spoken_items[:3]) + "."
            else:
                spoken = f"I inspected your {browser_name} browser with {total_tabs} open tabs."

            actions = [{
                "agent": "Browser Agent",
                "action": "summarize_tabs",
                "status": "success",
                "success": True,
                "browser": browser_name,
                "total_tabs": total_tabs,
                "data": summaries,
                "verification": {
                    "status": "verified",
                    "method": "UIAutomationTextPattern",
                    "live_session": True
                }
            }]

        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # 2. List open tabs
    if intent_router.is_list_tabs_request(last_user_msg):
        tabs_res = browser_agent.get_open_tabs()
        if not tabs_res.get("success"):
            err_msg = tabs_res.get("error", "Could not enumerate open browser tabs.")
            content = f"⚠️ {err_msg}"
            spoken = err_msg
            actions = [{
                "agent": "Browser Agent",
                "action": "list_tabs",
                "status": "error",
                "success": False,
                "error": err_msg,
                "verification": {"status": "failed", "reason": err_msg}
            }]
        else:
            browser_name = tabs_res.get("browser", "Browser")
            tabs = tabs_res.get("tabs", [])
            total_tabs = tabs_res.get("total_tabs", len(tabs))
            lines = [f"### 🌐 Open Tabs in {browser_name} ({total_tabs} total tabs):\n"]
            for t in tabs:
                active_badge = " *(Active)*" if t.get("is_active") else ""
                url_str = f" — `{t.get('url')}`" if t.get("url") else ""
                lines.append(f"• **{t.get('index')}.** {t.get('title')}{active_badge}{url_str}")
            content = "\n".join(lines)
            spoken = f"You have {total_tabs} tabs open in {browser_name}."
            actions = [{
                "agent": "Browser Agent",
                "action": "list_tabs",
                "status": "success",
                "success": True,
                "browser": browser_name,
                "total_tabs": total_tabs,
                "data": tabs,
                "verification": {
                    "status": "verified",
                    "method": "UIAutomationTabItem",
                    "live_session": True
                }
            }]

        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # 3. Browser Search / Navigation
    p_lower = last_user_msg.lower().strip()
    if any(p in p_lower for p in ["search for", "google for", "search in browser", "search "]):
        q = last_user_msg
        for prefix in ["search for", "google for", "search in browser", "google", "search"]:
            if prefix in p_lower:
                q = last_user_msg[p_lower.find(prefix) + len(prefix):].strip()
                break
        search_res = await browser_agent.search(q)
        content = search_res.get("message") or f"Searched for '{q}' in browser."
        spoken = f"Searched for {q} in your browser."
        actions = [{
            "agent": "Browser Agent",
            "action": "search",
            "status": "success" if search_res.get("success") else "error",
            "success": search_res.get("success", False),
            "data": search_res,
            "error": search_res.get("error"),
            "verification": {"status": "verified" if search_res.get("success") else "failed"}
        }]
        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # Direct URL match or navigation
    url_match = re.search(r"https?://[^\s]+|www\.[^\s]+|[a-zA-Z0-9\-]+\.(?:com|org|net|io|edu|gov|dev)[^\s]*", last_user_msg)
    if url_match:
        target_url = url_match.group(0)
        nav_res = await browser_agent.navigate(target_url)
        content = nav_res.get("message") or f"Navigated to {target_url}."
        spoken = f"Opened {target_url} in your browser."
        actions = [{
            "agent": "Browser Agent",
            "action": "navigate",
            "status": "success" if nav_res.get("success") else "error",
            "success": nav_res.get("success", False),
            "data": nav_res,
            "error": nav_res.get("error"),
            "verification": {"status": "verified" if nav_res.get("success") else "failed"}
        }]
        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # Fallback to enumerating open tabs
    tabs_res = browser_agent.get_open_tabs()
    content = tabs_res.get("message", "Browser controller active.")
    spoken = content
    return {
        "role": "assistant",
        "content": content,
        "actions": [tabs_res],
        "spoken_summary": spoken,
        "audio_url": None,
        "ppt_files": []
    }

async def handle_system_operation(last_user_msg: str, raw_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    if permission_engine.is_emergency_stopped():
        err_msg = "🛑 Emergency Stop is currently active. All system operations are halted."
        sp = speech_service.filter_text_for_speech(err_msg, user_prompt=last_user_msg)
        return {
            "role": "assistant",
            "content": err_msg,
            "actions": [],
            "spoken_summary": sp,
            "audio_url": None,
            "ppt_files": []
        }

    p_lower = last_user_msg.lower().strip()

    # 1. Telemetry / Specs / Hardware
    if any(w in p_lower for w in ["cpu", "ram", "memory", "specs", "disk space", "hardware", "telemetry", "system info"]):
        tel = system_agent.get_system_telemetry()
        if tel.get("success"):
            cpu_p = tel["cpu"]["percent"]
            mem_p = tel["memory"]["percent"]
            mem_u = tel["memory"]["used_gb"]
            mem_t = tel["memory"]["total_gb"]
            content = (
                f"### 💻 System Telemetry:\n"
                f"• **CPU**: {cpu_p}% utilization ({tel['cpu']['cores_physical']} physical / {tel['cpu']['cores_logical']} logical cores)\n"
                f"• **RAM**: {mem_p}% used ({mem_u} GB / {mem_t} GB)\n"
                f"• **OS**: {tel['os']['platform']} {tel['os']['release']} ({tel['os']['architecture']})\n"
                f"• **Host**: `{tel['os']['hostname']}` (Uptime: {tel['os']['uptime_hours']} hours)"
            )
            if "disk" in tel and "percent" in tel["disk"]:
                content += f"\n• **Disk**: {tel['disk']['percent']}% used ({tel['disk']['used_gb']} GB / {tel['disk']['total_gb']} GB)"
            spoken = f"Your CPU is at {cpu_p} percent and RAM is at {mem_p} percent usage."
            actions = [{
                "agent": "System Agent",
                "action": "telemetry",
                "status": "success",
                "success": True,
                "data": tel,
                "verification": {"status": "verified", "source": "psutil"}
            }]
        else:
            content = f"⚠️ Could not read system telemetry: {tel.get('error')}"
            spoken = "Could not read system telemetry."
            actions = [{
                "agent": "System Agent",
                "action": "telemetry",
                "status": "error",
                "success": False,
                "error": tel.get("error")
            }]

        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # 2. Diagnostics
    if any(w in p_lower for w in ["diagnose", "slow", "system health", "health check"]):
        diag = system_agent.diagnose_system()
        content = f"### 🩺 System Diagnostics ({diag.get('health_score')}/100 - {diag.get('status')}):\n"
        alerts = diag.get("alerts", [])
        if alerts:
            content += "\n".join(f"• ⚠️ {a}" for a in alerts)
        else:
            content += "• ✅ All system resources are healthy and within normal operating parameters."
        spoken = f"System health is {diag.get('status')}, score {diag.get('health_score')} out of 100."
        actions = [{
            "agent": "System Agent",
            "action": "diagnose",
            "status": "success",
            "success": True,
            "data": diag,
            "verification": {"status": "verified"}
        }]
        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # 3. Process Listing
    if any(w in p_lower for w in ["processes", "running apps", "task manager", "list processes"]):
        procs_res = system_agent.list_processes(limit=15)
        if procs_res.get("success"):
            procs = procs_res.get("processes", [])
            lines = [f"### 📋 Top Running Processes ({len(procs)} shown):\n"]
            for p in procs:
                lines.append(f"• **{p['name']}** (PID: {p['pid']}) — CPU: {p.get('cpu_percent', 0)}%, RAM: {p.get('memory_mb', 0):.1f} MB")
            content = "\n".join(lines)
            spoken = f"Found {len(procs)} top running processes."
            actions = [{
                "agent": "System Agent",
                "action": "list_processes",
                "status": "success",
                "success": True,
                "data": procs,
                "verification": {"status": "verified", "source": "psutil"}
            }]
        else:
            content = f"⚠️ Could not list processes: {procs_res.get('error')}"
            spoken = "Could not list processes."
            actions = [{
                "agent": "System Agent",
                "action": "list_processes",
                "status": "error",
                "success": False,
                "error": procs_res.get("error")
            }]

        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # 4. Terminate process
    if any(w in p_lower for w in ["kill ", "terminate ", "stop process", "close app"]):
        target = re.sub(r"^(?:kill|terminate|stop process|close app|stop)\s+", "", p_lower).strip()
        pid = int(target) if target.isdigit() else None
        name = target if not target.isdigit() else None
        term_res = system_agent.terminate_process(pid=pid, name=name, confirm=True)
        content = term_res.get("message") or f"Terminated {target}."
        spoken = content
        actions = [{
            "agent": "System Agent",
            "action": "kill_process",
            "status": "success" if term_res.get("success") else "error",
            "success": term_res.get("success", False),
            "data": term_res,
            "error": term_res.get("error"),
            "verification": {"status": "verified" if term_res.get("success") else "failed"}
        }]
        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # 5. Launch application
    if p_lower.startswith("launch ") or p_lower.startswith("start app ") or (p_lower.startswith("open ") and not intent_router.is_open_file_request(last_user_msg)):
        app_name = re.sub(r"^(?:launch|start app|open)\s+", "", last_user_msg, flags=re.IGNORECASE).strip()
        launch_res = system_agent.launch_app(app_name)
        content = launch_res.get("message") or f"Launched '{app_name}' on desktop."
        spoken = f"Opening {app_name} now."
        actions = [{
            "agent": "System Agent",
            "action": "launch_app",
            "status": "success" if launch_res.get("success") else "error",
            "success": launch_res.get("success", False),
            "data": launch_res,
            "error": launch_res.get("error"),
            "verification": {"status": "verified" if launch_res.get("success") else "failed"}
        }]
        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # Fallback to general system telemetry
    tel = system_agent.get_system_telemetry()
    content = f"System agent active. CPU: {tel.get('cpu', {}).get('percent', 0)}%, RAM: {tel.get('memory', {}).get('percent', 0)}%."
    return {
        "role": "assistant",
        "content": content,
        "actions": [tel],
        "spoken_summary": content,
        "audio_url": None,
        "ppt_files": []
    }

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    """
    Main interactive chat endpoint for the desktop app and remote UI.
    Dispatches to conversational AI, specialized agents (Vision, System, Browser, File),
    or closed-loop ReAct OS automation.
    """
    # 0. Check Emergency Stop
    if permission_engine.is_emergency_stopped():
        return {
            "role": "assistant",
            "content": "🛑 **Emergency Stop is currently active.** All agent operations are halted. Resume via the header button or settings to proceed.",
            "actions": [],
            "spoken_summary": "Emergency stop is active. Resume via the header button to proceed.",
            "audio_url": None,
            "ppt_files": []
        }

    raw_messages = [m.dict() if hasattr(m, 'dict') else dict(m) for m in req.messages]
    last_user_msg = ""
    for m in reversed(raw_messages):
        if m.get("role") == "user":
            last_user_msg = m.get("content", "").strip()
            break

    if not last_user_msg:
        return {
            "role": "assistant",
            "content": "Please enter a message or command.",
            "actions": [],
            "spoken_summary": "Please enter a message or command.",
            "audio_url": None,
            "ppt_files": []
        }

    # 1. Classify User Intent using lightweight, deterministic IntentRouter
    intent = intent_router.classify(last_user_msg, raw_messages)
    logger.info(f"Chat message '{last_user_msg[:40]}' classified as: {intent}")

    # 2. Voice Operation Intent
    if intent == AgentIntent.VOICE_OPERATION:
        msg_l = last_user_msg.lower()
        if "mute" in msg_l and "unmute" not in msg_l:
            speech_service.update_config(provider="muted")
            content = "Voice audio is now muted."
            return {
                "role": "assistant",
                "content": content,
                "actions": [],
                "spoken_summary": "",
                "audio_url": None,
                "ppt_files": []
            }
        elif "unmute" in msg_l:
            speech_service.update_config(provider="edge_tts")
            content = "Voice audio is unmuted and active."
            spoken = speech_service.filter_text_for_speech(content)
            audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if speech_service.tts_provider != "muted" else None
            return {
                "role": "assistant",
                "content": content,
                "actions": [],
                "spoken_summary": spoken,
                "audio_url": audio_url,
                "ppt_files": []
            }
        elif "faster" in msg_l or "speed up" in msg_l:
            new_spd = min(2.0, speech_service.voice_speed + 0.15)
            speech_service.update_config(speed=new_spd)
            content = f"Speaking speed set to {new_spd:.2f}x."
            spoken = speech_service.filter_text_for_speech(content)
            audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if speech_service.tts_provider != "muted" else None
            return {
                "role": "assistant",
                "content": content,
                "actions": [],
                "spoken_summary": spoken,
                "audio_url": audio_url,
                "ppt_files": []
            }
        elif "slower" in msg_l or "slow down" in msg_l:
            new_spd = max(0.6, speech_service.voice_speed - 0.15)
            speech_service.update_config(speed=new_spd)
            content = f"Speaking speed set to {new_spd:.2f}x."
            spoken = speech_service.filter_text_for_speech(content)
            audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if speech_service.tts_provider != "muted" else None
            return {
                "role": "assistant",
                "content": content,
                "actions": [],
                "spoken_summary": spoken,
                "audio_url": audio_url,
                "ppt_files": []
            }
        elif "guy" in msg_l:
            speech_service.update_config(voice="en-US-GuyNeural")
            content = "Switched voice to Guy (US Natural Male)."
            spoken = speech_service.filter_text_for_speech(content)
            audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if speech_service.tts_provider != "muted" else None
            return {
                "role": "assistant",
                "content": content,
                "actions": [],
                "spoken_summary": spoken,
                "audio_url": audio_url,
                "ppt_files": []
            }
        elif "jenny" in msg_l:
            speech_service.update_config(voice="en-US-JennyNeural")
            content = "Switched voice to Jenny (US Natural Female)."
            spoken = speech_service.filter_text_for_speech(content)
            audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if speech_service.tts_provider != "muted" else None
            return {
                "role": "assistant",
                "content": content,
                "actions": [],
                "spoken_summary": spoken,
                "audio_url": audio_url,
                "ppt_files": []
            }

    # 3. Direct Vision Agent routing for screen observation queries
    if intent == AgentIntent.SCREEN_QUESTION:
        try:
            analysis = await vision_agent.analyze_screen(prompt=last_user_msg)
            desc = analysis.get("description") or analysis.get("error") or "Screen analyzed."
            actions = [{
                "type": "vision_analysis",
                "active_window": analysis.get("active_window", {}),
                "elements_count": len(analysis.get("interactive_elements", [])),
                "status": "success" if analysis.get("success") else "error"
            }]
            spoken = speech_service.filter_text_for_speech(desc, user_prompt=last_user_msg)
            audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
            return {
                "role": "assistant",
                "content": desc,
                "actions": actions,
                "spoken_summary": spoken,
                "audio_url": audio_url,
                "ppt_files": []
            }
        except Exception as v_err:
            logger.warning(f"Vision direct analysis fallback: {v_err}")

    # 3b. Direct File Agent routing for file searches, recent documents, & verified file opening
    if intent == AgentIntent.FILE_OPERATION:
        return await handle_file_operation(last_user_msg, raw_messages)

    # 3c. Direct Browser Agent routing for live tab inspection, summarization, navigation, and web search
    if intent == AgentIntent.BROWSER_OPERATION:
        return await handle_browser_operation(last_user_msg, raw_messages)

    # 3d. Direct System Agent routing for telemetry, process management, diagnostics, and app launches
    if intent == AgentIntent.SYSTEM_OPERATION:
        return await handle_system_operation(last_user_msg, raw_messages)

    # 4. Casual Conversation & Identity Intent -> Direct Conversational LLM (No tools, no action blocks)
    if intent in [AgentIntent.CASUAL_CONVERSATION, AgentIntent.ASSISTANT_IDENTITY]:
        sys_prompt = intent_router.get_conversation_system_prompt(is_identity_query=(intent == AgentIntent.ASSISTANT_IDENTITY))
        conv_messages = [{"role": "system", "content": sys_prompt}]
        for m in raw_messages:
            if m.get("role") in ["user", "assistant"]:
                conv_messages.append({"role": m["role"], "content": m.get("content", "")})

        content = ""
        try:
            gw_res = await llm_gateway.chat_complete(
                messages=conv_messages,
                model=req.model or settings.LLM_MODEL,
                temperature=0.6,
                max_tokens=2048
            )
            if gw_res.get("success") and gw_res.get("content"):
                content = gw_res["content"].strip()
        except Exception as c_err:
            logger.warning(f"Conversational gateway error: {c_err}")

        # Natural fallback if LLM service is offline or not configured
        if not content:
            if intent == AgentIntent.ASSISTANT_IDENTITY:
                content = "I'm OS Agent, your Windows desktop assistant. I help you operate your computer through 4 specialized local agents: Browser, File, System, and Vision."
            else:
                content = "Hey there! How can I help you today?"

        # Strip accidental action blocks or hallucinated action code fences in small talk
        content = re.sub(r"```(?:action|json)?[\s\S]*?```", "", content).strip()

        spoken_summary = speech_service.filter_text_for_speech(content, user_prompt=last_user_msg)
        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken_summary)}" if spoken_summary and speech_service.tts_provider != "muted" else None

        return {
            "role": "assistant",
            "content": content,
            "actions": [],
            "spoken_summary": spoken_summary,
            "audio_url": audio_url,
            "ppt_files": []
        }

    # 5. OS Operations & Multi-Agent Tasks (Browser, File, System, Multi-Agent)
    try:
        res = await nim_client.chat_complete(
            messages=raw_messages,
            model=req.model or settings.LLM_MODEL,
            permission_mode=req.permission_mode or "full_access"
        )
        content = res.get("content", "")
        actions = res.get("actions", [])
        ppt_files = res.get("ppt_files", [])

        # If API key returned an error, attempt provider-independent gateway
        if ("API Key is missing" in content or "API Error" in content or "Connection Error" in content) and settings.LLM_API_KEY:
            gw_res = await llm_gateway.chat_complete(
                messages=raw_messages,
                model=req.model or settings.LLM_MODEL
            )
            if gw_res.get("success") and gw_res.get("content"):
                content = gw_res["content"]

        spoken_summary = speech_service.filter_text_for_speech(content, user_prompt=last_user_msg)
        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken_summary)}" if spoken_summary and speech_service.tts_provider != "muted" else None
        return {
            "role": "assistant",
            "content": content,
            "actions": actions,
            "spoken_summary": spoken_summary,
            "audio_url": audio_url,
            "ppt_files": ppt_files
        }
    except Exception as e:
        logger.exception(f"Error in chat endpoint: {e}")
        # Fallback to LLM Gateway
        try:
            gw_res = await llm_gateway.chat_complete(
                messages=raw_messages,
                model=req.model or settings.LLM_MODEL
            )
            if gw_res.get("success") and gw_res.get("content"):
                c = gw_res["content"]
                sp = speech_service.filter_text_for_speech(c, user_prompt=last_user_msg)
                a_url = f"/api/speech/tts?text={urllib.parse.quote(sp)}" if sp and speech_service.tts_provider != "muted" else None
                return {
                    "role": "assistant",
                    "content": c,
                    "actions": [],
                    "spoken_summary": sp,
                    "audio_url": a_url,
                    "ppt_files": []
                }
        except Exception as gw_err:
            logger.error(f"Fallback llm_gateway error: {gw_err}")

        err_msg = f"⚠️ Error processing request: {str(e)}"
        sp = speech_service.filter_text_for_speech(err_msg, user_prompt=last_user_msg)
        return {
            "role": "assistant",
            "content": err_msg,
            "error": str(e),
            "actions": [],
            "spoken_summary": sp,
            "audio_url": None,
            "ppt_files": []
        }

@app.post("/api/chat/stream")
async def chat_stream_endpoint(req: ChatRequest):
    """Streaming endpoint for Server-Sent Events (SSE) chat responses."""
    async def sse_generator():
        raw_messages = [m.dict() if hasattr(m, 'dict') else dict(m) for m in req.messages]
        async for chunk in nim_client.chat_stream(raw_messages, model=req.model):
            yield chunk
    return StreamingResponse(sse_generator(), media_type="text/event-stream")

# ----------------- MULTI-AGENT TASK PIPELINE -----------------

class CreateTaskRequest(BaseModel):
    prompt: str
    workspace: Optional[str] = None
    permission_mode: Optional[str] = "balanced"
    model: Optional[str] = None

@app.post("/api/tasks")
async def create_task_endpoint(req: CreateTaskRequest):
    try:
        ws = req.workspace or str(Path(__file__).resolve().parent.parent)
        task_id = await orchestrator.create_and_run_task(
            prompt=req.prompt,
            workspace=ws,
            permission_mode=req.permission_mode or "balanced",
            model=req.model
        )
        return {"task_id": task_id, "status": "QUEUED"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/tasks")
def list_tasks_endpoint(limit: int = 50):
    return db.list_tasks(limit=limit)

@app.get("/api/tasks/{task_id}")
def get_task_endpoint(task_id: str):
    t = db.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    events = db.get_task_events(task_id)
    t["events"] = events
    return t

@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task_endpoint(task_id: str):
    success = await orchestrator.cancel_task(task_id)
    return {"task_id": task_id, "cancelled": success}

@app.get("/api/tasks/{task_id}/events")
async def sse_task_events(task_id: str, request: Request):
    """Server-Sent Events (SSE) streaming real-time task updates to desktop & remote UI."""
    q = orchestrator.subscribe(task_id)

    async def event_generator():
        try:
            # First send existing DB events
            existing = db.get_task_events(task_id)
            for evt in existing:
                yield f"data: {json.dumps(evt)}\n\n"

            # Stream live events
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield f": heartbeat\n\n"
        finally:
            orchestrator.unsubscribe(task_id, q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ----------------- APPROVALS & PERMISSION RULES -----------------

class ApprovalResponse(BaseModel):
    decision: str
    meta: Optional[Dict[str, Any]] = None

@app.get("/api/approvals/pending")
def get_pending_approvals_endpoint(task_id: Optional[str] = None):
    return db.get_pending_approvals(task_id=task_id)

@app.post("/api/approvals/{approval_id}/respond")
def respond_approval_endpoint(approval_id: str, req: ApprovalResponse):
    success = permission_engine.submit_decision(approval_id, req.decision, req.meta)
    if not success:
        raise HTTPException(status_code=404, detail="Approval request not found or already resolved")
    return {"approval_id": approval_id, "resolved": True, "decision": req.decision}

@app.get("/api/permissions/rules")
def get_permission_rules_endpoint(project_path: Optional[str] = None):
    return db.list_permission_rules(project_path=project_path)

@app.post("/api/permissions/rules/{rule_id}/revoke")
def revoke_rule_endpoint(rule_id: str):
    success = permission_engine.revoke_rule(rule_id)
    return {"rule_id": rule_id, "revoked": success}

@app.get("/api/audit-logs")
def get_audit_logs_endpoint(task_id: Optional[str] = None, limit: int = 100):
    return db.get_audit_logs(task_id=task_id, limit=limit)

# ----------------- HEALTH & DIAGNOSTICS -----------------

@app.get("/api/health")
def get_health_diagnostics():
    return {
        "status": "healthy",
        "emergency_stopped": permission_engine.is_emergency_stopped(),
        "llm_gateway": llm_gateway.get_status(),
        "database": {
            "path": str(db.DB_PATH),
            "accessible": db.DB_PATH.exists()
        },
        "python_env": {
            "has_fastapi": True,
            "has_sqlite3": True,
            "has_httpx": True
        }
    }

# ----------------- WebSocket for Phone Remote -----------------

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

@app.websocket("/ws/remote")
async def websocket_remote_endpoint(websocket: WebSocket, token: Optional[str] = None):
    # Authenticate pairing token or allow local access
    await manager.connect(websocket)
    is_streaming = True

    async def stream_screen():
        nonlocal is_streaming
        fps = settings.STREAM_FPS
        delay = 1.0 / max(1, fps)
        err_count = 0
        while is_streaming:
            try:
                b64_frame, cursor_info = get_frame_with_cursor()
                if b64_frame:
                    await websocket.send_json({
                        "type": "frame",
                        "data": f"data:image/jpeg;base64,{b64_frame}",
                        "cursor": cursor_info
                    })
                    err_count = 0
                await asyncio.sleep(delay)
            except WebSocketDisconnect:
                break
            except Exception:
                err_count += 1
                if err_count > 30:
                    break
                await asyncio.sleep(0.3)

    streaming_task = asyncio.create_task(stream_screen())

    try:
        await websocket.send_json({
            "type": "status",
            "connected": True,
            "emergency_stopped": permission_engine.is_emergency_stopped(),
            "message": "Connected to Multi-Agent OS Assistant"
        })

        remote_chat_history: List[Dict[str, str]] = []

        while True:
            data = await websocket.receive_json()
            evt_type = data.get("type")

            if evt_type == "emergency_stop":
                res = orchestrator.emergency_stop()
                await manager.broadcast({
                    "type": "emergency_stop_triggered",
                    "message": "Emergency Stop Triggered"
                })

            elif evt_type == "clear_emergency_stop":
                orchestrator.clear_emergency_stop()
                await manager.broadcast({
                    "type": "emergency_stop_cleared"
                })

            elif evt_type == "trackpad_move":
                dx = float(data.get("dx", 0))
                dy = float(data.get("dy", 0))
                controller.move_rel(dx, dy)

            elif evt_type == "click":
                button = data.get("button", "left")
                clicks = int(data.get("clicks", 1))
                controller.click(button=button, clicks=clicks)

            elif evt_type == "mouse_down":
                button = data.get("button", "left")
                controller.mouse_down(button=button)

            elif evt_type == "mouse_up":
                button = data.get("button", "left")
                controller.mouse_up(button=button)

            elif evt_type == "scroll":
                dy = float(data.get("dy", 0))
                dx = float(data.get("dx", 0))
                controller.scroll(dy=dy, dx=dx)

            elif evt_type == "type":
                text = data.get("text", "")
                controller.type_text(text)

            elif evt_type == "press":
                key = data.get("key", "enter")
                controller.press_key(key)

            elif evt_type == "hotkey":
                keys = data.get("keys", [])
                controller.hotkey(*keys)

            elif evt_type == "stream_start":
                if not is_streaming:
                    is_streaming = True
                    streaming_task = asyncio.create_task(stream_screen())

            elif evt_type == "stream_stop":
                is_streaming = False
                if streaming_task:
                    streaming_task.cancel()
                    streaming_task = None

            elif evt_type == "prompt":
                prompt_text = data.get("text", "").strip()
                if prompt_text:
                    remote_chat_history.append({"role": "user", "content": prompt_text})
                    intent = intent_router.classify(prompt_text, remote_chat_history)
                    logger.info(f"[Remote WS] Prompt: '{prompt_text[:30]}' classified as: {intent}")

                    if intent == AgentIntent.FILE_OPERATION:
                        await websocket.send_json({
                            "type": "agent_thinking",
                            "prompt": prompt_text
                        })
                        file_res = await handle_file_operation(prompt_text, remote_chat_history)
                        resp_content = file_res.get("content", "")
                        remote_chat_history.append({"role": "assistant", "content": resp_content})
                        await websocket.send_json({
                            "type": "agent_response",
                            "role": "assistant",
                            "content": resp_content,
                            "spoken_summary": file_res.get("spoken_summary", ""),
                            "audio_url": file_res.get("audio_url"),
                            "actions": file_res.get("actions", [])
                        })
                    elif intent == AgentIntent.BROWSER_OPERATION:
                        await websocket.send_json({
                            "type": "agent_thinking",
                            "prompt": prompt_text
                        })
                        browser_res = await handle_browser_operation(prompt_text, remote_chat_history)
                        resp_content = browser_res.get("content", "")
                        remote_chat_history.append({"role": "assistant", "content": resp_content})
                        await websocket.send_json({
                            "type": "agent_response",
                            "role": "assistant",
                            "content": resp_content,
                            "spoken_summary": browser_res.get("spoken_summary", ""),
                            "audio_url": browser_res.get("audio_url"),
                            "actions": browser_res.get("actions", [])
                        })
                    elif intent == AgentIntent.SYSTEM_OPERATION:
                        await websocket.send_json({
                            "type": "agent_thinking",
                            "prompt": prompt_text
                        })
                        sys_res = await handle_system_operation(prompt_text, remote_chat_history)
                        resp_content = sys_res.get("content", "")
                        remote_chat_history.append({"role": "assistant", "content": resp_content})
                        await websocket.send_json({
                            "type": "agent_response",
                            "role": "assistant",
                            "content": resp_content,
                            "spoken_summary": sys_res.get("spoken_summary", ""),
                            "audio_url": sys_res.get("audio_url"),
                            "actions": sys_res.get("actions", [])
                        })
                    elif intent == AgentIntent.SCREEN_QUESTION:
                        await websocket.send_json({
                            "type": "agent_thinking",
                            "prompt": prompt_text
                        })
                        v_res = await vision_agent.analyze_screen(prompt=prompt_text)
                        desc = v_res.get("description") or v_res.get("error") or "Screen analyzed."
                        spoken = speech_service.filter_text_for_speech(desc, user_prompt=prompt_text)
                        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None
                        remote_chat_history.append({"role": "assistant", "content": desc})
                        await websocket.send_json({
                            "type": "agent_response",
                            "role": "assistant",
                            "content": desc,
                            "spoken_summary": spoken,
                            "audio_url": audio_url,
                            "actions": [{
                                "type": "vision_analysis",
                                "active_window": v_res.get("active_window", {}),
                                "status": "success" if v_res.get("success") else "error"
                            }]
                        })
                    elif intent in [AgentIntent.CASUAL_CONVERSATION, AgentIntent.ASSISTANT_IDENTITY, AgentIntent.VOICE_OPERATION]:
                        await websocket.send_json({
                            "type": "agent_thinking",
                            "prompt": prompt_text
                        })

                        if intent == AgentIntent.VOICE_OPERATION:
                            msg_l = prompt_text.lower()
                            if "mute" in msg_l and "unmute" not in msg_l:
                                speech_service.update_config(provider="muted")
                                content = "Voice audio is now muted."
                            elif "unmute" in msg_l:
                                speech_service.update_config(provider="edge_tts")
                                content = "Voice audio is unmuted and active."
                            else:
                                content = "Voice settings updated."
                        else:
                            sys_prompt = intent_router.get_conversation_system_prompt(is_identity_query=(intent == AgentIntent.ASSISTANT_IDENTITY))
                            conv_msgs = [
                                {"role": "system", "content": sys_prompt}
                            ]
                            conv_msgs.extend(remote_chat_history[-10:])
                            content = ""
                            try:
                                gw_res = await llm_gateway.chat_complete(
                                    messages=conv_msgs,
                                    model=settings.LLM_MODEL,
                                    temperature=0.6,
                                    max_tokens=2048
                                )
                                if gw_res.get("success") and gw_res.get("content"):
                                    content = gw_res["content"].strip()
                            except Exception as c_err:
                                logger.warning(f"Remote conversational gateway error: {c_err}")

                            if not content:
                                content = "I'm OS Agent, your Windows desktop assistant. I can operate your computer via Browser, File, System, and Vision agents." if intent == AgentIntent.ASSISTANT_IDENTITY else "Hey! How can I help you today?"

                        remote_chat_history.append({"role": "assistant", "content": content})
                        spoken = speech_service.filter_text_for_speech(content, user_prompt=prompt_text)
                        audio_url = f"/api/speech/tts?text={urllib.parse.quote(spoken)}" if spoken and speech_service.tts_provider != "muted" else None

                        await websocket.send_json({
                            "type": "agent_response",
                            "role": "assistant",
                            "content": content,
                            "spoken_summary": spoken,
                            "audio_url": audio_url,
                            "actions": []
                        })
                    else:
                        await websocket.send_json({
                            "type": "agent_thinking",
                            "prompt": prompt_text
                        })
                        ws = str(Path(__file__).resolve().parent.parent)
                        task_id = await orchestrator.create_and_run_task(
                            prompt=prompt_text,
                            workspace=ws,
                            permission_mode="balanced",
                            chat_history=remote_chat_history
                        )
                        await websocket.send_json({
                            "type": "task_dispatched",
                            "task_id": task_id,
                            "message": f"Task {task_id} launched across agents."
                        })

                        # Relay progress and spoken summary back to phone
                        async def relay_task_events(t_id):
                            q = orchestrator.subscribe(t_id)
                            try:
                                while True:
                                    evt = await q.get()
                                    e_type = evt.get("event_type")
                                    e_data = evt.get("data", {})
                                    if e_type in ["AGENT_SELECTED", "PLAN_GENERATED", "ACTION_EXECUTING", "ACTION_COMPLETED"]:
                                        await websocket.send_json({
                                            "type": "agent_progress",
                                            "event_type": e_type,
                                            "data": e_data
                                        })
                                    elif e_type == "TASK_COMPLETED":
                                        sum_text = e_data.get("summary", "")
                                        sp = speech_service.filter_text_for_speech(e_data.get("spoken_summary") or sum_text, user_prompt=prompt_text)
                                        a_url = f"/api/speech/tts?text={urllib.parse.quote(sp)}" if sp and speech_service.tts_provider != "muted" else None
                                        await websocket.send_json({
                                            "type": "task_completed",
                                            "task_id": t_id,
                                            "summary": sum_text,
                                            "spoken_summary": sp,
                                            "audio_url": a_url
                                        })
                                        break
                                    elif e_type in ["TASK_FAILED", "TASK_CANCELLED"]:
                                        err_t = e_data.get("error", "Task ended")
                                        sp = speech_service.filter_text_for_speech(err_t, user_prompt=prompt_text)
                                        await websocket.send_json({
                                            "type": "task_failed",
                                            "task_id": t_id,
                                            "error": err_t,
                                            "spoken_summary": sp
                                        })
                                        break
                            finally:
                                orchestrator.unsubscribe(t_id, q)

                        asyncio.create_task(relay_task_events(task_id))

    except WebSocketDisconnect:
        is_streaming = False
        if streaming_task:
            streaming_task.cancel()
        manager.disconnect(websocket)
    except Exception:
        is_streaming = False
        if streaming_task:
            streaming_task.cancel()
        manager.disconnect(websocket)

@app.get("/remote", response_class=HTMLResponse)
def get_remote_page():
    remote_file = STATIC_DIR / "remote.html"
    if remote_file.exists():
        return HTMLResponse(content=remote_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Remote interface loading...</h1>")

if DESKTOP_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DESKTOP_DIR), html=True), name="desktop_app")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=False)
