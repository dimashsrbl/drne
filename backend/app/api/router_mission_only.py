from fastapi import APIRouter

from app.api.routes import betaflight, drone, mission, nav, telemetry

api_router_mission_only = APIRouter()
api_router_mission_only.include_router(drone.router, prefix="/drone", tags=["drone"])
api_router_mission_only.include_router(telemetry.router, prefix="/telemetry", tags=["telemetry"])
api_router_mission_only.include_router(mission.router, prefix="/mission", tags=["mission"])
api_router_mission_only.include_router(nav.router, prefix="/nav", tags=["nav"])
api_router_mission_only.include_router(betaflight.router, prefix="/betaflight", tags=["betaflight"])
