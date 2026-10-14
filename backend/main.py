import asyncio
import socket
import json
import base64
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import settings, update_env
from screen_capture import get_screenshot_bytes, get_screenshot_base64, get_screen_dimensions, get_frame_with_cursor
from input_controller import controller
from llm_client import nim_client
from ppt_agent import ppt_agent

# New Autonomous OS Agent Modules
import db
from permission_engine import permission_engine
from codex_runtime import codex_runtime
from agy_reviewer import agy_reviewer
from planner import planner
from verifier import verifier
from orchestrator import orchestrator

app = FastAPI(title="FCC-Codex Autonomous OS Agent Backend", version="2.0.0")

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
    nvidia_api_key: Optional[str] = None
    default_model: Optional[str] = None
    stream_fps: Optional[int] = None
    stream_quality: Optional[int] = None
    stream_scale: Optional[float] = None

class ActionRequest(BaseModel):
    action: str
    params: Optional[Dict[str, Any]] = None

@app.get("/api/status")
def get_status():
    w, h = get_screen_dimensions()
    codex_st = codex_runtime.get_status()
    agy_st = agy_reviewer.get_status()
    return {
        "status": "online",
        "ip": get_local_ip(),
        "port": settings.PORT,
        "screen": {"width": w, "height": h},
        "has_api_key": bool(settings.NVIDIA_API_KEY),
        "model": settings.DEFAULT_MODEL,
        "codex": codex_st,
        "agy": agy_st
    }

@app.get("/api/network-info")
def get_network_info():
    ip = get_local_ip()
    port = settings.PORT
    remote_url = f"http://{ip}:{port}/remote"
    return {
        "ip": ip,
        "port": port,
        "remote_url": remote_url
    }

@app.get("/api/settings")
def get_settings():
    key = settings.NVIDIA_API_KEY
    masked_key = f"{key[:7]}...{key[-4:]}" if len(key) > 12 else ("configured" if key else "")
    return {
        "has_api_key": bool(key),
        "masked_key": masked_key,
        "default_model": settings.DEFAULT_MODEL,
        "stream_fps": settings.STREAM_FPS,
        "stream_quality": settings.STREAM_QUALITY,
        "stream_scale": settings.STREAM_SCALE,
        "default_workspace": str(Path(__file__).resolve().parent.parent)
    }

@app.post("/api/settings")
def save_settings(req: SettingsUpdateRequest):
    if req.nvidia_api_key is not None:
        update_env("NVIDIA_API_KEY", req.nvidia_api_key.strip())
    if req.default_model is not None:
        update_env("DEFAULT_MODEL", req.default_model.strip())
    if req.stream_fps is not None:
        update_env("STREAM_FPS", str(req.stream_fps))
    if req.stream_quality is not None:
        update_env("STREAM_QUALITY", str(req.stream_quality))
    if req.stream_scale is not None:
        update_env("STREAM_SCALE", str(req.stream_scale))
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
    act = req.action.lower()
    p = req.params or {}
    try:
        if act == "click":
            x, y = p.get("x"), p.get("y")
            if x is not None and y is not None:
                controller.move_to(int(x), int(y))
            controller.click(p.get("button", "left"), p.get("clicks", 1))
        elif act == "move":
            controller.move_to(int(p.get("x", 0)), int(p.get("y", 0)))
        elif act == "move_rel":
            controller.move_rel(float(p.get("dx", 0)), float(p.get("dy", 0)))
        elif act == "type":
            controller.type_text(p.get("text", ""))
        elif act == "press":
            controller.press_key(p.get("key", "enter"))
        elif act == "hotkey":
            controller.hotkey(*p.get("keys", []))
        elif act == "scroll":
            controller.scroll(float(p.get("dy", 1)), float(p.get("dx", 0)))
        return {"status": "ok", "action": act}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ----------------- PowerPoint Endpoints -----------------
class PptInspectRequest(BaseModel):
    path: str

class PptModifyRequest(BaseModel):
    path: str
    action: str
    title: Optional[str] = "New Slide"
    bullets: Optional[List[str]] = []
    find_text: Optional[str] = None
    replace_text: Optional[str] = None
    slide_num: Optional[int] = None
    open_after: Optional[bool] = True

@app.get("/api/ppt/recent")
def get_recent_ppts(
    query: Optional[str] = None,
    drive: Optional[str] = None,
    limit: int = 20,
    refresh: bool = False
):
    return ppt_agent.find_recent_ppts(query=query, drive=drive, limit=limit, force_refresh=refresh)

@app.post("/api/ppt/inspect")
def inspect_ppt_endpoint(req: PptInspectRequest):
    try:
        return ppt_agent.inspect_ppt(req.path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/ppt/modify")
def modify_ppt_endpoint(req: PptModifyRequest):
    try:
        if req.action == "add_slide":
            res = ppt_agent.add_slide(req.path, req.title or "New Slide", req.bullets or [])
        elif req.action == "replace_text":
            res = ppt_agent.replace_text(req.path, req.find_text or "", req.replace_text or "", req.slide_num)
        elif req.action == "update_slide":
            res = ppt_agent.update_slide(req.path, req.slide_num or 1, title=req.title, bullet_points=req.bullets)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

        if req.open_after:
            ppt_agent.open_in_powerpoint(req.path)

        return {"status": "success", "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ppt/open")
def open_ppt_endpoint(req: PptInspectRequest):
    success = ppt_agent.open_in_powerpoint(req.path)
    return {"status": "opened" if success else "failed"}

# ----------------- Chat Endpoints -----------------
@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    result = await nim_client.chat_complete(
        messages=messages,
        model=req.model,
        permission_mode=req.permission_mode or "full_access"
    )
    return result

@app.post("/api/chat/stream")
async def chat_stream_endpoint(req: ChatRequest):
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    return StreamingResponse(
        nim_client.chat_stream(messages=messages, model=req.model),
        media_type="text/event-stream"
    )

# ----------------- AUTONOMOUS OS AGENT TASK PIPELINE -----------------

class CreateTaskRequest(BaseModel):
    prompt: str
    workspace: Optional[str] = None
    permission_mode: Optional[str] = "full_access"  # full_access, balanced, ask_permission, read_only
    model: Optional[str] = "gpt-5.6-terra"

class ApprovalResponseRequest(BaseModel):
    decision: str  # allow_once, allow_session, allow_project, allow_full_access, deny, cancel
    meta: Optional[Dict[str, Any]] = None

@app.post("/api/tasks")
async def create_task_endpoint(req: CreateTaskRequest):
    ws = req.workspace or str(Path(__file__).resolve().parent.parent)
    perm = req.permission_mode or "full_access"
    task_id = await orchestrator.create_and_run_task(
        prompt=req.prompt,
        workspace=ws,
        permission_mode=perm,
        model=req.model or "gpt-5.6-terra"
    )
    if perm == "full_access":
        permission_engine._full_access_grants[task_id] = True
    return {"task_id": task_id, "status": "QUEUED"}

@app.get("/api/tasks")
def list_tasks_endpoint(limit: int = 50):
    return db.list_tasks(limit=limit)

@app.get("/api/tasks/{task_id}")
def get_task_endpoint(task_id: str):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    events = db.get_task_events(task_id)
    task["events"] = events
    return task

@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task_endpoint(task_id: str):
    success = await orchestrator.cancel_task(task_id)
    return {"task_id": task_id, "cancelled": success}

@app.get("/api/tasks/{task_id}/events")
async def stream_task_events_endpoint(task_id: str, request: Request):
    """Server-Sent Events (SSE) stream for real-time live execution monitoring."""
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    q = orchestrator.subscribe(task_id)

    async def event_generator():
        # First yield past recorded events
        past_events = db.get_task_events(task_id)
        for evt in past_events:
            yield f"data: {json.dumps(evt)}\n\n"

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield f"data: {json.dumps(item)}\n\n"
                    # If task reached terminal state, send done and break
                    if item.get("event_type") in ["TASK_COMPLETED", "TASK_CANCELLED", "TASK_BLOCKED", "TASK_ERROR"]:
                        yield f"data: {json.dumps({'event_type': 'STREAM_END', 'task_id': task_id})}\n\n"
                        break
                except asyncio.TimeoutError:
                    # Keep-alive comment
                    yield ": ping\n\n"
        finally:
            orchestrator.unsubscribe(task_id, q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ----------------- APPROVALS & HUMAN-IN-THE-LOOP -----------------

@app.get("/api/approvals/pending")
def get_pending_approvals_endpoint(task_id: Optional[str] = None):
    return db.get_pending_approvals(task_id=task_id)

@app.post("/api/approvals/{approval_id}/respond")
def respond_approval_endpoint(approval_id: str, req: ApprovalResponseRequest):
    success = permission_engine.submit_decision(
        approval_id=approval_id,
        decision=req.decision,
        meta=req.meta or {}
    )
    if not success:
        raise HTTPException(status_code=400, detail="Approval ID not found or already resolved")
    return {"approval_id": approval_id, "status": "resolved", "decision": req.decision}

# ----------------- PERMISSION RULES & AUDIT -----------------

@app.get("/api/permissions/rules")
def list_permission_rules_endpoint(project_path: Optional[str] = None):
    return db.list_permission_rules(project_path=project_path, include_revoked=False)

@app.post("/api/permissions/rules/{rule_id}/revoke")
def revoke_permission_rule_endpoint(rule_id: str):
    success = permission_engine.revoke_rule(rule_id)
    return {"rule_id": rule_id, "revoked": success}

@app.get("/api/permissions/active")
def get_active_grants_endpoint(task_id: str, workspace: Optional[str] = None):
    ws = workspace or str(Path(__file__).resolve().parent.parent)
    return permission_engine.get_active_grants(task_id, ws)

@app.get("/api/audit-logs")
def get_audit_logs_endpoint(task_id: Optional[str] = None, limit: int = 100):
    return db.get_audit_logs(task_id=task_id, limit=limit)

# ----------------- HEALTH & DIAGNOSTICS -----------------

@app.get("/api/health")
def get_health_diagnostics():
    codex_info = codex_runtime.get_status()
    agy_info = agy_reviewer.get_status()
    return {
        "status": "healthy",
        "codex": codex_info,
        "agy": agy_info,
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

# Backward compatible codex guide endpoint
@app.get("/api/codex/status")
def get_codex_status():
    return codex_runtime.get_status()

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
async def websocket_remote_endpoint(websocket: WebSocket):
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
            except Exception as e:
                err_count += 1
                if err_count > 30:
                    break
                await asyncio.sleep(0.3)

    streaming_task = asyncio.create_task(stream_screen())

    try:
        await websocket.send_json({
            "type": "status",
            "connected": True,
            "message": "Connected to FCC-Codex OS Agent Desktop"
        })

        while True:
            data = await websocket.receive_json()
            evt_type = data.get("type")

            if evt_type == "trackpad_move":
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
                prompt_text = data.get("text", "")
                if prompt_text:
                    await websocket.send_json({
                        "type": "agent_thinking",
                        "prompt": prompt_text
                    })
                    # Trigger autonomous task
                    ws = str(Path(__file__).resolve().parent.parent)
                    task_id = await orchestrator.create_and_run_task(
                        prompt=prompt_text,
                        workspace=ws,
                        permission_mode="balanced"
                    )
                    await websocket.send_json({
                        "type": "task_dispatched",
                        "task_id": task_id,
                        "message": f"Autonomous task {task_id} launched in background."
                    })

    except WebSocketDisconnect:
        is_streaming = False
        if streaming_task:
            streaming_task.cancel()
        manager.disconnect(websocket)
    except Exception as e:
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
