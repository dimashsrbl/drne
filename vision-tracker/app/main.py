"""
Vision Tracker — отдельный сервис (порт 8001).
Камера + YOLO (если доступен) + подсказки «куда сдвинуть кадр» для удержания цели в центре.
/UI — демо со стрелками. Позже: PTZ / интеграция с mission backend.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.pipeline import TrackerSnapshot, VisionPipeline

log = logging.getLogger("vision.main")


def _load_dotenv() -> None:
    """Подхватывает vision-tracker/.env (логин камеры и т.д.), не перетирает уже выставленные переменные."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=False)


class AimHintModel(BaseModel):
    horizontal: str  # left | right | center
    vertical: str  # up | down | center


class TargetState(BaseModel):
    track_id: int | None = None
    cx: float = Field(0.0, description="центр объекта: -1 слева … +1 справа от центра кадра")
    cy: float = Field(0.0, description="центр объекта: -1 выше … +1 ниже центра кадра")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    lost: bool = True
    backend: str = Field("stub", description="stub | yolo")
    hint: AimHintModel | None = None
    fps: float = 0.0
    frame_w: int = 0
    frame_h: int = 0
    cls_name: str | None = None
    camera_status: str = Field("", description="ok | no_device | no_frames | init")
    target_locked: bool = False
    lock_note: str = Field("", description="ok | none_in_center | lost_track | no_model | …")


class MonitorStartRequest(BaseModel):
    classes: list[str] | None = None
    min_conf: float | None = Field(default=None, ge=0.01, le=1.0)
    cooldown_s: float | None = Field(default=None, ge=0.0, le=3600.0)
    duration_hours: float | None = Field(default=None, ge=0.0, le=72.0)


class MonitorStatus(BaseModel):
    enabled: bool
    started_at: float = 0.0
    stop_at: float | None = None
    classes: list[str] = Field(default_factory=list)
    min_conf: float = 0.0
    cooldown_s: float = 0.0
    events_written: int = 0
    log_path: str = ""


def _to_model(s: TrackerSnapshot) -> TargetState:
    hint = None
    if s.hint is not None:
        hint = AimHintModel(horizontal=s.hint.horizontal, vertical=s.hint.vertical)
    return TargetState(
        track_id=s.track_id,
        cx=s.cx,
        cy=s.cy,
        confidence=s.confidence,
        lost=s.lost,
        backend=s.backend,
        hint=hint,
        fps=s.fps,
        frame_w=s.frame_w,
        frame_h=s.frame_h,
        cls_name=s.cls_name,
        camera_status=s.camera_status,
        target_locked=s.target_locked,
        lock_note=s.lock_note,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _load_dotenv()
    pipeline = VisionPipeline.from_env()
    pipeline.start()
    app.state.pipeline = pipeline
    log.info("Vision pipeline started")
    yield
    pipeline.stop()
    log.info("Vision pipeline stopped")


app = FastAPI(title="Vision Tracker", version="0.2.0", lifespan=lifespan)


@app.post("/lock")
def lock_target(request: Request) -> dict[str, str]:
    """Зафиксировать объект, который сейчас в центре кадра (следующий кадр в потоке)."""
    p: VisionPipeline = request.app.state.pipeline
    p.request_lock_center_target()
    return {"ok": "true", "message": "lock requested"}


@app.post("/unlock")
def unlock_target(request: Request) -> dict[str, str]:
    p: VisionPipeline = request.app.state.pipeline
    p.request_unlock_target()
    return {"ok": "true", "message": "unlocked"}


@app.post("/monitor/start", response_model=MonitorStatus)
def monitor_start(request: Request, body: MonitorStartRequest) -> MonitorStatus:
    p: VisionPipeline = request.app.state.pipeline
    st = p.start_monitor(
        classes=body.classes,
        min_conf=body.min_conf,
        cooldown_s=body.cooldown_s,
        duration_s=(body.duration_hours * 3600.0 if body.duration_hours is not None else None),
    )
    return MonitorStatus(**st)


@app.post("/monitor/stop", response_model=MonitorStatus)
def monitor_stop(request: Request) -> MonitorStatus:
    p: VisionPipeline = request.app.state.pipeline
    return MonitorStatus(**p.stop_monitor())


@app.get("/monitor/status", response_model=MonitorStatus)
def monitor_status(request: Request) -> MonitorStatus:
    p: VisionPipeline = request.app.state.pipeline
    return MonitorStatus(**p.monitor_status())


@app.get("/monitor/logs")
def monitor_logs(request: Request, limit: int = 500) -> list[dict]:
    p: VisionPipeline = request.app.state.pipeline
    return p.read_monitor_logs(limit=limit)


@app.get("/monitor/summary")
def monitor_summary(request: Request, hours: float | None = None) -> dict:
    p: VisionPipeline = request.app.state.pipeline
    return p.monitor_summary(hours=hours)


def _event_log_path() -> str:
    p = (os.environ.get("VISION_EVENT_LOG_PATH") or "").strip()
    if p.lower() in ("", "0", "false", "off", "none"):
        return ""
    return p


@app.get("/events/recent")
def events_recent(limit: int = 200) -> list[dict]:
    """Последние записи из JSONL-журнала событий (см. VISION_EVENT_LOG_PATH)."""
    path = _event_log_path()
    if not path or not os.path.isfile(path):
        return []
    n = max(1, min(int(limit), 2000))
    lines: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
    except OSError:
        return []
    tail = lines[-n:]
    out: list[dict] = []
    for line in tail:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/target", response_model=TargetState)
def target(request: Request) -> TargetState:
    p: VisionPipeline = request.app.state.pipeline
    return _to_model(p.snapshot())


@app.get("/snapshot.jpg")
def snapshot_jpg(request: Request) -> Response:
    """Один JPEG кадра (превью с перекрестьем и bbox)."""
    p: VisionPipeline = request.app.state.pipeline
    data = p.get_preview_jpeg()
    if not data:
        return Response(status_code=503, content=b"no preview yet")
    return Response(content=data, media_type="image/jpeg")


@app.get("/stream")
async def mjpeg_stream(request: Request) -> StreamingResponse:
    """MJPEG для <img src=\"/stream\"> в браузере."""

    p: VisionPipeline = request.app.state.pipeline

    async def frames() -> AsyncIterator[bytes]:
        seq = -1
        while True:
            if await request.is_disconnected():
                break
            data, seq = await asyncio.to_thread(p.wait_preview_update, seq, 0.35)
            if not data:
                await asyncio.sleep(0.02)
                continue
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + data
                + b"\r\n"
            )

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Connection": "close",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/ui", response_class=HTMLResponse)
def ui() -> str:
    """Простая страница: стрелки показывают, куда смещать камеру, чтобы объект был в центре."""
    return _HTML_UI


_HTML_UI = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Vision — удержание в центре</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; background: #111; color: #eee; margin: 0; min-height: 100vh;
      display: flex; flex-direction: column; align-items: center; padding: 0.75rem 0 1.5rem; gap: 0.5rem; }
    h1 { font-size: 0.95rem; font-weight: 600; opacity: 0.9; margin: 0; text-align: center; max-width: 28rem; }
    .shell { position: relative; width: min(96vw, 960px); border-radius: 10px; border: 1px solid #333;
      overflow: hidden; background: #000; }
    .shell img { display: block; width: 100%; height: auto; vertical-align: bottom; }
    .ovl { position: absolute; inset: 0; pointer-events: none; }
    .arrow { position: absolute; display: flex; align-items: center; justify-content: center;
      font-size: clamp(2rem, 8vw, 3.25rem); color: #eef; opacity: 0.22; transition: opacity 0.1s;
      text-shadow: 0 0 2px #000; user-select: none; }
    .arrow.on { opacity: 1; text-shadow: 0 0 14px rgba(120,200,255,0.95), 0 0 4px #000; }
    .arrow.up { top: 4%; left: 50%; transform: translateX(-50%); }
    .arrow.down { bottom: 4%; left: 50%; transform: translateX(-50%); }
    .arrow.left { left: 4%; top: 50%; transform: translateY(-50%); }
    .arrow.right { right: 4%; top: 50%; transform: translateY(-50%); }
    .center-dot { position: absolute; left: 50%; top: 50%; width: 10px; height: 10px; margin: -5px 0 0 -5px;
      border-radius: 50%; background: rgba(255,255,255,0.25); border: 1px solid rgba(255,255,255,0.5); }
    .bar { display: flex; gap: 0.5rem; flex-wrap: wrap; justify-content: center; max-width: min(96vw, 960px); }
    .bar button { padding: 0.45rem 0.85rem; border-radius: 8px; border: 1px solid #444; background: #2a2a2a;
      color: #eee; cursor: pointer; font-size: 0.875rem; }
    .bar button:hover { background: #353535; }
    .bar button:active { transform: scale(0.98); }
    .stats { font-size: 0.8rem; opacity: 0.8; text-align: center; max-width: min(96vw, 960px); line-height: 1.45; padding: 0 0.5rem; }
    .lost { color: #f88; }
    .ok { color: #9d9; }
  </style>
</head>
<body>
  <h1>Стрелки — куда сдвигать камеру. Оранжевая рамка — зафиксированная цель (только этот класс).</h1>
  <div class="shell">
    <img src="/stream" alt="камера"/>
    <div class="ovl">
      <div class="arrow up" id="a-up">▲</div>
      <div class="arrow down" id="a-down">▼</div>
      <div class="arrow left" id="a-left">◀</div>
      <div class="arrow right" id="a-right">▶</div>
      <div class="center-dot"></div>
    </div>
  </div>
  <div class="bar">
    <button type="button" id="btn-lock">Зафиксировать цель в центре</button>
    <button type="button" id="btn-unlock">Снять фиксацию</button>
  </div>
  <p class="stats" id="stats">загрузка…</p>
  <script>
    const ids = { up: 'a-up', down: 'a-down', left: 'a-left', right: 'a-right' };
    function setArrow(name, on) {
      const el = document.getElementById(ids[name]);
      if (el) el.classList.toggle('on', on);
    }
    function clearArrows() {
      Object.keys(ids).forEach(k => setArrow(k, false));
    }
    document.getElementById('btn-lock').addEventListener('click', async () => {
      await fetch('/lock', { method: 'POST' });
    });
    document.getElementById('btn-unlock').addEventListener('click', async () => {
      await fetch('/unlock', { method: 'POST' });
    });
    async function tick() {
      try {
        const r = await fetch('/target');
        const j = await r.json();
        clearArrows();
        const st = document.getElementById('stats');
        if (j.camera_status && j.camera_status !== 'ok') {
          st.className = 'stats lost';
          st.textContent = 'Камера: ' + j.camera_status + ' · backend=' + j.backend +
            ' · VISION_VIDEO_SOURCE / VISION_CAMERA_FALLBACK';
          return;
        }
        let prefix = '';
        if (j.target_locked) prefix = '[LOCK] ';
        if (j.lock_note) prefix += '(' + j.lock_note + ') ';
        if (j.lost) {
          st.className = 'stats lost';
          st.textContent = prefix + 'Цель не найдена · backend=' + j.backend +
            ' · ' + (j.frame_w || 0) + '×' + (j.frame_h || 0);
          return;
        }
        if (j.hint) {
          if (j.hint.vertical === 'up') setArrow('up', true);
          if (j.hint.vertical === 'down') setArrow('down', true);
          if (j.hint.horizontal === 'left') setArrow('left', true);
          if (j.hint.horizontal === 'right') setArrow('right', true);
        }
        st.className = 'stats ok';
        st.textContent = prefix +
          'cx=' + j.cx.toFixed(2) + ' cy=' + j.cy.toFixed(2) +
          ' · conf=' + j.confidence.toFixed(2) +
          (j.cls_name ? ' · ' + j.cls_name : '') +
          (j.track_id != null ? ' · tid=' + j.track_id : '') +
          ' · fps≈' + j.fps.toFixed(0) +
          ' · ' + j.backend;
      } catch (e) {
        document.getElementById('stats').textContent = 'Ошибка: ' + e;
      }
    }
    setInterval(tick, 100);
    tick();
  </script>
</body>
</html>"""


@app.get("/")
def root() -> RedirectResponse:
    """Открытие `127.0.0.1:8001/` ведёт сразу в интерфейс."""
    return RedirectResponse(url="/ui", status_code=302)


@app.get("/meta")
def meta() -> dict[str, str]:
    """JSON со списком эндпоинтов (для скриптов / отладки)."""
    return {
        "service": "vision-tracker",
        "ui": "/ui",
        "target": "/target",
        "stream": "/stream",
        "snapshot": "/snapshot.jpg",
        "lock": "/lock",
        "unlock": "/unlock",
        "monitor_start": "/monitor/start",
        "monitor_stop": "/monitor/stop",
        "monitor_status": "/monitor/status",
        "monitor_logs": "/monitor/logs",
        "monitor_summary": "/monitor/summary",
        "events_recent": "/events/recent",
        "docs": "/docs",
    }
