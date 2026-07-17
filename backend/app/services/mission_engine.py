from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

from app.core.config import settings
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
from app.services.ardupilot_mission import actions_support_auto_upload
from app.services.drone_control import DroneControlService
from app.services.drone_types import DroneAdapter
from app.services.telemetry import TelemetryService


@dataclass
class MissionState:
    status: str = "idle"  # idle|running|completed|stopped|error
    current_step: int | None = None
    total_steps: int | None = None
    current_action: str | None = None
    error: str | None = None


class MissionStopped(Exception):
    pass


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class MissionEngine:
    """
    Движок миссий: последовательное выполнение действий через MAVLink-адаптер.

    ArduPilot:
    - guided (по умолчанию): GUIDED + takeoff/goto по шагам (конструктор с wait).
    - auto: загрузка MISSION_ITEM_INT + режим AUTO (маршрут по точкам без пауз).
    """

    def __init__(self, drone: DroneAdapter, telemetry: TelemetryService) -> None:
        self._drone = drone
        self._telemetry = telemetry
        self._lock = threading.RLock()
        self._state = MissionState()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def get_state(self) -> MissionState:
        with self._lock:
            return MissionState(**self._state.__dict__)

    def start(self, actions: list[MissionAction], *, prefer_auto: bool = False) -> None:
        with self._lock:
            if not self._drone.get_capabilities().supports_missions:
                raise RuntimeError(
                    "Текущий профиль борта не поддерживает mission engine. "
                    "Для BOB57 bridge нужен ArduPilot / INAV."
                )
            if self._state.status == "running":
                raise RuntimeError("Миссия уже выполняется")
            self._stop_event.clear()
            self._state = MissionState(status="running", current_step=0, total_steps=len(actions))
            self._thread = threading.Thread(
                target=self._run,
                args=(actions, prefer_auto),
                daemon=True,
            )
            self._thread.start()

    def stop(self, action: str = "land") -> None:
        """Остановить runner и передать безопасную команду непосредственно FC."""
        action = action.strip().lower()
        if action not in {"land", "rtl", "disarm"}:
            raise ValueError("stop action: land, rtl или disarm")
        self._stop_event.set()
        if action == "land":
            self._drone.land()
        elif action == "rtl":
            self._drone.return_home()
        else:
            self._drone.disarm()
        with self._lock:
            self._state.status = "stopped"
            self._state.current_action = action
            self._state.error = None

    def _check_stopped(self) -> None:
        if self._stop_event.is_set():
            raise MissionStopped()

    def _sleep(self, seconds: float) -> None:
        if self._stop_event.wait(max(0.0, seconds)):
            raise MissionStopped()

    def _profile(self) -> str:
        return self._drone.get_capabilities().profile

    def _set_step(self, idx: int, action: str) -> None:
        with self._lock:
            self._state.current_step = idx
            self._state.current_action = action

    def _finish_ok(self) -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            self._state.status = "completed"
            self._state.current_action = None

    def _finish_error(self, error: str) -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            self._state.status = "error"
            self._state.error = error

    def _needs_gps(self, actions: list[MissionAction]) -> bool:
        return any(isinstance(a, (TakeoffAction, GotoAction, ReturnHomeAction)) for a in actions)

    def _use_ardupilot_auto(self, actions: list[MissionAction], prefer_auto: bool) -> bool:
        if self._profile() != "ardupilot":
            return False
        if not isinstance(self._drone, DroneControlService):
            return False
        mode = (settings.ardupilot_mission_mode or "guided").strip().lower()
        if mode != "auto" and not prefer_auto:
            return False
        if not actions_support_auto_upload(actions):
            return False
        goto_n = sum(1 for a in actions if isinstance(a, GotoAction))
        if prefer_auto and goto_n >= settings.ardupilot_auto_route_min_waypoints:
            return True
        return mode == "auto"

    def _run(self, actions: list[MissionAction], prefer_auto: bool) -> None:
        try:
            self._drone.connect()
            self._telemetry.start()
            self._check_stopped()

            if self._needs_gps(actions):
                self._wait_gps_ready()

            if self._use_ardupilot_auto(actions, prefer_auto):
                self._run_ardupilot_auto(actions)
                return

            for i, a in enumerate(actions, start=1):
                self._check_stopped()
                self._set_step(i, a.action)

                if isinstance(a, ArmAction):
                    self._drone.arm()
                    if self._profile() == "ardupilot":
                        self._sleep(settings.ardupilot_arm_settle_s)

                elif isinstance(a, DisarmAction):
                    self._drone.disarm()

                elif isinstance(a, TakeoffAction):
                    if a.no_gps and self._profile() == "ardupilot":
                        raise RuntimeError("ArduPilot GUIDED takeoff требует GPS (no_gps не поддерживается)")
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
                    self._wait_land(timeout_s=90)

                elif isinstance(a, ReturnHomeAction):
                    self._drone.return_home()

                elif isinstance(a, GotoAction):
                    self._drone.goto(a.lat, a.lon, a.alt)
                    self._wait_goto(a.lat, a.lon, a.alt, timeout_s=180)

                elif isinstance(a, WaitAction):
                    self._sleep(float(a.seconds))

                else:
                    raise RuntimeError(f"Неизвестное действие: {a.action}")

            self._finish_ok()
        except MissionStopped:
            # stop() уже записал конечный статус и отправил LAND/RTL/DISARM.
            pass
        except Exception as e:
            self._finish_error(str(e))

    def _run_ardupilot_auto(self, actions: list[MissionAction]) -> None:
        if not isinstance(self._drone, DroneControlService):
            raise RuntimeError("AUTO-миссия доступна только для ArduPilot")
        arm_in_mission = any(isinstance(a, ArmAction) for a in actions)
        self._set_step(1, "auto_upload")
        plan = self._drone.upload_and_start_auto_mission(actions, arm_first=arm_in_mission)
        self._set_step(len(actions), "auto_run")
        timeout_s = max(120.0, 60.0 * len(plan.items))
        self._wait_auto_complete(timeout_s=timeout_s)
        self._finish_ok()

    def _wait_gps_ready(self, timeout_s: float = 90.0) -> None:
        min_sats = max(1, int(settings.ardupilot_min_gps_sats))
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._check_stopped()
            snap = self._telemetry.get_snapshot()
            fix = snap.gps_fix or 0
            sats = snap.gps_sats or 0
            if fix >= 3 and sats >= min_sats:
                return
            if snap.lat is not None and snap.lon is not None and sats >= min_sats:
                return
            self._sleep(0.3)
        raise TimeoutError(
            f"Timeout: нет GPS 3D (нужно ≥{min_sats} спутников) для миссии ArduPilot/INAV"
        )

    def _wait_takeoff_complete(
        self,
        target_m: float,
        no_gps: bool,
        baseline_alt: float | None,
        timeout_s: float,
    ) -> None:
        frac = 0.85
        if no_gps and baseline_alt is not None:
            min_alt = baseline_alt + target_m * frac
        else:
            min_alt = target_m * frac
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._check_stopped()
            snap = self._telemetry.get_snapshot()
            if snap.alt is not None and snap.alt >= min_alt:
                return
            self._sleep(0.2)
        raise TimeoutError(f"Timeout: takeoff altitude не достигнута (ожидали >= {min_alt:.2f} м)")

    def _wait_land(self, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._check_stopped()
            snap = self._telemetry.get_snapshot()
            landed = (snap.armed is False) or (snap.alt is not None and snap.alt < 0.5)
            if landed:
                return
            self._sleep(0.3)

    def _wait_goto(self, lat: float, lon: float, alt: float, timeout_s: float) -> None:
        tol_m = float(settings.ardupilot_goto_tol_m)
        tol_alt = float(settings.ardupilot_goto_alt_tol_m)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._check_stopped()
            snap = self._telemetry.get_snapshot()
            if snap.lat is None or snap.lon is None or snap.alt is None:
                self._sleep(0.2)
                continue
            horiz = _haversine_m(snap.lat, snap.lon, lat, lon)
            if horiz <= tol_m and abs(snap.alt - alt) <= tol_alt:
                return
            self._sleep(0.2)
        raise TimeoutError(f"Timeout: goto не достигнут ({lat:.6f}, {lon:.6f}, {alt:.1f} м)")

    def _wait_auto_complete(self, timeout_s: float) -> None:
        """Ждём завершения AUTO: disarm или посадка (alt < 1 м)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._check_stopped()
            snap = self._telemetry.get_snapshot()
            if snap.armed is False:
                return
            if snap.alt is not None and snap.alt < 1.0 and snap.mode in ("LAND", "RTL", None):
                self._sleep(2.0)
                snap2 = self._telemetry.get_snapshot()
                if snap2.armed is False or (snap2.alt is not None and snap2.alt < 0.6):
                    return
            self._sleep(0.5)
        raise TimeoutError("Timeout: ArduPilot AUTO миссия не завершилась")
