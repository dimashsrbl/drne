from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class TelemetrySnapshot:
    lat: float | None = None
    lon: float | None = None
    alt: float | None = None
    battery: float | None = None
    status: str = "unknown"
    speed: float | None = None
    armed: bool | None = None
    mode: str | None = None
    heading: float | None = None
    gps_sats: int | None = None
    gps_fix: int | None = None
    updated_at_monotonic: float | None = None
    source: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class SafetyGate:
    id: str
    title: str
    level: str = "info"


@dataclass(frozen=True)
class DroneCapabilities:
    profile: str
    label: str
    supports_missions: bool
    supports_manual_control: bool
    supports_direct_commands: bool
    supports_video: bool
    video_url: str | None = None
    warnings: tuple[str, ...] = ()
    safety_gates: tuple[SafetyGate, ...] = ()


class DroneAdapter(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def poll_telemetry(self, wait_s: float = 1.0) -> TelemetrySnapshot | None: ...
    def get_capabilities(self) -> DroneCapabilities: ...
    def arm(self) -> None: ...
    def disarm(self) -> None: ...
    def takeoff(self, altitude_m: float, no_gps: bool = False) -> None: ...
    def land(self) -> None: ...
    def return_home(self) -> None: ...
    def goto(self, lat: float, lon: float, alt_agl_m: float) -> None: ...
    def manual_control(self, pitch: int, roll: int, thrust: int, yaw: int) -> None: ...
    def set_home_global(self, lat: float, lon: float, alt_amsl_m: float) -> None: ...
    def set_flight_mode(self, mode_name: str) -> None: ...
