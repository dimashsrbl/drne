from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

from app.core.config import settings
from app.services.drone_types import DroneCapabilities, SafetyGate, TelemetrySnapshot


@dataclass
class QueuedCommand:
    id: str
    command: str
    params: dict[str, int | float | str | bool | None]
    created_at: float
    status: str = "pending"


class Bob57BridgeAdapter:
    """
    Режим реального BOB57 без прямой завязки на MAVLink.

    Идея:
      - внешний bridge/collector постит телеметрию в backend
      - frontend продолжает работать по тем же /api путям
      - команды из UI складываются в очередь, которую bridge может забирать
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot = TelemetrySnapshot(
            status="disconnected",
            source="bob57-bridge",
            note="Ожидается bridge-процесс BOB57",
        )
        self._last_ingest_monotonic: float | None = None
        self._bridge_seen_monotonic: float | None = None
        self._queue: list[QueuedCommand] = []

    def connect(self) -> None:
        return

    def disconnect(self) -> None:
        return

    def _bridge_online(self) -> bool:
        ts = self._bridge_seen_monotonic or self._last_ingest_monotonic
        if ts is None:
            return False
        return (time.monotonic() - ts) <= settings.bob57_bridge_timeout_s

    def poll_telemetry(self, wait_s: float = 1.0) -> TelemetrySnapshot | None:
        if wait_s > 0:
            time.sleep(min(wait_s, 0.2))

        with self._lock:
            snap = TelemetrySnapshot(**self._snapshot.__dict__)

        if not self._bridge_online():
            snap.status = "disconnected"
            if not snap.note:
                snap.note = "Нет свежей телеметрии от BOB57 bridge"
        elif snap.status in ("unknown", "disconnected"):
            snap.status = "connected"
        snap.updated_at_monotonic = time.monotonic()
        return snap

    def get_capabilities(self) -> DroneCapabilities:
        direct = settings.bob57_allow_write_commands
        warnings = [
            "Betaflight bridge mode: миссии с ПК отключены до миграции на ArduPilot.",
        ]
        if not direct:
            warnings.append("Запись команд в BOB57 bridge выключена: только мониторинг.")

        return DroneCapabilities(
            profile="bob57_bridge",
            label="BOB57 / Betaflight bridge",
            supports_missions=False,
            supports_manual_control=direct,
            supports_direct_commands=direct,
            supports_video=bool(settings.video_stream_url),
            video_url=settings.video_stream_url,
            warnings=tuple(warnings),
            safety_gates=(
                SafetyGate("failsafe", "Проверь failsafe: в backup был DROP, перед вылетом должен быть безопасный сценарий", "critical"),
                SafetyGate("no_props", "Первый bench-test команд только без пропеллеров", "warning"),
                SafetyGate("manual_first", "Первый реальный полёт только вручную с Tango 2, без mission mode", "warning"),
            ),
        )

    def ingest_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        with self._lock:
            snapshot.source = snapshot.source or "bob57-bridge"
            snapshot.note = snapshot.note or self._snapshot.note
            self._snapshot = snapshot
            self._last_ingest_monotonic = time.monotonic()

    def bridge_ping(self) -> None:
        with self._lock:
            self._bridge_seen_monotonic = time.monotonic()

    def get_pending_commands(self, limit: int = 20) -> list[QueuedCommand]:
        with self._lock:
            pending = [cmd for cmd in self._queue if cmd.status == "pending"]
            return [QueuedCommand(**cmd.__dict__) for cmd in pending[:limit]]

    def ack_command(self, command_id: str, status: str, note: str | None = None) -> None:
        with self._lock:
            for cmd in self._queue:
                if cmd.id == command_id:
                    cmd.status = status
                    break
            if note:
                self._snapshot.note = note

    def _ensure_write_enabled(self) -> None:
        if not settings.bob57_allow_write_commands:
            raise RuntimeError("BOB57 bridge работает в режиме мониторинга. Включи DRONE_BOB57_ALLOW_WRITE_COMMANDS=true только после bench-теста.")

    def _enqueue(self, command: str, params: dict[str, int | float | str | bool | None] | None = None) -> None:
        self._ensure_write_enabled()
        with self._lock:
            self._queue.append(
                QueuedCommand(
                    id=uuid.uuid4().hex,
                    command=command,
                    params=params or {},
                    created_at=time.time(),
                )
            )

    def arm(self) -> None:
        self._enqueue("arm")

    def disarm(self) -> None:
        self._enqueue("disarm")

    def takeoff(self, altitude_m: float, no_gps: bool = False) -> None:
        _ = (altitude_m, no_gps)
        raise RuntimeError("Автоматический takeoff для BOB57 bridge отключён. Первый полёт выполняй вручную с пульта.")

    def land(self) -> None:
        raise RuntimeError("Автоматическая посадка для BOB57 bridge отключена до миграции на ArduPilot.")

    def return_home(self) -> None:
        raise RuntimeError("Mission/RTL через backend для BOB57 bridge пока недоступны.")

    def goto(self, lat: float, lon: float, alt_agl_m: float) -> None:
        raise RuntimeError("Waypoint-миссии для BOB57 bridge пока недоступны.")

    def manual_control(self, pitch: int, roll: int, thrust: int, yaw: int) -> None:
        self._enqueue(
            "manual_control",
            {"pitch": pitch, "roll": roll, "thrust": thrust, "yaw": yaw},
        )

    def set_home_global(self, lat: float, lon: float, alt_amsl_m: float) -> None:
        raise RuntimeError("Set home через BOB57 bridge пока не поддержан.")

    def set_flight_mode(self, mode_name: str) -> None:
        self._enqueue("set_mode", {"mode": mode_name})
