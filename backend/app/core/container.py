from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import settings
from app.services.bob57_bridge import Bob57BridgeAdapter
from app.services.betaflight_control import BetaflightRcRunner
from app.services.betaflight_telemetry import BetaflightTelemetryPoller
from app.services.drone_control import DroneControlService
from app.services.drone_types import DroneAdapter
from app.services.inav_adapter import InavMavlinkAdapter
from app.services.mission_engine import MissionEngine
from app.services.telemetry import TelemetryService

if TYPE_CHECKING:
    from app.services.obstacle_avoidance import ObstacleAvoidanceService
    from app.services.vision import VisionService


def _build_drone() -> DroneAdapter:
    profile = (settings.backend_profile or "").strip().lower()
    if profile == "bob57_bridge":
        return Bob57BridgeAdapter()
    if profile == "inav":
        return InavMavlinkAdapter()
    if profile == "unity_sim":
        from app.services.unity_sim_adapter import UnitySimAdapter

        return UnitySimAdapter()
    return DroneControlService()


_drone = _build_drone()
_telemetry = TelemetryService(_drone)
_telemetry.start()
_mission = MissionEngine(_drone, _telemetry)
_betaflight_runner = None
_betaflight_telemetry: BetaflightTelemetryPoller | None = None
_vision = None
_avoidance = None


def get_drone() -> DroneAdapter:
    return _drone


def get_telemetry() -> TelemetryService:
    return _telemetry


def get_mission_engine() -> MissionEngine:
    return _mission


def get_betaflight_runner() -> BetaflightRcRunner:
    global _betaflight_runner
    if _betaflight_runner is None:
        _betaflight_runner = BetaflightRcRunner()
    return _betaflight_runner


if isinstance(_drone, Bob57BridgeAdapter):
    _betaflight_telemetry = BetaflightTelemetryPoller(_drone, get_betaflight_runner())
    _betaflight_telemetry.start()


def get_vision() -> VisionService:
    global _vision
    if _vision is None:
        from app.services.vision import VisionService

        _vision = VisionService()
    return _vision


def get_avoidance() -> ObstacleAvoidanceService:
    global _avoidance
    if _avoidance is None:
        from app.services.obstacle_avoidance import ObstacleAvoidanceService

        _avoidance = ObstacleAvoidanceService(_drone, _telemetry, get_vision())
    return _avoidance
