from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.container import get_telemetry as get_telemetry_service
from app.schemas.telemetry import TelemetryResponse

router = APIRouter()


def _snap_to_response(snap) -> TelemetryResponse:
    return TelemetryResponse(
        lat=snap.lat,
        lon=snap.lon,
        alt=snap.alt,
        battery=snap.battery,
        status=snap.status,
        speed=snap.speed,
        armed=snap.armed,
        mode=snap.mode,
        heading=snap.heading,
        source=snap.source,
        note=snap.note,
    )


@router.get("", response_model=TelemetryResponse)
def get_telemetry() -> TelemetryResponse:
    return _snap_to_response(get_telemetry_service().get_snapshot())


@router.websocket("/ws")
async def telemetry_ws(websocket: WebSocket) -> None:
    """
    WebSocket-поток телеметрии ~5 Гц.
    Подключение: ws://host/telemetry/ws
    Клиент получает JSON TelemetryResponse каждые 200 мс.
    """
    await websocket.accept()
    svc = get_telemetry_service()
    try:
        while True:
            data = _snap_to_response(svc.get_snapshot()).model_dump()
            await websocket.send_json(data)
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
