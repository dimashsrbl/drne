#!/usr/bin/env python3
"""
Betaflight на Pi: arm/disarm через MSP SET_RAW_RC (без backend и без MAVLink).

Нужно в Betaflight Configurator:
  1) Receiver → MSP
  2) Ports → USB VCP → MSP ON @ 115200
  3) Modes → ARM на AUX1 (канал 5), ANGLE на AUX2 (канал 6) — или arm с пульта
  4) Save

Примеры на Pi (USB к FC):
  sudo systemctl stop drone-mission   # освободить порт

  cd ~/drone/backend && source .venv/bin/activate

  python3 tools/betaflight_arm_test.py --check
  python3 tools/betaflight_arm_test.py --arm-only --yes
  python3 tools/betaflight_arm_test.py --spin-seconds 2 --throttle-us 1100 --yes

Порт по умолчанию: /dev/ttyACM0 (USB). GPIO: --port /dev/serial0

⚠️  Без пропеллеров на первом тесте. Betaflight не поддерживает GPS-миссии как INAV.
    Для миссий позже — Pixhawk + ArduPilot (профиль ardupilot в backend).
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


def build_msp_request(cmd: int, payload: bytes = b"") -> bytes:
    size = len(payload)
    data = bytearray((ord("$"), ord("M"), ord("<"), size & 0xFF, cmd & 0xFF))
    data.extend(payload)
    ck = 0
    for b in data[3:]:
        ck ^= b
    data.append(ck)
    return bytes(data)


def read_msp_response(ser, expected_cmd: int | None, timeout: float = 0.8) -> tuple[int, bytes] | None:
    """Читает один MSP-ответ $M> cmd payload."""
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
            need = 6 + size
            if len(buf) < need:
                break
            cmd = buf[4]
            payload = bytes(buf[5 : 5 + size])
            del buf[:need]
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


def parse_status(payload: bytes) -> dict:
    """Betaflight MSP_STATUS (минимум 11 байт)."""
    if len(payload) < 11:
        return {"raw": payload.hex(" ")}
    cycle = struct.unpack("<H", payload[0:2])[0]
    i2c_err = struct.unpack("<H", payload[2:4])[0]
    sensors = struct.unpack("<H", payload[4:6])[0]
    mode_flags = struct.unpack("<I", payload[6:10])[0]
    profile = payload[10]
    return {
        "cycle_ms": cycle,
        "i2c_errors": i2c_err,
        "sensors": sensors,
        "mode_flags": mode_flags,
        "profile": profile,
    }


def build_channels(
    num_channels: int,
    throttle_us: int = 1000,
    arm_us: int = 1000,
    angle_us: int = 1000,
    arm_channel: int = 5,
    angle_channel: int = 6,
) -> list[int]:
    """AETR + AUX. Каналы 1-based для arm_channel/angle_channel."""
    ch = [1500, 1500, throttle_us, 1500]  # roll, pitch, throttle, yaw
    while len(ch) < num_channels:
        ch.append(1500)
    if 1 <= arm_channel <= num_channels:
        ch[arm_channel - 1] = arm_us
    if 1 <= angle_channel <= num_channels:
        ch[angle_channel - 1] = angle_us
    return ch


class RcStreamer:
    """Betaflight MSP RX требует непрерывный поток SET_RAW_RC."""

    def __init__(self, ser, hz: float = 25.0, num_channels: int = 8) -> None:
        self._ser = ser
        self._hz = hz
        self._num_channels = num_channels
        self._channels = build_channels(num_channels)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_channels(self, **kwargs) -> None:
        with self._lock:
            self._channels = build_channels(self._num_channels, **kwargs)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="bf-msp-rc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        dt = 1.0 / self._hz
        while not self._stop.is_set():
            with self._lock:
                ch = list(self._channels)
            payload = struct.pack("<" + "H" * len(ch), *ch)
            try:
                self._ser.write(build_msp_request(MSP_SET_RAW_RC, payload))
                self._ser.flush()
            except Exception:
                pass
            time.sleep(dt)


def confirm(args: argparse.Namespace) -> None:
    if args.yes:
        return
    print()
    print("=" * 60)
    print("Betaflight: возможен ARM и вращение моторов!")
    print("Сними пропеллеры или надёжно зафиксируй дрон.")
    print("=" * 60)
    ans = input("Продолжить? [y/N]: ").strip().lower()
    if ans not in ("y", "yes", "д", "да"):
        print("Отменено.")
        raise SystemExit(0)


def run_check(ser) -> int:
    api = msp_transaction(ser, MSP_API_VERSION)
    if not api or len(api) < 3:
        print("FAIL: нет ответа MSP API_VERSION")
        print("Проверь: Betaflight → Receiver=MSP, Ports → USB MSP ON, порт свободен")
        return 1
    variant = msp_transaction(ser, MSP_FC_VARIANT)
    version = msp_transaction(ser, MSP_FC_VERSION)
    var_str = variant.decode("ascii", errors="replace") if variant else "?"
    ver = ".".join(str(b) for b in version[:3]) if version and len(version) >= 3 else "?"
    print(f"OK: Betaflight/MSP  variant={var_str}  version={ver}")
    st = msp_transaction(ser, MSP_STATUS)
    if st:
        print("STATUS:", parse_status(st))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Betaflight arm/disarm через MSP с Pi")
    ap.add_argument("--port", default=DEFAULT_PORT, help="USB: /dev/ttyACM0, GPIO: /dev/serial0")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--channels", type=int, default=8, help="Число RC-каналов в SET_RAW_RC")
    ap.add_argument("--arm-channel", type=int, default=5, help="AUX для ARM (обычно 5 = AUX1)")
    ap.add_argument("--angle-channel", type=int, default=6, help="AUX для ANGLE (обычно 6 = AUX2)")
    ap.add_argument("--check", action="store_true", help="Только проверка MSP связи")
    ap.add_argument("--arm-only", action="store_true", help="ARM → пауза → DISARM")
    ap.add_argument("--hold", type=float, default=4.0, help="Секунд держать ARM")
    ap.add_argument("--spin-seconds", type=float, default=0.0, help="Секунд газа после ARM")
    ap.add_argument("--throttle-us", type=int, default=1100, help="Газ в мкс (1000=min, осторожно)")
    ap.add_argument("--yes", action="store_true", help="Без подтверждения")
    args = ap.parse_args()

    try:
        import serial
    except ImportError:
        print("Нужен pyserial: pip install pyserial", file=sys.stderr)
        return 1

    print(f"Port: {args.port} @ {args.baud}")
    try:
        ser = serial.Serial(args.port, baudrate=args.baud, timeout=0.2)
    except serial.SerialException as e:
        print(f"FAIL: не открыть {args.port!r}: {e}", file=sys.stderr)
        print("sudo systemctl stop drone-mission  &&  ls -l /dev/ttyACM*", file=sys.stderr)
        return 1

    streamer: RcStreamer | None = None
    try:
        if args.check:
            return run_check(ser)

        confirm(args)

        streamer = RcStreamer(ser, num_channels=args.channels)
        streamer.start()
        time.sleep(0.3)

        print("→ RC stream: ANGLE + ARM OFF (стабилизация)")
        streamer.set_channels(
            throttle_us=1000,
            arm_us=1000,
            angle_us=2000,
            arm_channel=args.arm_channel,
            angle_channel=args.angle_channel,
        )
        time.sleep(1.0)

        print("→ ARM ON (AUX high)")
        streamer.set_channels(
            throttle_us=1000,
            arm_us=2000,
            angle_us=2000,
            arm_channel=args.arm_channel,
            angle_channel=args.angle_channel,
        )

        deadline = time.monotonic() + args.hold
        while time.monotonic() < deadline:
            st = msp_transaction(ser, MSP_STATUS, timeout=0.3)
            if st:
                info = parse_status(st)
                print(f"  status mode_flags=0x{info.get('mode_flags', 0):08X}")
            time.sleep(0.5)

        if args.spin_seconds > 0:
            print(f"→ throttle {args.throttle_us} us на {args.spin_seconds} s")
            streamer.set_channels(
                throttle_us=args.throttle_us,
                arm_us=2000,
                angle_us=2000,
                arm_channel=args.arm_channel,
                angle_channel=args.angle_channel,
            )
            time.sleep(args.spin_seconds)
            streamer.set_channels(
                throttle_us=1000,
                arm_us=2000,
                angle_us=2000,
                arm_channel=args.arm_channel,
                angle_channel=args.angle_channel,
            )
            time.sleep(0.5)

        print("→ DISARM")
        streamer.set_channels(
            throttle_us=1000,
            arm_us=1000,
            angle_us=2000,
            arm_channel=args.arm_channel,
            angle_channel=args.angle_channel,
        )
        time.sleep(1.0)
        print("Готово.")
        return 0
    finally:
        if streamer is not None:
            streamer.stop()
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
