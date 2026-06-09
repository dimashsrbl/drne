from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.container import get_avoidance, get_vision

router = APIRouter()


# ------------------------------------------------------------------
# Схемы
# ------------------------------------------------------------------

class CameraStartRequest(BaseModel):
    camera_index: int = Field(default=0, ge=0, le=10)


class AvoidanceStartRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt: float = Field(ge=1, le=500, description="Высота AGL (м)")


class VisionStatusResponse(BaseModel):
    running: bool
    camera_index: int
    fps: float
    error: str | None
    obstacle_detected: bool
    obstacle_area_ratio: float
    obstacle_offset_x: float
    obstacle_offset_y: float
    obstacle_high_threat: bool      # YOLO: человек/машина/etc
    obstacle_primary_class: str     # YOLO: класс главного объекта ("person", "car", ...)
    obstacle_track_id: int | None   # ByteTrack ID главного препятствия (None если нет/Canny)
    frame_b64: str                  # JPEG base64, пустая строка если камера не запущена
    backend: str                    # "yolo" / "yolo-int8" / "yolo-onnx" / "canny" / "none"


class AvoidanceStatusResponse(BaseModel):
    active: bool
    phase: str
    target_lat: float | None
    target_lon: float | None
    target_alt: float | None
    log: list[str]


# ------------------------------------------------------------------
# Камера / Vision
# ------------------------------------------------------------------

@router.get("/stream")
def vision_stream(vision=Depends(get_vision)) -> StreamingResponse:
    """
    MJPEG live stream. Используй прямо в браузере:
      <img src="/api/vision/stream">
    Работает пока камера запущена (/vision/camera/start).
    """
    return StreamingResponse(
        vision.mjpeg_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.post("/camera/start")
def camera_start(req: CameraStartRequest = CameraStartRequest(), vision=Depends(get_vision)):
    vision.start(camera_index=req.camera_index)
    return {"ok": True, "detail": f"Камера {req.camera_index} запущена"}


@router.post("/camera/stop")
def camera_stop(vision=Depends(get_vision)):
    vision.stop()
    return {"ok": True, "detail": "Камера остановлена"}


@router.get("/status", response_model=VisionStatusResponse)
def vision_status(vision=Depends(get_vision)):
    snap = vision.get_snapshot()
    # ID трекера для основного (крупнейшего) препятствия
    primary_track_id: int | None = None
    if snap.obstacle.objects:
        main_obj = max(snap.obstacle.objects, key=lambda o: o.area)
        primary_track_id = main_obj.track_id

    return VisionStatusResponse(
        running=snap.running,
        camera_index=snap.camera_index,
        fps=snap.fps,
        error=snap.error,
        obstacle_detected=snap.obstacle.detected,
        obstacle_area_ratio=snap.obstacle.area_ratio,
        obstacle_offset_x=snap.obstacle.offset_x,
        obstacle_offset_y=snap.obstacle.offset_y,
        obstacle_high_threat=snap.obstacle.high_threat,
        obstacle_primary_class=snap.obstacle.primary_class,
        obstacle_track_id=primary_track_id,
        frame_b64=snap.frame_b64,
        backend=snap.backend,
    )


# ------------------------------------------------------------------
# Облёт препятствий
# ------------------------------------------------------------------

@router.post("/avoidance/start")
def avoidance_start(req: AvoidanceStartRequest, avoidance=Depends(get_avoidance)):
    try:
        avoidance.start(req.lat, req.lon, req.alt)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "detail": "Облёт препятствий запущен"}


@router.post("/avoidance/stop")
def avoidance_stop(avoidance=Depends(get_avoidance)):
    avoidance.stop()
    return {"ok": True, "detail": "Облёт остановлен"}


@router.get("/avoidance/status", response_model=AvoidanceStatusResponse)
def avoidance_status(avoidance=Depends(get_avoidance)):
    s = avoidance.get_state()
    return AvoidanceStatusResponse(
        active=s.active,
        phase=s.phase,
        target_lat=s.target_lat,
        target_lon=s.target_lon,
        target_alt=s.target_alt,
        log=s.log,
    )
