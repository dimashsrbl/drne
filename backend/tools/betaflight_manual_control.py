#!/usr/bin/env python3
"""
One-shot Betaflight manual control over MSP SET_RAW_RC.

This is not a mission autopilot. It acts like a virtual RC transmitter:
roll/pitch/yaw/throttle/AUX values are streamed continuously while the
process is running. When the process exits, Betaflight will lose RX shortly
after, so each command is intentionally self-contained and disarms on exit.

Examples on Raspberry Pi:
  sudo systemctl stop drone-mission
  cd ~/drone/backend && source .venv/bin/activate

  python3 tools/betaflight_manual_control.py --port /dev/ttyACM0 check
  python3 tools/betaflight_manual_control.py --port /dev/ttyACM0 arm --seconds 5 --yes
  python3 tools/betaflight_manual_control.py --port /dev/ttyACM0 forward --seconds 1.5 --throttle-us 1100 --yes
  python3 tools/betaflight_manual_control.py --port /dev/ttyACM0 land --yes

Betaflight setup:
  - Receiver: MSP
  - USB VCP or chosen UART: MSP enabled at 115200
  - Modes: ARM on AUX1 high, ANGLE on AUX2 high
"""
from __future__ import annotations

import argparse
import struct
import sys
import threading
import time

MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3
MSP_STATUS = 101
MSP_SET_RAW_RC = 200

DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_BAUD = 115200
DEFAULT_CHANNELS = 8


def build_msp_request(cmd: int, payload: bytes = b"") -> bytes:
    size = len(payload)
    data = bytearray((ord("$"), ord("M"), ord("<"), size & 0xFF, cmd & 0xFF))
    data.extend(payload)
    checksum = 0
    for byte in data[3:]:
        checksum ^= byte
    data.append(checksum)
    return bytes(data)


def read_msp_response(ser, expected_cmd: int | None, timeout: float = 0.8) -> tuple[int, bytes] | None:
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


def msp_transaction(ser, cmd: int, payload: bytes = b"", timeout: float = 0.8) -> bytes | None:
    ser.reset_input_buffer()
    ser.write(build_msp_request(cmd, payload))
    ser.flush()
    resp = read_msp_response(ser, cmd, timeout=timeout)
    return resp[1] if resp else None


def parse_status(payload: bytes) -> dict[str, int | str]:
    if len(payload) < 11:
        return {"raw": payload.hex(" ")}
    return {
        "cycle_ms": struct.unpack("<H", payload[0:2])[0],
        "i2c_errors": struct.unpack("<H", payload[2:4])[0],
        "sensors": struct.unpack("<H", payload[4:6])[0],
        "mode_flags": struct.unpack("<I", payload[6:10])[0],
        "profile": payload[10],
    }


def clamp(value: int, low: int = 1000, high: int = 2000) -> int:
    return max(low, min(high, value))


def build_channels(
    *,
    channels: int,
    roll_us: int = 1500,
    pitch_us: int = 1500,
    throttle_us: int = 1000,
    yaw_us: int = 1500,
    arm_us: int = 1000,
    angle_us: int = 2000,
    arm_channel: int = 5,
    angle_channel: int = 6,
) -> list[int]:
    values = [
        clamp(roll_us),
        clamp(pitch_us),
        clamp(throttle_us),
        clamp(yaw_us),
    ]
    while len(values) < channels:
        values.append(1500)

    if 1 <= arm_channel <= channels:
        values[arm_channel - 1] = clamp(arm_us)
    if 1 <= angle_channel <= channels:
        values[angle_channel - 1] = clamp(angle_us)
    return values


class RcStreamer:
    def __init__(self, ser, hz: float, channels: int) -> None:
        self._ser = ser
        self._hz = hz
        self._channels_count = channels
        self._channels = build_channels(channels=channels)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set(self, **kwargs) -> None:
        with self._lock:
            self._channels = build_channels(channels=self._channels_count, **kwargs)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        delay = 1.0 / self._hz
        while not self._stop.is_set():
            with self._lock:
                channels = list(self._channels)
            payload = struct.pack("<" + "H" * len(channels), *channels)
            self._ser.write(build_msp_request(MSP_SET_RAW_RC, payload))
            self._ser.flush()
            time.sleep(delay)


def run_check(ser) -> int:
    api = msp_transaction(ser, MSP_API_VERSION)
    if not api or len(api) < 3:
        print("FAIL: no MSP API_VERSION response")
        print("Check port, close Betaflight Configurator, enable MSP on USB/UART.")
        return 1

    variant = msp_transaction(ser, MSP_FC_VARIANT)
    version = msp_transaction(ser, MSP_FC_VERSION)
    variant_text = variant.decode("ascii", errors="replace") if variant else "?"
    version_text = ".".join(str(b) for b in version[:3]) if version and len(version) >= 3 else "?"
    print(f"OK: MSP connected variant={variant_text} version={version_text}")

    status = msp_transaction(ser, MSP_STATUS)
    if status:
        print("STATUS:", parse_status(status))
    return 0


def confirm(args: argparse.Namespace, text: str) -> None:
    if args.yes:
        return
    print("=" * 64)
    print(text)
    print("Remove propellers for bench tests. The command streams virtual RC.")
    print("=" * 64)
    answer = input("Continue? [y/N]: ").strip().lower()
    if answer not in {"y", "yes", "д", "да"}:
        raise SystemExit("Cancelled.")


def hold(streamer: RcStreamer, seconds: float, label: str) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        print(f"{label}: {remaining:.1f}s", end="\r", flush=True)
        time.sleep(min(0.25, max(0.0, remaining)))
    print()


def command_values(command: str, delta: int) -> dict[str, int]:
    if command == "forward":
        return {"pitch_us": 1500 + delta}
    if command == "back":
        return {"pitch_us": 1500 - delta}
    if command == "right":
        return {"roll_us": 1500 + delta}
    if command == "left":
        return {"roll_us": 1500 - delta}
    if command == "yaw-right":
        return {"yaw_us": 1500 + delta}
    if command == "yaw-left":
        return {"yaw_us": 1500 - delta}
    return {}


def run_manual_command(ser, args: argparse.Namespace) -> int:
    if args.command == "check":
        return run_check(ser)

    confirm(args, f"Betaflight command: {args.command}")
    streamer = RcStreamer(ser, hz=args.hz, channels=args.channels)

    try:
        streamer.start()

        # Always start disarmed so Betaflight sees a clean ARM switch transition.
        streamer.set(
            throttle_us=1000,
            arm_us=1000,
            angle_us=2000,
            arm_channel=args.arm_channel,
            angle_channel=args.angle_channel,
        )
        time.sleep(1.0)

        if args.command == "stop":
            streamer.set(
                throttle_us=1000,
                arm_us=2000 if args.armed else 1000,
                angle_us=2000,
                arm_channel=args.arm_channel,
                angle_channel=args.angle_channel,
            )
            hold(streamer, args.seconds, "neutral")
            return 0

        print("ARM ON")
        streamer.set(
            throttle_us=1000,
            arm_us=2000,
            angle_us=2000,
            arm_channel=args.arm_channel,
            angle_channel=args.angle_channel,
        )
        time.sleep(args.prearm_seconds)

        if args.command == "arm":
            hold(streamer, args.seconds, "armed")
            return 0

        if args.command == "land":
            start = clamp(args.throttle_us)
            steps = max(1, int(args.seconds * 10))
            for step in range(steps):
                throttle = int(start + (1000 - start) * (step / steps))
                streamer.set(
                    throttle_us=throttle,
                    arm_us=2000,
                    angle_us=2000,
                    arm_channel=args.arm_channel,
                    angle_channel=args.angle_channel,
                )
                time.sleep(args.seconds / steps)
            return 0

        values = command_values(args.command, args.stick_delta)
        streamer.set(
            throttle_us=args.throttle_us,
            arm_us=2000,
            angle_us=2000,
            arm_channel=args.arm_channel,
            angle_channel=args.angle_channel,
            **values,
        )
        hold(streamer, args.seconds, args.command)
        return 0
    finally:
        print("DISARM")
        try:
            streamer.set(
                throttle_us=1000,
                arm_us=1000,
                angle_us=2000,
                arm_channel=args.arm_channel,
                angle_channel=args.angle_channel,
            )
            time.sleep(0.8)
        finally:
             streamer.stop()

 