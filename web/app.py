"""Minecraft AI Chat — web UI + OpenAI-compatible AI control panel."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ai_agent import chat_with_tools
from mc_server import manager

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CONFIG_PATH = ROOT.parent / "config" / "settings.json"

app = FastAPI(title="Minecraft AI Chat", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# --------------------------------------------------------------------------- models
class SettingsModel(BaseModel):
    api_key: str = ""
    base_url: str = "https://api.x.ai/v1"
    model: str = "grok-4.5"
    system_note: str = ""


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    # optional per-request overrides
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


class CommandRequest(BaseModel):
    command: str


class LogsRequest(BaseModel):
    lines: int = Field(default=100, ge=1, le=2000)


# --------------------------------------------------------------------------- settings
def load_settings() -> SettingsModel:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return SettingsModel(**data)
        except Exception:
            pass
    # env fallbacks
    return SettingsModel(
        api_key=os.environ.get("XAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or "",
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.x.ai/v1"),
        model=os.environ.get("OPENAI_MODEL", "grok-4.5"),
    )


def save_settings(settings: SettingsModel) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        settings.model_dump_json(indent=2),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- routes
@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "service": "minecraft-ai-chat"}


@app.get("/api/settings")
async def get_settings() -> dict:
    s = load_settings()
    # never return full key — mask it
    masked = ""
    if s.api_key:
        if len(s.api_key) <= 8:
            masked = "********"
        else:
            masked = s.api_key[:4] + "…" + s.api_key[-4:]
    return {
        "api_key_set": bool(s.api_key),
        "api_key_masked": masked,
        "base_url": s.base_url,
        "model": s.model,
        "system_note": s.system_note,
    }


@app.post("/api/settings")
async def post_settings(body: SettingsModel) -> dict:
    current = load_settings()
    # empty api_key means "keep existing"
    if not body.api_key and current.api_key:
        body.api_key = current.api_key
    save_settings(body)
    return {"ok": True, "message": "Settings saved"}


@app.get("/api/server/status")
async def server_status() -> dict:
    return manager.status()


@app.post("/api/server/start")
async def server_start() -> dict:
    return manager.start()


@app.post("/api/server/stop")
async def server_stop(force: bool = False) -> dict:
    return manager.stop(force=force)


@app.post("/api/server/command")
async def server_command(body: CommandRequest) -> dict:
    result = manager.send_command(body.command)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@app.get("/api/server/logs")
async def server_logs(lines: int = 100) -> dict:
    lines = max(1, min(lines, 2000))
    return {"lines": manager.get_logs(lines)}


@app.post("/api/chat")
async def chat(body: ChatRequest) -> dict:
    settings = load_settings()
    api_key = body.api_key or settings.api_key
    base_url = body.base_url or settings.base_url
    model = body.model or settings.model

    messages: List[Dict[str, Any]] = [
        {"role": m.role, "content": m.content} for m in body.messages if m.role in ("user", "assistant")
    ]
    if settings.system_note:
        # inject as first user context if present
        messages = [
            {
                "role": "user",
                "content": f"[Operator note]\n{settings.system_note}",
            },
            {
                "role": "assistant",
                "content": "Understood. I will keep that note in mind.",
            },
            *messages,
        ]

    try:
        # run blocking OpenAI + tools in a worker thread
        result = await asyncio.to_thread(
            chat_with_tools,
            messages=messages,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket) -> None:
    await websocket.accept()
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
    loop = asyncio.get_running_loop()

    def on_line(line: str) -> None:
        def _put() -> None:
            try:
                queue.put_nowait(line)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except Exception:
                    pass
                try:
                    queue.put_nowait(line)
                except Exception:
                    pass

        loop.call_soon_threadsafe(_put)

    manager.add_log_listener(on_line)
    # send recent history
    for line in manager.get_logs(200):
        await websocket.send_text(line)
    try:
        while True:
            # also allow client pings / ignore inbound
            try:
                line = await asyncio.wait_for(queue.get(), timeout=20.0)
                await websocket.send_text(line)
            except asyncio.TimeoutError:
                await websocket.send_text("__ping__")
            # drain client messages without blocking forever
            try:
                while True:
                    msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.01)
                    if msg == "close":
                        raise WebSocketDisconnect()
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        manager.remove_log_listener(on_line)


def detect_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def read_minecraft_port() -> int:
    """Read game port from server.properties (default 25565)."""
    props = Path(__file__).resolve().parent.parent / "server" / "server.properties"
    try:
        for line in props.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("server-port="):
                return int(line.split("=", 1)[1].strip())
    except Exception:
        pass
    return 25565


@app.get("/api/info")
async def info() -> dict:
    port = int(os.environ.get("WEB_PORT", "8080"))
    ip = detect_local_ip()
    mc_port = read_minecraft_port()
    return {
        "web_url": f"http://{ip}:{port}",
        "local_url": f"http://127.0.0.1:{port}",
        "ip": ip,
        "port": port,
        "minecraft_port": mc_port,
        "minecraft_address": f"{ip}:{mc_port}",
        "minecraft_address_local": f"127.0.0.1:{mc_port}",
        "minecraft_version": "26.2",
        "loader": "Fabric",
        "server": manager.status(),
    }


@app.on_event("startup")
async def on_startup() -> None:
    # Optionally auto-start MC if env says so
    auto = os.environ.get("AUTO_START_MC", "1").lower() not in ("0", "false", "no")
    if auto:
        result = manager.start()
        print(f"[web] Minecraft start: {result}", flush=True)
