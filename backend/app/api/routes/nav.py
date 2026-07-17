from fastapi import APIRouter, HTTPException

from app.core.container import get_mission_engine
from app.schemas.mission import MissionStatusResponse
from app.schemas.navigation import RouteRequest
from app.services.navigation import NavigationService


router = APIRouter()
_nav = NavigationService()


@router.post("/route", response_model=MissionStatusResponse)
def route(body: RouteRequest) -> MissionStatusResponse:
    engine = get_mission_engine()
    try:
        actions = _nav.build_route_mission(body)
        prefer_auto = len(body.waypoints) >= 1
        engine.start(actions, prefer_auto=prefer_auto)
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

