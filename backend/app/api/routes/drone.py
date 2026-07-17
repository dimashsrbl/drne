from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.container import get_drone
from app.services.bob57_bridge import Bob57BridgeAdapter
from app.services.drone_types import TelemetrySnapshot
from app.schemas.drone import (
    BridgeAckRequest,
    BridgeCommandResponse,
    BridgeTelemetryRequest,
    CommandResponse,
    DroneProfileResponse,
    GotoRequest,
    ManualControlRequest,
    SafetyGateResponse,
    SetHomeRequest,
    SetModeRequest,
    TakeoffRequest,
)


class SimWindRequest(BaseModel):
    speed_ms: float = 0.0
    direction_deg: float = 0.0


router = APIRouter()


def _profile_response() -> DroneProfileResponse:
    caps = get_drone().get_capabilities()
    return DroneProfileResponse(
        profile=caps.profile,
        label=caps.label,
        supports_missions=caps.supports_missions,
        supports_manual_control=caps.supports_manual_control,
        supports_direct_commands=caps.supports_direct_commands,
        supports_video=caps.supports_video,
        video_url=caps.video_url,
        warnings=list(caps.warnings),
        safety_gates=[SafetyGateResponse(id=g.id, title=g.title, level=g.level) for g in caps.safety_gates],
    )


def _require_bob57_bridge() -> Bob57BridgeAdapter:
    drone = get_drone()
    if not isinstance(drone, Bob57BridgeAdapter):
        raise HTTPException(status_code=409, detail="Текущий backend profile не является BOB57 bridge")
    return drone


@router.get("/profile", response_model=DroneProfileResponse)
def profile() -> DroneProfileResponse:
    return _profile_response()


@router.get("/link-debug")
def link_debug(seconds: float = 3.0) -> dict:
    """Диагностика MAVLink: baud + счётчики типов сообщений за N секунд."""
    drone = get_drone()
    fn = getattr(drone, "link_debug", None)
    if fn is None:
        raise HTTPException(status_code=409, detail="link-debug доступен только для ArduPilot профиля")
    try:
        return fn(seconds=max(0.5, min(float(seconds), 10.0)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/arm", response_model=CommandResponse)
def arm(force: bool | None = None) -> CommandResponse:
    """ARM. force=true — param2=21196 (стенд без GPS, без пропеллеров)."""
    try:
        drone = get_drone()
        fn = getattr(drone, "arm")
        if force is None:
            fn()
        else:
            try:
                fn(force=force)
            except TypeError:
                fn()
        return CommandResponse(ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/disarm", response_model=CommandResponse)
def disarm() -> CommandResponse:
    try:
        get_drone().disarm()
        return CommandResponse(ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/takeoff", response_model=CommandResponse)
def takeoff(body: TakeoffRequest) -> CommandResponse:
    try:
        get_drone().takeoff(body.altitude, no_gps=body.no_gps)
        return CommandResponse(ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/land", response_model=CommandResponse)
def land() -> CommandResponse:
    try:
        get_drone().land()
        return CommandResponse(ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/goto", response_model=CommandResponse)
def goto(body: GotoRequest) -> CommandResponse:
    try:
        get_drone().goto(body.lat, body.lon, body.alt)
        return CommandResponse(ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/return-home", response_model=CommandResponse)
def return_home() -> CommandResponse:
    try:
        get_drone().return_home()
        return CommandResponse(ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/manual-control", response_model=CommandResponse)
def manual_control(body: ManualControlRequest) -> CommandResponse:
    try:
        get_drone().manual_control(body.pitch, body.roll, body.thrust, body.yaw)
        return CommandResponse(ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/home", response_model=CommandResponse)
def set_home(body: SetHomeRequest) -> CommandResponse:
    try:
        get_drone().set_home_global(body.lat, body.lon, body.alt)
        return CommandResponse(ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/flight-mode", response_model=CommandResponse)
def flight_mode(body: SetModeRequest) -> CommandResponse:
    try:
        get_drone().set_flight_mode(body.mode.strip().upper())
        return CommandResponse(ok=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/sim/wind", response_model=CommandResponse)
def sim_wind(body: SimWindRequest) -> CommandResponse:
    """
    No-op в реальном режиме — ветер применяется только в 3D-симуляторе на фронте.
    При профиле ardupilot SITL (ArduPilot) можно расширить командой в симулятор.
    """
    return CommandResponse(ok=True, detail=f"wind: {body.speed_ms} m/s @ {body.direction_deg}° (no-op on real hw)")


@router.post("/bridge/telemetry", response_model=CommandResponse)
def bridge_telemetry(body: BridgeTelemetryRequest) -> CommandResponse:
    bridge = _require_bob57_bridge()
    bridge.ingest_telemetry(
        TelemetrySnapshot(
            lat=body.lat,
            lon=body.lon,
            alt=body.alt,
            battery=body.battery,
            status=body.status,
            speed=body.speed,
            armed=body.armed,
            mode=body.mode,
            heading=body.heading,
            source=body.source or "bob57-bridge",
            note=body.note,
        )
    )
    return CommandResponse(ok=True, detail="telemetry ingested")


@router.post("/bridge/ping", response_model=CommandResponse)
def bridge_ping() -> CommandResponse:
    bridge = _require_bob57_bridge()
    bridge.bridge_ping()
    return CommandResponse(ok=True, detail="bridge online")


@router.get("/bridge/commands", response_model=list[BridgeCommandResponse])
def bridge_commands(limit: int = 20) -> list[BridgeCommandResponse]:
    bridge = _require_bob57_bridge()
    cmds = bridge.get_pending_commands(limit=max(1, min(limit, 100)))
    return [
        BridgeCommandResponse(
            id=cmd.id,
            command=cmd.command,
            params=cmd.params,
            created_at=cmd.created_at,
            status=cmd.status,
        )
        for cmd in cmds
    ]


@router.post("/bridge/ack", response_model=CommandResponse)
def bridge_ack(body: BridgeAckRequest) -> CommandResponse:
    bridge = _require_bob57_bridge()
    bridge.ack_command(body.id, body.status, body.note)
    return CommandResponse(ok=True, detail="command acknowledged")

