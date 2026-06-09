from __future__ import annotations

import logging
import threading
import time

from app.core.config import settings
from app.services.drone_types import DroneAdapter, TelemetrySnapshot

logger = logging.getLogger(__name__)


class TelemetryService:
    """
    Фоново читает MAVLink и хранит последние значения телеметрии.
    Встроенный battery watchdog: при battery < battery_rtl_pct автоматически
    вызывает return_home() и логирует предупреждения при battery < battery_warn_pct.
    """

    def __init__(self, drone: DroneAdapter) -> None:
        self._drone = drone
        self._lock = threading.RLock()
        self._snapshot = TelemetrySnapshot()
        self._started = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Battery watchdog state
        self._last_battery_check = 0.0
        self._rtl_triggered = False
        self._warned_low = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._thread = threading.Thread(target=self._run, name="telemetry", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def get_snapshot(self) -> TelemetrySnapshot:
        with self._lock:
            return TelemetrySnapshot(**self._snapshot.__dict__)

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._snapshot.status = status
            self._snapshot.updated_at_monotonic = time.monotonic()

    def _check_battery(self, snap: TelemetrySnapshot) -> None:
        """Battery watchdog: предупреждение и авто-RTL по уровню батареи."""
        now = time.monotonic()
        if now - self._last_battery_check < settings.battery_check_interval_s:
            return
        self._last_battery_check = now

        battery = snap.battery
        armed = snap.armed

        if battery is None or not armed:
            # Сбрасываем флаги при разарминге
            if not armed:
                self._rtl_triggered = False
                self._warned_low = False
            return

        if battery <= settings.battery_rtl_pct and not self._rtl_triggered:
            self._rtl_triggered = True
            logger.critical(
                "[WATCHDOG] Критический заряд батареи: %.0f%% (порог %.0f%%) — "
                "автоматический RTL!",
                battery, settings.battery_rtl_pct,
            )
            with self._lock:
                self._snapshot.note = (
                    f"[WATCHDOG] КРИТИЧЕСКИЙ ЗАРЯД {battery:.0f}% — RTL запущен!"
                )
            try:
                self._drone.return_home()
            except Exception as e:
                logger.error("[WATCHDOG] Не удалось выполнить RTL: %s", e)

        elif battery <= settings.battery_warn_pct and not self._warned_low:
            self._warned_low = True
            logger.warning(
                "[WATCHDOG] Низкий заряд батареи: %.0f%% (порог %.0f%%)",
                battery, settings.battery_warn_pct,
            )
            with self._lock:
                note = self._snapshot.note or ""
                self._snapshot.note = f"[WATCHDOG] Низкий заряд {battery:.0f}% — планируй посадку!"

        elif battery > settings.battery_warn_pct:
            self._warned_low = False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                try:
                    self._drone.connect()
                    with self._lock:
                        self._snapshot.note = None
                    if self.get_snapshot().status in ("unknown", "disconnected"):
                        self._set_status("connected")
                except Exception as e:
                    self._set_status("disconnected")
                    with self._lock:
                        self._snapshot.note = f"MAVLink: {e!s}"[:500]
                    time.sleep(1)
                    continue

                snap = self._drone.poll_telemetry(wait_s=1.0)
                if snap is None:
                    continue

                with self._lock:
                    for field_name, value in snap.__dict__.items():
                        if field_name == "status":
                            if value in ("connected", "idle"):
                                if self._snapshot.status in ("unknown", "disconnected"):
                                    self._snapshot.status = value
                            elif value is not None:
                                self._snapshot.status = value
                            continue

                        if value is not None or field_name in ("updated_at_monotonic", "source", "note"):
                            setattr(self._snapshot, field_name, value)

                self._check_battery(self.get_snapshot())

            except Exception:
                self._set_status("disconnected")
                time.sleep(1)
