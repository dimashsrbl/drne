from fastapi import APIRouter, HTTPException

from app.core.container import get_mission_engine
from app.schemas.mission import MissionRequest, MissionStatusResponse, MissionStopRequest


router = APIRouter()


@router.post("", response_model=MissionStatusResponse)
def start_mission(body: MissionRequest) -> MissionStatusResponse:
    engine = get_mission_engine()
    try:
        engine.start(body.mission)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    st = engine.get_state()
    return MissionStatusResponse(
        status=st.status, current_step=st.current_step, total_steps=st.total_steps, current_action=st.current_action
    )


@router.get("/status", response_model=MissionStatusResponse)
def mission_status() -> MissionStatusResponse:
    st = get_mission_engine().get_state()
    return MissionStatusResponse(
        status=st.status,
        current_step=st.current_step,
        total_steps=st.total_steps,
        current_action=st.current_action,
        error=st.error,
    )


@router.post("/stop", response_model=MissionStatusResponse)
def stop_mission(body: MissionStopRequest) -> MissionStatusResponse:
    engine = get_mission_engine()
    try:
        engine.stop(body.action)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    st = engine.get_state()
    return MissionStatusResponse(
        status=st.status,
        current_step=st.current_step,
        total_steps=st.total_steps,
        current_action=st.current_action,
        error=st.error,
    )

