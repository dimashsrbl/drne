from __future__ import annotations

import logging
import struct
import threading
import time

import serial

from app.core.config import settings
from app.services.betaflight_control import BetaflightRcRunner, _msp_transaction, _parse_status
from app.services.betaflight_port_lock import PortBusyError, betaflight_port_lock
from app.services.bob57_bridge import Bob57BridgeAdapter
from app.services.drone_types import TelemetrySnapshot

logger = logging.getLogger(__name__)

MSP_STATUS = 101
MSP_ATTITUDE = 108
MSP_ALTITUDE = 109
MSP_ANALOG = 110

# Betaflight mode box ids (typical defaults).
_BOX_NAMES: dict[int, str] = {
    0: "ARM",
    1: "ANGLE",
    2: "HORIZON",
    3: "ANTI GRAVITY",
    4: "MAG",
    5: "HEADFREE",
    6: "HEADADJ",
    7: "CAMSTAB",
    8: "PASSTHRU",
    9: "BEEPERON",
    10: "LEDLOW",
    11: "CALIB",
    12: "OSD",
    13: "TELEMETRY",
    14: "SERVO1",
    15: "SERVO2",
    16: "SERVO3",
    17: "BLACKBOX",
    18: "FAILSAFE",
    19: "AIR MODE",
}


def _voltage_to_pct(voltage_v: float, cells: int) -> float:
    empty_v = cells * 3.5
    full_v = cells * 4.2
    if full_v <= empty_v:
        return 0.0
    pct = (voltage_v - empty_v) / (full_v - empty_v) * 100.0
    return max(0.0, min(100.0, pct))


def _mode_from_flags(mode_flags: int) -> str:
    active = [_BOX_NAMES[idx] for idx in sorted(_BOX_NAMES) if mode_flags & (1 << idx)]
    return "+".join(active) if active else "DISARM"


def _snapshot_from_runner(runner: BetaflightRcRunner) -> TelemetrySnapshot:
    state = runner.get_state()
    armed: bool | None = None
    channels = state.current_channels
    if channels and len(channels) >= settings.betaflight_arm_channel:
        armed = channels[settings.betaflight_arm_channel - 1] >= 1700

    note_parts = ["Betaflight sequence"]
    if state.current_action:
        note_parts.append(state.current_action)
    if state.elapsed_s:
        note_parts.append(f"{state.elapsed_s:.1f}s")

    return TelemetrySnapshot(
        status="connected",
        armed=armed,
        alt=state.current_alt_m,
        mode="BF SEQ",
        source="betaflight-msp",
        note=" · ".join(note_parts),
        updated_at_monotonic=time.monotonic(),
    )


def read_betaflight_telemetry(
    port: str | None = None,
    baud: int | None = None,
) -> TelemetrySnapshot:
    cfg_port = port or settings.betaflight_port
    cfg_baud = baud or settings.betaflight_baud
    cells = max(1, int(settings.betaflight_battery_cells))

    with betaflight_port_lock(timeout=0.5):
        with serial.Serial(cfg_port, cfg_baud, timeout=0.15) as ser:
            status_payload = _msp_transaction(ser, MSP_STATUS, timeout=0.6)
            if not status_payload:
                raise RuntimeError("MSP_STATUS: нет ответа")

            status = _parse_status(status_payload)
            mode_flags = int(status.get("mode_flags", 0))
            armed = bool(mode_flags & 1)
            mode = _mode_from_flags(mode_flags)

            voltage_v: float | None = None
            battery_pct: float | None = None
            analog_payload = _msp_transaction(ser, MSP_ANALOG, timeout=0.6)
            if analog_payload and len(analog_payload) >= 1:
                voltage_v = analog_payload[0] * 0.1
                battery_pct = _voltage_to_pct(voltage_v, cells)

            alt_m: float | None = None
            alt_payload = _msp_transaction(ser, MSP_ALTITUDE, timeout=0.6)
            if alt_payload and len(alt_payload) >= 4:
                alt_cm = struct.unpack("<i", alt_payload[0:4])[0]
                alt_m = alt_cm / 100.0

            heading: float | None = None
            att_payload = _msp_transaction(ser, MSP_ATTITUDE, timeout=0.6)
            if att_payload and len(att_payload) >= 6:
                yaw_tenth = struct.unpack("<h", att_payload[4:6])[0]
                heading = float(yaw_tenth) / 10.0
                if heading < 0:
                    heading += 360.0

            note = f"Betaflight MSP · {voltage_v:.1f}V" if voltage_v is not None else "Betaflight MSP"

            return TelemetrySnapshot(
                status="connected",
                alt=alt_m,
                battery=battery_pct,
                armed=armed,
                mode=mode,
                heading=heading,
                source="betaflight-msp",
                note=note,
                updated_at_monotonic=time.monotonic(),
            )


class BetaflightTelemetryPoller:
    """Фоновый MSP-опрос Betaflight для верхней полоски /telemetry."""

    def __init__(self, adapter: Bob57BridgeAdapter, runner: BetaflightRcRunner) -> None:
        self._adapter = adapter
        self._runner = runner
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="betaflight-telemetry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        interval = 1.0 / max(0.5, float(settings.betaflight_telemetry_hz))
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                runner_state = self._runner.get_state()
                if runner_state.status == "running":
                    self._adapter.ingest_telemetry(_snapshot_from_runner(self._runner))
                else:
                    try:
                        snap = read_betaflight_telemetry()
                        self._adapter.ingest_telemetry(snap)
                    except PortBusyError:
                        pass
            except serial.SerialException as exc:
                self._adapter.ingest_telemetry(
                    TelemetrySnapshot(
                        status="disconnected",
                        source="betaflight-msp",
                        note=f"Betaflight MSP: {exc!s}"[:500],
                        updated_at_monotonic=time.monotonic(),
                    )
                )
            except Exception as exc:
                logger.debug("Betaflight telemetry poll failed: %s", exc)
                self._adapter.ingest_telemetry(
                    TelemetrySnapshot(
                        status="disconnected",
                        source="betaflight-msp",
                        note=f"Betaflight MSP: {exc!s}"[:500],
                        updated_at_monotonic=time.monotonic(),
                    )
                )

            elapsed = time.monotonic() - started
            self._stop.wait(max(0.05, interval - elapsed))
