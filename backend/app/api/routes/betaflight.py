from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import settings
from app.core.container import get_betaflight_runner
from app.schemas.betaflight import (
    BetaflightCheckResponse,
    BetaflightEmergencyLandRequest,
    BetaflightSequenceStartRequest,
    BetaflightSequenceStatusResponse,
    BetaflightTrackStartRequest,
)
from app.services.betaflight_control import BetaflightRcRunner
from app.services.vision_tracker_client import fetch_target, vision_health_ok

router = APIRouter()


def _status_response(runner: BetaflightRcRunner) -> BetaflightSequenceStatusResponse:
    state = runner.get_state()
    return BetaflightSequenceStatusResponse(
        status=state.status,
        current_step=state.current_step,
        total_steps=state.total_steps,
        current_action=state.current_action,
        elapsed_s=round(state.elapsed_s, 2),
        error=state.error,
        port=state.port,
        current_channels=state.current_channels,
        current_alt_m=state.current_alt_m,
        target_alt_m=state.target_alt_m,
    )


@router.get("/check", response_model=BetaflightCheckResponse)
def check(
    port: str | None = Query(default=None),
    baud: int | None = Query(default=None, ge=9600, le=1000000),
    runner: BetaflightRcRunner = Depends(get_betaflight_runner),
) -> BetaflightCheckResponse:
    result = runner.check(port=port, baud=baud)
    return BetaflightCheckResponse(
        ok=bool(result.get("ok")),
        detail=str(result.get("detail") or ""),
        port=str(result.get("port") or port or settings.betaflight_port),
        variant=result.get("variant") if isinstance(result.get("variant"), str) else None,
        version=result.get("version") if isinstance(result.get("version"), str) else None,
        status=result.get("status") if isinstance(result.get("status"), dict) else None,
    )


@router.post("/sequence/start", response_model=BetaflightSequenceStatusResponse)
def start_sequence(
    body: BetaflightSequenceStartRequest,
    runner: BetaflightRcRunner = Depends(get_betaflight_runner),
) -> BetaflightSequenceStatusResponse:
    try:
        runner.start(body)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _status_response(runner)


@router.post("/sequence/stop", response_model=BetaflightSequenceStatusResponse)
def stop_sequence(runner: BetaflightRcRunner = Depends(get_betaflight_runner)) -> BetaflightSequenceStatusResponse:
    runner.stop()
    return _status_response(runner)


@router.post("/sequence/emergency-land", response_model=BetaflightSequenceStatusResponse)
def emergency_land(
    body: BetaflightEmergencyLandRequest | None = None,
    runner: BetaflightRcRunner = Depends(get_betaflight_runner),
) -> BetaflightSequenceStatusResponse:
    req = body or BetaflightEmergencyLandRequest()
    try:
        runner.emergency_land(
            port=req.port,
            baud=req.baud,
            seconds=req.seconds,
            throttle_us=req.throttle_us,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _status_response(runner)


@router.post("/sequence/heartbeat")
def sequence_heartbeat(runner: BetaflightRcRunner = Depends(get_betaflight_runner)) -> dict[str, str]:
    runner.touch_client_heartbeat()
    return {"ok": "true"}


@router.get("/sequence/status", response_model=BetaflightSequenceStatusResponse)
def sequence_status(runner: BetaflightRcRunner = Depends(get_betaflight_runner)) -> BetaflightSequenceStatusResponse:
    return _status_response(runner)


@router.get("/vision/check")
def vision_check(
    vision_url: str | None = Query(default=None),
) -> dict[str, object]:
    base = (vision_url or settings.vision_tracker_url).rstrip("/")
    ok = vision_health_ok(base)
    snap = fetch_target(base) if ok else None
    return {
        "ok": ok and snap is not None and snap.camera_status in ("ok", "init", ""),
        "vision_url": base,
        "camera_status": snap.camera_status if snap else "unreachable",
        "backend": snap.backend if snap else None,
        "target_locked": snap.target_locked if snap else False,
        "lost": snap.lost if snap else True,
        "cx": snap.cx if snap else 0.0,
        "cy": snap.cy if snap else 0.0,
    }


@router.post("/track/start", response_model=BetaflightSequenceStatusResponse)
def start_track(
    body: BetaflightTrackStartRequest,
    runner: BetaflightRcRunner = Depends(get_betaflight_runner),
) -> BetaflightSequenceStatusResponse:
    try:
        runner.start_track(body)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _status_response(runner)
