from fastapi import FastAPI

from app.api.router_mission_only import api_router_mission_only


def create_app() -> FastAPI:
    app = FastAPI(title="Drone Backend (Mission Only)", version="0.1.0")
    app.include_router(api_router_mission_only)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
