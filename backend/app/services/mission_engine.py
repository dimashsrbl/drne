from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from app.schemas.mission import (
    ArmAction,
    DisarmAction,
    GotoAction,
    LandAction,
    MissionAction,
    ReturnHomeAction,
    TakeoffAction,
    WaitAction,
)
from app.services.drone_types import DroneAdapter
from app.services.telemetry import TelemetryService


@dataclass
class MissionState:
    status: str = "idle"  # idle|running|completed|error
    current_step: int | None = None
    total_steps: int | None = None
    current_action: str | None = None
    error: str | None = None


class MissionEngine:
    """
    MVP движок миссий: последовательное выполнение действий.
    Ожидание "завершения" сделано упрощённо:
    - takeoff: ждём 85% целевой высоты; при no_gps — по приросту от базовой высоты (баро)
    - goto: ждём приближения к цели по lat/lon/alt в пределах tol
    - wait: sleep
    Остальное — fire-and-forget.
    """

    def __init__(self, drone: DroneAdapter, telemetry: TelemetryService) -> None:
        self._drone = drone
        self._telemetry = telemetry
        self._lock = threading.RLock()
        self._state = MissionState()
        self._thread: threading.Thread | None = None

    def get_state(self) -> MissionState:
        with self._lock:
            return MissionState(**self._state.__dict__)

    def start(self, actions: list[MissionAction]) -> None:
        with self._lock:
            if not self._drone.get_capabilities().supports_missions:
                raise RuntimeError("Текущий профиль борта не поддерживает mission engine. Для BOB57 bridge сначала работаем в ручном режиме, миссии будут доступны после перехода на ArduPilot.")
            if self._state.status == "running":
                raise RuntimeError("Миссия уже выполняется")
            self._state = MissionState(status="running", current_step=0, total_steps=len(actions))
            self._thread = threading.Thread(target=self._run, args=(actions,), daemon=True)
            self._thread.start()

    def _set_step(self, idx: int, action: str) -> None:
        with self._lock:
            self._state.current_step = idx
            self._state.current_action = action

    def _finish_ok(self) -> None:
        with self._lock:
            self._state.status = "completed"
            self._state.current_action = None

    def _finish_error(self, error: str) -> None:
        with self._lock:
            self._state.status = "error"
            self._state.error = error

    def _run(self, actions: list[MissionAction]) -> None:
        try:
            self._drone.connect()
            self._telemetry.start()

            for i, a in enumerate(actions, start=1):
                self._set_step(i, a.action)

                if isinstance(a, ArmAction):
                    self._drone.arm()

                elif isinstance(a, DisarmAction):
                    self._drone.disarm()

                elif isinstance(a, TakeoffAction):
                    baseline = self._telemetry.get_snapshot().alt
                    self._drone.takeoff(a.alt, no_gps=a.no_gps)
                    self._wait_takeoff_complete(
                        target_m=a.alt,
                        no_gps=a.no_gps,
                        baseline_alt=baseline,
                        timeout_s=90.0 if a.alt <= 5.0 else 120.0,
                    )

                elif isinstance(a, LandAction):
                    self._drone.land()
                    self._wait_land(timeout_s=60)

                elif isinstance(a, ReturnHomeAction):
                    self._drone.return_home()

                elif isinstance(a, GotoAction):
                    self._drone.goto(a.lat, a.lon, a.alt)
                    self._wait_goto(a.lat, a.lon, a.alt, timeout_s=120)

                elif isinstance(a, WaitAction):
                    time.sleep(float(a.seconds))

                else:
                    raise RuntimeError(f"Неизвестное действие: {a.action}")

            self._finish_ok()
        except Exception as e:
            self._finish_error(str(e))

    def _wait_altitude(self, min_alt: float, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            snap = self._telemetry.get_snapshot()
            if snap.alt is not None and snap.alt >= min_alt:
                return
            time.sleep(0.2)
        raise TimeoutError(f"Timeout: altitude < {min_alt}")

    def _wait_takeoff_complete(
        self,
        target_m: float,
        no_gps: bool,
        baseline_alt: float | None,
        timeout_s: float,
    ) -> None:
        """
        Ждём достижения целевой высоты.
        - Обычно: min = 85% от target (AGL от земли, ArduPilot / INAV с GPS).
        - no_gps: min = baseline + 85% от target (баро/MSL — прирост высоты).
        """
        frac = 0.85
        if no_gps and baseline_alt is not None:
            min_alt = baseline_alt + target_m * frac
        else:
            min_alt = target_m * frac
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            snap = self._telemetry.get_snapshot()
            if snap.alt is not None and snap.alt >= min_alt:
                return
            time.sleep(0.2)
        raise TimeoutError(f"Timeout: takeoff altitude не достигнута (ожидали >= {min_alt:.2f} м)")

    def _wait_land(self, timeout_s: float) -> None:
        """Ждём завершения посадки: armed=False ИЛИ alt < 0.5 м."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            snap = self._telemetry.get_snapshot()
            landed = (snap.armed is False) or (snap.alt is not None and snap.alt < 0.5)
            if landed:
                return
            time.sleep(0.3)
        # Таймаут — не считаем ошибкой, просто идём дальше

    def _wait_goto(self, lat: float, lon: float, alt: float, timeout_s: float) -> None:
        # очень грубо: градус ~111км. tol ~ 3e-5 градуса ≈ 3.3м
        tol_deg = 3e-5
        tol_alt = 2.0
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            snap = self._telemetry.get_snapshot()
            if snap.lat is None or snap.lon is None or snap.alt is None:
                time.sleep(0.2)
                continue
            if abs(snap.lat - lat) <= tol_deg and abs(snap.lon - lon) <= tol_deg and abs(snap.alt - alt) <= tol_alt:
                return
            time.sleep(0.2)
        raise TimeoutError("Timeout: goto not reached")

