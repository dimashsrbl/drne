from fastapi import APIRouter

from app.api.routes import betaflight, drone, mission, nav, telemetry, vision

api_router = APIRouter()
api_router.include_router(drone.router,     prefix="/drone",     tags=["drone"])
api_router.include_router(telemetry.router, prefix="/telemetry", tags=["telemetry"])
api_router.include_router(mission.router,   prefix="/mission",   tags=["mission"])
api_router.include_router(nav.router,       prefix="/nav",       tags=["nav"])
api_router.include_router(vision.router,    prefix="/vision",    tags=["vision"])
api_router.include_router(betaflight.router, prefix="/betaflight", tags=["betaflight"])
                                                                    