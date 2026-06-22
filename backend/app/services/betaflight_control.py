from __future__ import annotations

import struct
import threading
import time
from dataclasses import dataclass

import serial

from app.core.config import settings
from app.schemas.betaflight import BetaflightSequenceStartRequest, BetaflightSequenceStep, BetaflightTrackStartRequest
from app.services.betaflight_port_lock import PortBusyError, betaflight_port_lock
from app.services.inav_msp import build_msp_v1, msp_allow_arming, msp_rc_sticks_to_frame

MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3
MSP_STATUS = 101
MSP_ALTITUDE = 109
MSP_RC = 105
MSP_SET_RAW_RC = 200


@dataclass
class BetaflightRunnerState:
    status: str = "idle"
    current_step: int | None = None
    total_steps: int | None = None
    current_action: str | None = None
    elapsed_s: float = 0.0
    error: str | None = None
    port: str | None = None
    current_channels: list[int] | None = None
    current_alt_m: float | None = None
    target_alt_m: float | None = None


@dataclass(frozen=True)
class BetaflightRunConfig:
    port: str
    baud: int
    hz: float
    channels: int
    arm_channel: int
    angle_channel: int


def _clamp(value: int, low: int = 1000, high: int = 2000) -> int:
    return max(low, min(high, int(value)))


def _read_msp_response(ser: serial.Serial, expected_cmd: int | None, timeout: float = 0.8) -> tuple[int, bytes] | None:
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf.extend(chunk)

        while len(buf) >= 6:
            if buf[0:3] != b"$M>":
                idx = buf.find(b"$M>")
                if idx < 0:
                    buf.clear()
                    break
                del buf[:idx]
                continue

            size = buf[3]
            needed = 6 + size
            if len(buf) < needed:
                break

            cmd = buf[4]
            payload = bytes(buf[5 : 5 + size])
            checksum = buf[5 + size]
            calc = size ^ cmd
            for byte in payload:
                calc ^= byte
            del buf[:needed]

            if checksum != calc:
                continue
            if expected_cmd is None or cmd == expected_cmd:
                return cmd, payload

        if not chunk:
            time.sleep(0.01)
    return None


def _msp_transaction(ser: serial.Serial, cmd: int, payload: bytes = b"", timeout: float = 0.8) -> bytes | None:
    ser.reset_input_buffer()
    ser.write(build_msp_v1(cmd, payload))
    ser.flush()
    resp = _read_msp_response(ser, cmd, timeout=timeout)
    return resp[1] if resp else None


def _read_altitude_m(ser: serial.Serial) -> tuple[float, float] | None:
    payload = _msp_transaction(ser, MSP_ALTITUDE, timeout=0.2)
    if not payload or len(payload) < 4:
        return None
    alt_cm = struct.unpack("<i", payload[0:4])[0]
    vario_cms = struct.unpack("<h", payload[4:6])[0] if len(payload) >= 6 else 0
    return alt_cm / 100.0, vario_cms / 100.0


def _parse_status(payload: bytes) -> dict[str, int | str]:
    if len(payload) < 11:
        return {"raw": payload.hex(" ")}
    return {
        "cycle_ms": struct.unpack("<H", payload[0:2])[0],
        "i2c_errors": struct.unpack("<H", payload[2:4])[0],
        "sensors": struct.unpack("<H", payload[4:6])[0],
        "mode_flags": struct.unpack("<I", payload[6:10])[0],
        "profile": payload[10],
    }


def _parse_altitude_payload(payload: bytes) -> tuple[float, float] | None:
    if len(payload) < 4:
        return None
    alt_cm = struct.unpack("<i", payload[0:4])[0]
    vario_cms = struct.unpack("<h", payload[4:6])[0] if len(payload) >= 6 else 0
    return alt_cm / 100.0, vario_cms / 100.0


def _arm_fail_message(
    cfg: BetaflightRunConfig | None,
    *,
    mode_flags: int | None = None,
    msp_rc_arm_us: int | None = None,
) -> str:
    arm_ch = cfg.arm_channel if cfg is not None else settings.betaflight_arm_channel
    angle_ch = cfg.angle_channel if cfg is not None else settings.betaflight_angle_channel
    extra = ""
    if mode_flags is not None:
        extra += f" mode_flags=0x{mode_flags:08X}."
    if msp_rc_arm_us is not None:
        extra += f" FC видит канал {arm_ch}={msp_rc_arm_us} us."
    return (
        "FC не заармился через MSP. Проверь в Betaflight Configurator: "
        f"Receiver=MSP, Ports USB MSP ON, Modes ARM на AUX канале {arm_ch} (1700–2100), "
        f"throttle min, CLI `status` (arming disable flags). "
        f"Backend ARM={arm_ch}, ANGLE={angle_ch}.{extra} "
        "Configurator должен быть закрыт."
    )


def _parse_msp_rc(payload: bytes) -> list[int]:
    return [struct.unpack("<H", payload[i : i + 2])[0] for i in range(0, len(payload) - 1, 2)]


class MspRcStreamer:
    """Непрерывный SET_RAW_RC в фоне — как tools/betaflight_arm_test.py."""

    def __init__(
        self,
        ser: serial.Serial,
        hz: float,
        num_channels: int,
        io_lock: threading.Lock | None = None,
    ) -> None:
        self._ser = ser
        self._hz = max(1.0, hz)
        self._num_channels = num_channels
        self._channels = [1500] * num_channels
        self._lock = threading.Lock()
        self._io_lock = io_lock or threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_channels(self, channels: list[int]) -> None:
        with self._lock:
            self._channels = list(channels[: self._num_channels])

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="bf-msp-rc-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        interval = 1.0 / self._hz
        while not self._stop.is_set():
            with self._lock:
                channels = list(self._channels)
            payload = struct.pack("<" + "H" * len(channels), *channels)
            try:
                with self._io_lock:
                    self._ser.write(build_msp_v1(MSP_SET_RAW_RC, payload))
                    self._ser.flush()
            except Exception:
                pass
            time.sleep(interval)


class BetaflightRcRunner:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = BetaflightRunnerState()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ser: serial.Serial | None = None
        self._alt_baseline_m: float | None = None
        self._alt_poll_counter = 0
        self._alt_last_rel_m: float | None = None
        self._rc_streamer: MspRcStreamer | None = None
        self._serial_io_lock = threading.Lock()
        self._emergency_land_steps: list[BetaflightSequenceStep] | None = None
        self._abort_remaining_steps = False
        self._run_mode: str = "idle"
        self._last_cfg: BetaflightRunConfig | None = None

    def get_state(self) -> BetaflightRunnerState:
        with self._lock:
            return BetaflightRunnerState(**self._state.__dict__)

    def check(self, port: str | None = None, baud: int | None = None) -> dict[str, object]:
        cfg_port = port or settings.betaflight_port
        cfg_baud = baud or settings.betaflight_baud
        cfg = BetaflightRunConfig(
            port=cfg_port,
            baud=cfg_baud,
            hz=settings.betaflight_rc_hz,
            channels=settings.betaflight_rc_channels,
            arm_channel=settings.betaflight_arm_channel,
            angle_channel=settings.betaflight_angle_channel,
        )
        try:
            with betaflight_port_lock(timeout=3.0):
                with serial.Serial(cfg_port, baudrate=cfg_baud, timeout=0.2) as ser:
                    api = _msp_transaction(ser, MSP_API_VERSION)
                    if not api or len(api) < 3:
                        return {
                            "ok": False,
                            "detail": "Нет ответа MSP API_VERSION. Закрой Configurator и проверь MSP на порту.",
                            "port": cfg_port,
                        }
                    variant = _msp_transaction(ser, MSP_FC_VARIANT)
                    version = _msp_transaction(ser, MSP_FC_VERSION)
                    status_payload = _msp_transaction(ser, MSP_STATUS)
                    status_info = _parse_status(status_payload) if status_payload else {}

                    arm_channels = self._channels(cfg, throttle_us=1000, arm_us=self._arm_switch_us())
                    deadline = time.monotonic() + 2.0
                    interval = 1.0 / max(1.0, cfg.hz)
                    while time.monotonic() < deadline:
                        self._send_channels(ser, arm_channels)
                        time.sleep(interval)

                    rc_payload = self._read_msp_while_streaming(ser, MSP_RC, arm_channels, cfg, timeout=0.3)
                    status_payload2 = self._read_msp_while_streaming(ser, MSP_STATUS, arm_channels, cfg, timeout=0.25)
                    rc_values = _parse_msp_rc(rc_payload) if rc_payload else []
                    status2 = _parse_status(status_payload2) if status_payload2 else {}
                    mode_flags = int(status2.get("mode_flags", status_info.get("mode_flags", 0)))
                    arm_idx = cfg.arm_channel - 1
                    msp_rc_arm = rc_values[arm_idx] if 0 <= arm_idx < len(rc_values) else None
                    arm_box_active = bool(mode_flags & 1)
        except PortBusyError as e:
            return {"ok": False, "detail": str(e), "port": cfg_port}
        except serial.SerialException as e:
            return {"ok": False, "detail": str(e), "port": cfg_port}

        diag = {
            "mode_flags": mode_flags,
            "arm_box_active": int(arm_box_active),
            "msp_rc_arm_us": msp_rc_arm if msp_rc_arm is not None else -1,
            "msp_rc": ",".join(str(v) for v in rc_values[:8]) if rc_values else "",
        }
        detail = "MSP link OK"
        if msp_rc_arm is not None and msp_rc_arm < 1700:
            detail += f". FC не видит ARM на канале {cfg.arm_channel} (={msp_rc_arm} us) — проверь Receiver=MSP"
        elif not arm_box_active:
            detail += ". ARM switch не активен — проверь Modes/aux или CLI `status`"

        return {
            "ok": True,
            "detail": detail,
            "port": cfg_port,
            "variant": variant.decode("ascii", errors="replace") if variant else None,
            "version": ".".join(str(b) for b in version[:3]) if version and len(version) >= 3 else None,
            "status": {**status_info, **diag},
        }

    def emergency_land(
        self,
        *,
        port: str | None = None,
        baud: int | None = None,
        seconds: float | None = None,
        throttle_us: int | None = None,
    ) -> None:
        land_s = seconds if seconds is not None else settings.betaflight_emergency_land_s
        hover = throttle_us or settings.betaflight_alt_hover_us
        land_steps = [
            BetaflightSequenceStep(action="land", seconds=land_s, throttle_us=hover),
            BetaflightSequenceStep(action="disarm"),
        ]
        cfg = BetaflightRunConfig(
            port=port or settings.betaflight_port,
            baud=baud or settings.betaflight_baud,
            hz=settings.betaflight_rc_hz,
            channels=settings.betaflight_rc_channels,
            arm_channel=settings.betaflight_arm_channel,
            angle_channel=settings.betaflight_angle_channel,
        )

        with self._lock:
            if self._state.status == "running":
                self._emergency_land_steps = land_steps
                self._stop_event.set()
                return

        self._start_sequence(land_steps, cfg, skip_disarm_preamble=True)

    def _start_sequence(
        self,
        steps: list[BetaflightSequenceStep],
        cfg: BetaflightRunConfig,
        *,
        skip_disarm_preamble: bool = False,
    ) -> None:
        with self._lock:
            if self._state.status == "running":
                raise RuntimeError("Betaflight sequence already running")
            self._stop_event.clear()
            self._emergency_land_steps = None
            self._abort_remaining_steps = False
            self._run_mode = "sequence"
            self._last_cfg = cfg
            self._state = BetaflightRunnerState(
                status="running",
                current_step=0,
                total_steps=len(steps),
                port=cfg.port,
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(steps, cfg),
                kwargs={"skip_disarm_preamble": skip_disarm_preamble},
                name="betaflight-rc-runner",
                daemon=True,
            )
            self._thread.start()

    def start(self, req: BetaflightSequenceStartRequest) -> None:
        cfg = BetaflightRunConfig(
            port=req.port or settings.betaflight_port,
            baud=req.baud or settings.betaflight_baud,
            hz=req.hz or settings.betaflight_rc_hz,
            channels=req.channels or settings.betaflight_rc_channels,
            arm_channel=req.arm_channel or settings.betaflight_arm_channel,
            angle_channel=req.angle_channel or settings.betaflight_angle_channel,
        )
        self._start_sequence(list(req.steps), cfg, skip_disarm_preamble=False)

    def start_track(self, req: BetaflightTrackStartRequest) -> None:
        cfg = BetaflightRunConfig(
            port=req.port or settings.betaflight_port,
            baud=req.baud or settings.betaflight_baud,
            hz=settings.betaflight_rc_hz,
            channels=settings.betaflight_rc_channels,
            arm_channel=settings.betaflight_arm_channel,
            angle_channel=settings.betaflight_angle_channel,
        )
        with self._lock:
            if self._state.status == "running":
                raise RuntimeError("Betaflight sequence already running")
            self._run_mode = "track"
            self._stop_event.clear()
            self._emergency_land_steps = None
            self._abort_remaining_steps = False
            self._last_cfg = cfg
            self._state = BetaflightRunnerState(
                status="running",
                current_step=1,
                total_steps=5,
                port=cfg.port,
                current_action="wait_lock",
            )
            self._thread = threading.Thread(
                target=self._run_track,
                args=(req, cfg),
                name="betaflight-track-runner",
                daemon=True,
            )
            self._thread.start()

    def _set_action(self, action: str) -> None:
        with self._lock:
            self._state.current_action = action

    def _disarm_hold_s(self) -> float:
        return max(0.5, float(settings.betaflight_disarm_hold_s))

    def _stream_disarm_hold(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        seconds: float | None = None,
        *,
        start_t: float | None = None,
    ) -> None:
        """Непрерывный DISARM по MSP — одного кадра недостаточно для Betaflight."""
        hold_s = self._disarm_hold_s() if seconds is None else max(0.5, float(seconds))
        idle = settings.betaflight_idle_throttle_us
        channels = self._channels(cfg, throttle_us=idle, arm_us=1000)
        interval = 1.0 / max(1.0, cfg.hz)
        deadline = time.monotonic() + hold_s
        while time.monotonic() < deadline:
            self._send_channels(ser, channels)
            if start_t is not None:
                with self._lock:
                    self._state.elapsed_s = time.monotonic() - start_t
                    self._state.current_channels = list(channels)
            time.sleep(interval)

    def _force_disarm_standalone(
        self,
        port: str,
        baud: int,
        cfg: BetaflightRunConfig | None = None,
    ) -> None:
        """Открыть порт заново и гарантированно DISARM (после ошибки / когда поток уже завершился)."""
        cfg_eff = cfg or BetaflightRunConfig(
            port=port,
            baud=baud,
            hz=settings.betaflight_rc_hz,
            channels=settings.betaflight_rc_channels,
            arm_channel=settings.betaflight_arm_channel,
            angle_channel=settings.betaflight_angle_channel,
        )
        try:
            with betaflight_port_lock(timeout=5.0):
                with serial.Serial(port, baudrate=baud, timeout=0.2) as ser:
                    self._stream_disarm_hold(ser, cfg_eff)
        except (PortBusyError, serial.SerialException, OSError):
            pass

    def stop(self) -> None:
        with self._lock:
            self._emergency_land_steps = None
            port = self._state.port or settings.betaflight_port
            cfg = self._last_cfg
            baud = cfg.baud if cfg is not None else settings.betaflight_baud
        self._stop_event.set()

        ser = self._ser
        if ser is not None and cfg is not None:
            try:
                self._stream_disarm_hold(ser, cfg, min(1.0, self._disarm_hold_s()))
            except Exception:
                pass

        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(8.0, self._disarm_hold_s() + 5.0))

        self._force_disarm_standalone(port, baud, cfg)

        with self._lock:
            if self._state.status in ("running", "error"):
                self._state.status = "stopped"
                self._state.current_action = None
            self._run_mode = "idle"

    def _run(
        self,
        steps: list[BetaflightSequenceStep],
        cfg: BetaflightRunConfig,
        *,
        skip_disarm_preamble: bool = False,
    ) -> None:
        start_t = time.monotonic()
        self._alt_baseline_m = None
        self._alt_poll_counter = 0
        self._alt_last_rel_m = None
        try:
            with betaflight_port_lock():
                with serial.Serial(cfg.port, baudrate=cfg.baud, timeout=0.2) as ser:
                    self._ser = ser
                    with self._serial_io_lock:
                        msp_allow_arming(ser)
                    self._rc_streamer = MspRcStreamer(ser, cfg.hz, cfg.channels, self._serial_io_lock)
                    self._rc_streamer.start()
                    time.sleep(0.3)
                    if not skip_disarm_preamble:
                        self._stream_for(
                            ser,
                            cfg,
                            self._channels(cfg, throttle_us=1000, arm_us=1000),
                            1.0,
                            start_t,
                        )
                        self._capture_alt_baseline(ser, cfg)

                    self._execute_steps(ser, cfg, steps, start_t)

                    self._stream_for(
                        ser,
                        cfg,
                        self._channels(cfg, throttle_us=1000, arm_us=1000),
                        0.8,
                        start_t,
                    )
                    if self._stop_event.is_set() and not self._emergency_land_steps:
                        self._finish_stopped()
                        return
            with self._lock:
                self._state.status = "completed"
                self._state.current_action = None
                self._state.elapsed_s = time.monotonic() - start_t
        except Exception as e:
            try:
                if self._ser is not None:
                    self._stream_disarm_hold(self._ser, cfg)
            except Exception:
                pass
            with self._lock:
                self._state.status = "error"
                self._state.error = str(e)
                self._state.current_action = None
                self._state.elapsed_s = time.monotonic() - start_t
        finally:
            if self._rc_streamer is not None:
                self._rc_streamer.stop()
                self._rc_streamer = None
            self._ser = None
            with self._lock:
                self._run_mode = "idle"

    def _run_track(self, req: BetaflightTrackStartRequest, cfg: BetaflightRunConfig) -> None:
        from app.services.betaflight_track import BetaflightTrackMission

        start_t = time.monotonic()
        self._alt_baseline_m = None
        self._alt_poll_counter = 0
        self._alt_last_rel_m = None
        try:
            with betaflight_port_lock():
                with serial.Serial(cfg.port, baudrate=cfg.baud, timeout=0.2) as ser:
                    self._ser = ser
                    with self._serial_io_lock:
                        msp_allow_arming(ser)
                    self._rc_streamer = MspRcStreamer(ser, cfg.hz, cfg.channels, self._serial_io_lock)
                    self._rc_streamer.start()
                    time.sleep(0.3)
                    self._stream_for(
                        ser,
                        cfg,
                        self._channels(cfg, throttle_us=1000, arm_us=1000),
                        1.0,
                        start_t,
                    )
                    BetaflightTrackMission(self).run(req, cfg, start_t)
            with self._lock:
                if self._stop_event.is_set():
                    self._state.status = "stopped"
                else:
                    self._state.status = "completed"
                self._state.current_action = None
                self._state.elapsed_s = time.monotonic() - start_t
        except Exception as e:
            try:
                if self._ser is not None:
                    self._stream_disarm_hold(self._ser, cfg)
            except Exception:
                pass
            with self._lock:
                if self._stop_event.is_set() and not self._state.error:
                    self._state.status = "stopped"
                else:
                    self._state.status = "error"
                    self._state.error = str(e)
                self._state.current_action = None
                self._state.elapsed_s = time.monotonic() - start_t
        finally:
            if self._rc_streamer is not None:
                self._rc_streamer.stop()
                self._rc_streamer = None
            self._ser = None
            with self._lock:
                self._run_mode = "idle"

    def _execute_steps(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        steps: list[BetaflightSequenceStep],
        start_t: float,
    ) -> None:
        for idx, step in enumerate(steps, start=1):
            if self._try_interrupt(ser, cfg, start_t):
                return
            with self._lock:
                self._state.current_step = idx
                self._state.current_action = step.action
                if step.target_alt_m is not None:
                    self._state.target_alt_m = step.target_alt_m
            if step.action == "arm":
                self._ensure_armed(ser, cfg, start_t, step.seconds)
                self._capture_alt_baseline(ser, cfg)
            elif step.action == "takeoff_alt":
                self._stream_takeoff_alt(ser, cfg, step, start_t)
            elif step.action == "hold_alt":
                self._stream_hold_alt(ser, cfg, step, start_t)
            elif step.action == "land":
                self._stream_land_alt(ser, cfg, step, start_t)
            elif step.action == "disarm":
                channels = self._step_channels(step, cfg)
                self._stream_for(ser, cfg, channels, step.seconds, start_t)
            elif self._active_target_alt_m(step) is not None:
                self._stream_baro_hover(ser, cfg, step, start_t)
            else:
                channels = self._step_channels(step, cfg)
                self._stream_for(ser, cfg, channels, step.seconds, start_t)
            if self._abort_remaining_steps:
                return

    def _stream_for(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        channels: list[int],
        seconds: float,
        start_t: float,
    ) -> None:
        interval = 1.0 / max(1.0, cfg.hz)
        deadline = time.monotonic() + max(0.0, seconds)
        self._send_channels(ser, channels)
        while time.monotonic() < deadline:
            if self._try_interrupt(ser, cfg, start_t):
                return
            with self._lock:
                self._state.elapsed_s = time.monotonic() - start_t
                self._state.current_channels = list(channels)
            self._stop_event.wait(interval)

    def _send_channels(self, ser: serial.Serial, channels: list[int]) -> None:
        with self._lock:
            self._state.current_channels = list(channels)
        if self._rc_streamer is not None:
            self._rc_streamer.set_channels(channels)
            return
        payload = struct.pack("<" + "H" * len(channels), *channels)
        ser.write(build_msp_v1(MSP_SET_RAW_RC, payload))
        ser.flush()

    def _msp_read(self, ser: serial.Serial, cmd: int, timeout: float = 0.35) -> bytes | None:
        if self._rc_streamer is not None:
            with self._serial_io_lock:
                ser.write(build_msp_v1(cmd))
                ser.flush()
            resp = _read_msp_response(ser, cmd, timeout=timeout)
            return resp[1] if resp else None
        return _msp_transaction(ser, cmd, timeout=timeout)

    def _msp_rc_arm_us(self, ser: serial.Serial, cfg: BetaflightRunConfig) -> int | None:
        payload = self._msp_read(ser, MSP_RC, timeout=0.35)
        if not payload:
            return None
        rc_values = _parse_msp_rc(payload)
        arm_idx = cfg.arm_channel - 1
        if 0 <= arm_idx < len(rc_values):
            return rc_values[arm_idx]
        return None

    def _arm_switch_us(self) -> int:
        return _clamp(settings.betaflight_arm_switch_us, 1700, 2100)

    def _read_msp_while_streaming(
        self,
        ser: serial.Serial,
        cmd: int,
        channels: list[int],
        cfg: BetaflightRunConfig,
        timeout: float = 0.22,
    ) -> bytes | None:
        self._send_channels(ser, channels)
        return self._msp_read(ser, cmd, timeout=timeout)

    def _fc_armed(self, ser: serial.Serial, cfg: BetaflightRunConfig, channels: list[int]) -> bool:
        self._send_channels(ser, channels)
        time.sleep(0.15)
        payload = self._msp_read(ser, MSP_STATUS, timeout=0.35)
        if payload:
            status = _parse_status(payload)
            if bool(int(status.get("mode_flags", 0)) & 1):
                return True
        rc_arm = self._msp_rc_arm_us(ser, cfg)
        return rc_arm is not None and rc_arm >= 1700

    def _arm_diag(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        channels: list[int],
    ) -> tuple[int | None, int | None]:
        rc_payload = self._read_msp_while_streaming(ser, MSP_RC, channels, cfg, timeout=0.25)
        status_payload = self._read_msp_while_streaming(ser, MSP_STATUS, channels, cfg, timeout=0.2)
        msp_rc_arm: int | None = None
        if rc_payload:
            rc_values = _parse_msp_rc(rc_payload)
            arm_idx = cfg.arm_channel - 1
            if 0 <= arm_idx < len(rc_values):
                msp_rc_arm = rc_values[arm_idx]
        mode_flags: int | None = None
        if status_payload:
            mode_flags = int(_parse_status(status_payload).get("mode_flags", 0))
        return mode_flags, msp_rc_arm

    def _ensure_armed(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        start_t: float,
        timeout_s: float | None = None,
    ) -> None:
        hold_s = max(float(timeout_s or 0.0), settings.betaflight_arm_hold_s)
        # Как arm_test: сначала ANGLE без ARM, потом ARM ON @ 2000.
        self._stream_for(
            ser,
            cfg,
            self._channels(cfg, throttle_us=1000, arm_us=1000),
            1.0,
            start_t,
        )
        arm_channels = self._channels(cfg, throttle_us=1000, arm_us=2000)
        deadline = time.monotonic() + hold_s
        interval = 1.0 / max(1.0, cfg.hz)
        next_check = 0.0

        while time.monotonic() < deadline:
            if self._try_interrupt(ser, cfg, start_t):
                return

            self._send_channels(ser, arm_channels)
            with self._lock:
                self._state.elapsed_s = time.monotonic() - start_t

            now = time.monotonic()
            if now >= next_check:
                if self._fc_armed(ser, cfg, arm_channels):
                    self._stream_for(ser, cfg, arm_channels, 0.6, start_t)
                    return
                next_check = now + 0.35
            time.sleep(interval)

        flags, msp_rc_arm = self._arm_diag(ser, cfg, arm_channels)
        if msp_rc_arm is not None and msp_rc_arm >= 1700:
            self._stream_for(ser, cfg, arm_channels, 0.6, start_t)
            return
        raise RuntimeError(_arm_fail_message(cfg, mode_flags=flags, msp_rc_arm_us=msp_rc_arm))

    def _capture_alt_baseline(self, ser: serial.Serial, cfg: BetaflightRunConfig) -> None:
        """Среднее баро на земле (armed, throttle min) — точнее одного снимка."""
        channels = self._channels(cfg, throttle_us=1000, arm_us=self._arm_switch_us())
        samples = max(1, settings.betaflight_alt_baseline_samples)
        readings: list[float] = []
        for _ in range(samples):
            payload = self._read_msp_while_streaming(ser, MSP_ALTITUDE, channels, cfg, timeout=0.25)
            if payload:
                parsed = _parse_altitude_payload(payload)
                if parsed is not None:
                    readings.append(parsed[0])
            time.sleep(0.07)
        if readings:
            self._alt_baseline_m = sum(readings) / len(readings)
            self._alt_last_rel_m = 0.0
            with self._lock:
                self._state.current_alt_m = 0.0

    def _ramp_until_liftoff(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        step: BetaflightSequenceStep,
        start_t: float,
    ) -> None:
        """Мягкий разгон газа при нейтральных стиках до первого подъёма по баро."""
        hover = step.throttle_us or settings.betaflight_alt_hover_us
        throttle = max(1000, hover - 120)
        max_throttle = min(hover + 40, settings.betaflight_alt_max_climb_us)
        ramp = max(1, settings.betaflight_alt_liftoff_ramp_us)
        liftoff_m = settings.betaflight_alt_liftoff_m
        interval = 1.0 / max(1.0, cfg.hz)
        deadline = time.monotonic() + 5.0

        while time.monotonic() < deadline:
            if self._try_interrupt(ser, cfg, start_t):
                return
            channels = self._channels(cfg, throttle_us=throttle, arm_us=self._arm_switch_us())
            self._send_channels(ser, channels)
            with self._lock:
                self._state.elapsed_s = time.monotonic() - start_t
            rel = self._relative_alt_m(ser, cfg, channels)
            if rel is not None and rel >= liftoff_m:
                return
            if throttle < max_throttle:
                throttle = min(max_throttle, throttle + ramp)
            self._stop_event.wait(interval)

    def _relative_alt_m(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        channels: list[int],
    ) -> float | None:
        payload = self._read_msp_while_streaming(ser, MSP_ALTITUDE, channels, cfg, timeout=0.22)
        if not payload:
            return None
        parsed = _parse_altitude_payload(payload)
        if parsed is None:
            return None
        alt_m, _vario = parsed
        if self._alt_baseline_m is None:
            self._alt_baseline_m = alt_m
        rel = alt_m - self._alt_baseline_m
        with self._lock:
            self._state.current_alt_m = round(rel, 2)
        return rel

    def _active_target_alt_m(self, step: BetaflightSequenceStep) -> float | None:
        if step.target_alt_m is not None and step.target_alt_m > 0:
            return float(step.target_alt_m)
        with self._lock:
            t = self._state.target_alt_m
        return float(t) if t is not None and t > 0 else None

    def _alt_throttle_us(
        self,
        error_m: float,
        step: BetaflightSequenceStep,
        *,
        hold: bool = False,
        landing: bool = False,
    ) -> int:
        hover = step.throttle_us or settings.betaflight_alt_hover_us
        tol = settings.betaflight_alt_tolerance_m
        if abs(error_m) <= tol:
            return _clamp(int(hover), 1000, settings.betaflight_max_throttle_us)
        gain = settings.betaflight_alt_p_gain
        if abs(error_m) < settings.betaflight_alt_near_band_m:
            gain = settings.betaflight_alt_p_gain_near
        throttle = int(hover + gain * error_m)
        if error_m > 0:
            throttle = min(throttle, settings.betaflight_alt_max_climb_us)
            if hold:
                throttle = max(throttle, settings.betaflight_alt_hold_min_us)
        elif landing:
            # Только явная посадка: можно снижать газ сильнее к land_throttle.
            overshoot = abs(error_m)
            floor_us = settings.betaflight_alt_max_descend_us
            if overshoot >= settings.betaflight_alt_overshoot_m:
                floor_us = min(floor_us, settings.betaflight_land_throttle_us)
            if overshoot >= 0.5:
                floor_us = min(floor_us, 1050)
            throttle = max(throttle, floor_us)
        else:
            # Взлёт / hold в воздухе: мягко сбавляем, но не «рубим» до посадочного газа.
            floor_us = settings.betaflight_alt_max_descend_us
            if hold:
                floor_us = max(floor_us, settings.betaflight_alt_hold_min_us - 80)
            throttle = max(throttle, floor_us)
        return _clamp(throttle, 1000, settings.betaflight_max_throttle_us)

    def _poll_rel_alt_m(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        channels: list[int],
        *,
        force: bool = False,
    ) -> float | None:
        every = max(1, settings.betaflight_alt_poll_every_n)
        self._alt_poll_counter += 1
        if not force and self._alt_last_rel_m is not None and (self._alt_poll_counter % every) != 0:
            return self._alt_last_rel_m
        parsed = self._poll_altitude(ser, cfg, channels)
        if parsed is None:
            return self._alt_last_rel_m
        rel = parsed[0]
        self._alt_last_rel_m = rel
        return rel

    def _poll_altitude(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        channels: list[int],
    ) -> tuple[float, float] | None:
        """(rel_alt_m, vario_cms) или None."""
        payload = self._read_msp_while_streaming(ser, MSP_ALTITUDE, channels, cfg, timeout=0.22)
        if not payload:
            return None
        parsed = _parse_altitude_payload(payload)
        if parsed is None:
            return None
        alt_m, vario_cms = parsed
        if self._alt_baseline_m is None:
            self._alt_baseline_m = alt_m
        rel = alt_m - self._alt_baseline_m
        with self._lock:
            self._state.current_alt_m = round(rel, 2)
        return rel, vario_cms

    def _stream_baro_hover(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        step: BetaflightSequenceStep,
        start_t: float,
    ) -> None:
        """
        Парение/нейтраль/стики с P по баро каждый тик (~25 Гц): выше цели — снижает газ, ниже — поднимает.
        """
        target = self._active_target_alt_m(step)
        if target is None:
            channels = self._step_channels(step, cfg)
            self._stream_for(ser, cfg, channels, step.seconds, start_t)
            return

        interval = 1.0 / max(1.0, cfg.hz)
        deadline = time.monotonic() + max(0.1, step.seconds)
        base_channels = self._step_channels(step, cfg)
        throttle = step.throttle_us or settings.betaflight_alt_hover_us
        hold_step = BetaflightSequenceStep(
            action="hold_alt",
            seconds=step.seconds,
            target_alt_m=target,
            throttle_us=step.throttle_us,
        )
        last_success_at = time.monotonic()

        while time.monotonic() < deadline:
            if self._try_interrupt(ser, cfg, start_t):
                return

            probe = self._channels(
                cfg,
                roll_us=base_channels[0],
                pitch_us=base_channels[1],
                throttle_us=throttle,
                yaw_us=base_channels[3] if len(base_channels) > 3 else None,
                arm_us=self._arm_switch_us(),
            )
            rel = self._poll_rel_alt_m(ser, cfg, probe)
            if rel is not None:
                error = target - rel
                target_throttle = self._alt_throttle_us(error, hold_step, hold=True)
                throttle = self._smooth_throttle_us(throttle, target_throttle)
                last_success_at = time.monotonic()
            elif time.monotonic() - last_success_at > 2.5:
                raise RuntimeError("Нет данных барометра (MSP_ALTITUDE). Проверь baro в Betaflight.")

            channels = self._channels(
                cfg,
                roll_us=base_channels[0],
                pitch_us=base_channels[1],
                throttle_us=throttle,
                yaw_us=base_channels[3] if len(base_channels) > 3 else None,
                arm_us=self._arm_switch_us(),
            )
            self._send_channels(ser, channels)
            with self._lock:
                self._state.elapsed_s = time.monotonic() - start_t
                self._state.target_alt_m = target
            self._stop_event.wait(interval)

    def _smooth_throttle_us(self, current: int, target: int) -> int:
        step = max(1, settings.betaflight_alt_throttle_slew_us)
        if target > current:
            return min(target, current + step)
        return max(target, current - step)

    def _stream_alt_loop(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        step: BetaflightSequenceStep,
        start_t: float,
        *,
        target_rel_m: float,
        done_when_reached: bool,
        hold: bool = False,
    ) -> None:
        """P-регулятор по барометру до target_rel_m или до таймаута."""
        interval = 1.0 / max(1.0, cfg.hz)
        deadline = time.monotonic() + max(0.5, step.seconds)
        tolerance = settings.betaflight_alt_tolerance_m
        ground = settings.betaflight_alt_ground_m
        last_success_at = time.monotonic()
        in_band_ticks = 0
        stable_need = max(1, int(cfg.hz * settings.betaflight_alt_takeoff_stable_s))
        land_final_m = settings.betaflight_land_final_m
        if step.action == "land":
            throttle = (
                step.throttle_us
                if step.throttle_us is not None
                else settings.betaflight_land_throttle_us
            )
        else:
            throttle = step.throttle_us or settings.betaflight_alt_hover_us

        while time.monotonic() < deadline:
            if self._try_interrupt(ser, cfg, start_t):
                return

            channels = self._channels(cfg, throttle_us=throttle, arm_us=self._arm_switch_us())
            self._send_channels(ser, channels)
            with self._lock:
                self._state.elapsed_s = time.monotonic() - start_t

            rel_alt = self._poll_rel_alt_m(ser, cfg, channels)

            if rel_alt is not None:
                target_throttle = throttle
                if done_when_reached:
                    error = target_rel_m - rel_alt
                    if abs(error) <= tolerance:
                        in_band_ticks += 1
                        target_throttle = self._alt_throttle_us(error, step, hold=True)
                        if in_band_ticks >= stable_need:
                            return
                    else:
                        in_band_ticks = 0
                        target_throttle = self._alt_throttle_us(error, step, hold=True)
                elif hold:
                    error = target_rel_m - rel_alt
                    target_throttle = self._alt_throttle_us(error, step, hold=True)
                else:
                    if rel_alt <= max(ground, target_rel_m):
                        self._stream_for(
                            ser,
                            cfg,
                            self._channels(cfg, throttle_us=1000, arm_us=self._arm_switch_us()),
                            0.5,
                            start_t,
                        )
                        return
                    error = target_rel_m - rel_alt
                    if step.action == "land" and rel_alt <= land_final_m:
                        target_throttle = (
                            step.throttle_us
                            if step.throttle_us is not None
                            else settings.betaflight_land_throttle_us
                        )
                    elif step.action == "land":
                        target_throttle = self._alt_throttle_us(error, step, landing=True)
                    else:
                        target_throttle = self._alt_throttle_us(error, step)
                throttle = self._smooth_throttle_us(throttle, target_throttle)
                last_success_at = time.monotonic()
            elif time.monotonic() - last_success_at > 2.5:
                raise RuntimeError("Нет данных барометра (MSP_ALTITUDE). Проверь baro в Betaflight.")

            self._stop_event.wait(interval)

        if done_when_reached:
            if not self._fc_armed(ser, cfg, self._channels(cfg, throttle_us=1000, arm_us=self._arm_switch_us())):
                raise RuntimeError(_arm_fail_message(cfg))
            raise RuntimeError(
                f"Таймаут взлёта: не достигнута высота {target_rel_m:.2f} м за {step.seconds:.1f} с "
                f"(текущая {self._state.current_alt_m or 0:.2f} м). Подними MAX_CLIMB_US / P_GAIN."
            )
        if hold:
            return
        raise RuntimeError(f"Таймаут посадки: не коснулись земли за {step.seconds:.1f} с")

    def _stream_takeoff_alt(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        step: BetaflightSequenceStep,
        start_t: float,
    ) -> None:
        target = step.target_alt_m
        if target is None or target <= 0:
            raise RuntimeError("takeoff_alt требует target_alt_m > 0 (например 1.0)")
        self._ensure_armed(ser, cfg, start_t)
        time.sleep(0.35)
        self._capture_alt_baseline(ser, cfg)
        self._ramp_until_liftoff(ser, cfg, step, start_t)
        with self._lock:
            self._state.target_alt_m = target
        self._stream_alt_loop(
            ser,
            cfg,
            step,
            start_t,
            target_rel_m=target,
            done_when_reached=True,
        )
        settle_s = step.settle_s if step.settle_s is not None else settings.betaflight_alt_takeoff_settle_s
        if settle_s > 0:
            settle_step = BetaflightSequenceStep(
                action="hold_alt",
                seconds=settle_s,
                target_alt_m=target,
                throttle_us=step.throttle_us,
            )
            self._stream_alt_loop(
                ser,
                cfg,
                settle_step,
                start_t,
                target_rel_m=target,
                done_when_reached=False,
                hold=True,
            )

    def _stream_hold_alt(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        step: BetaflightSequenceStep,
        start_t: float,
    ) -> None:
        target = self._active_target_alt_m(step)
        if target is None:
            raise RuntimeError(
                "hold_alt: нет целевой высоты. Задай target_alt_m в takeoff_alt или в этом шаге."
            )
        with self._lock:
            self._state.target_alt_m = target
        self._stream_alt_loop(
            ser,
            cfg,
            step,
            start_t,
            target_rel_m=target,
            done_when_reached=False,
            hold=True,
        )

    def _stream_land_alt(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        step: BetaflightSequenceStep,
        start_t: float,
    ) -> None:
        """Посадка по барометру до земли (относительная высота ~0)."""
        land_step = BetaflightSequenceStep(
            action="land",
            seconds=step.seconds if step.seconds >= 2.0 else settings.betaflight_land_seconds,
            throttle_us=step.throttle_us if step.throttle_us is not None else settings.betaflight_land_throttle_us,
        )
        with self._lock:
            self._state.target_alt_m = 0.0
        self._stream_alt_loop(
            ser,
            cfg,
            land_step,
            start_t,
            target_rel_m=0.0,
            done_when_reached=False,
        )

    def _channels(
        self,
        cfg: BetaflightRunConfig | None,
        *,
        roll_us: int | None = None,
        pitch_us: int | None = None,
        throttle_us: int | None = None,
        yaw_us: int | None = None,
        arm_us: int = 1000,
        angle_us: int | None = None,
    ) -> list[int]:
        channels_count = cfg.channels if cfg is not None else settings.betaflight_rc_channels
        arm_channel = cfg.arm_channel if cfg is not None else settings.betaflight_arm_channel
        angle_channel = cfg.angle_channel if cfg is not None else settings.betaflight_angle_channel
        if angle_us is None:
            angle_us = 2000 if settings.betaflight_enable_angle else 1000

        r = settings.betaflight_stick_center_roll_us if roll_us is None else roll_us
        p = settings.betaflight_stick_center_pitch_us if pitch_us is None else pitch_us
        y = settings.betaflight_stick_center_yaw_us if yaw_us is None else yaw_us
        t = settings.betaflight_idle_throttle_us if throttle_us is None else throttle_us
        values = msp_rc_sticks_to_frame(
            roll_us=_clamp(r),
            pitch_us=_clamp(p),
            throttle_us=_clamp(t),
            yaw_us=_clamp(y),
            rc_map=settings.betaflight_rc_map,
        )
        while len(values) < channels_count:
            values.append(1500)
        if 1 <= arm_channel <= channels_count:
            values[arm_channel - 1] = _clamp(arm_us)
        if 1 <= angle_channel <= channels_count:
            values[angle_channel - 1] = _clamp(angle_us)
        return values[:channels_count]

    def _step_channels(self, step: BetaflightSequenceStep, cfg: BetaflightRunConfig) -> list[int]:
        max_throttle = settings.betaflight_max_throttle_us
        max_delta = settings.betaflight_max_stick_delta
        throttle = min(step.throttle_us or 1000, max_throttle)
        delta = min(step.stick_delta or 100, max_delta)

        if step.action == "arm":
            return self._channels(cfg, throttle_us=1000, arm_us=self._arm_switch_us())
        if step.action in {"neutral", "wait"}:
            return self._channels(cfg, throttle_us=throttle, arm_us=self._arm_switch_us())
        if step.action == "throttle":
            return self._channels(cfg, throttle_us=throttle, arm_us=self._arm_switch_us())
        roll_c = settings.betaflight_stick_center_roll_us
        pitch_c = settings.betaflight_stick_center_pitch_us
        yaw_c = settings.betaflight_stick_center_yaw_us
        if step.action == "forward":
            return self._channels(cfg, pitch_us=pitch_c + delta, throttle_us=throttle, arm_us=self._arm_switch_us())
        if step.action == "back":
            return self._channels(cfg, pitch_us=pitch_c - delta, throttle_us=throttle, arm_us=self._arm_switch_us())
        if step.action == "right":
            return self._channels(cfg, roll_us=roll_c + delta, throttle_us=throttle, arm_us=self._arm_switch_us())
        if step.action == "left":
            return self._channels(cfg, roll_us=roll_c - delta, throttle_us=throttle, arm_us=self._arm_switch_us())
        if step.action == "yaw_right":
            return self._channels(cfg, yaw_us=yaw_c + delta, throttle_us=throttle, arm_us=self._arm_switch_us())
        if step.action == "yaw_left":
            return self._channels(cfg, yaw_us=yaw_c - delta, throttle_us=throttle, arm_us=self._arm_switch_us())
        if step.action == "takeoff_alt":
            return self._channels(cfg, throttle_us=throttle, arm_us=self._arm_switch_us())
        if step.action == "hold_alt":
            return self._channels(cfg, throttle_us=throttle, arm_us=self._arm_switch_us())
        if step.action == "land":
            return self._channels(cfg, throttle_us=throttle, arm_us=self._arm_switch_us())
        if step.action == "disarm":
            return self._channels(cfg, throttle_us=1000, arm_us=1000)
        raise RuntimeError(f"Unknown Betaflight action: {step.action}")

    def _try_interrupt(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        start_t: float,
    ) -> bool:
        """True — выйти из текущего цикла (stop или экстренная посадка)."""
        if not self._stop_event.is_set():
            return False
        pending = self._emergency_land_steps
        if pending:
            self._stop_event.clear()
            with self._lock:
                self._emergency_land_steps = None
                self._abort_remaining_steps = True
            self._execute_steps(ser, cfg, pending, start_t)
            return True
        self._stream_disarm_hold(ser, cfg, start_t=start_t)
        self._finish_stopped()
        return True

    def _finish_stopped(self) -> None:
        with self._lock:
            self._state.status = "stopped"
            self._state.current_action = None
