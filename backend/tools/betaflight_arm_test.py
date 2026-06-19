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

⚠️  Без пропеллеров. В Betaflight: ``feature MOTOR_STOP`` + save — иначе моторы крутятся
    уже на idle при ARM. ``--arm-only`` делает DISARM ~1 с после успешного ARM.
"""
from __future__ import annotations

import argparse
import struct
import sys
import threading
import time

try:
    from app.services.inav_msp import (
        BETAFLIGHT_IDLE_THROTTLE_US,
        decode_arming_disable_flags,
        msp_rc_sticks_to_frame,
        msp_rcmap_to_wire_map,
        msp_status_armed,
        parse_msp_status,
    )
except ImportError:
    BETAFLIGHT_IDLE_THROTTLE_US = 988

    def decode_arming_disable_flags(flags: int) -> list[str]:
        names: list[str] = []
        for bit, name in (
            (7, "THROTTLE"),
            (8, "ANGLE"),
            (13, "CLI"),
            (16, "MSP"),
        ):
            if flags & (1 << bit):
                names.append(name)
        return names or ([f"0x{flags:08X}"] if flags else [])

    def parse_msp_status(payload: bytes, *, is_ex: bool = False) -> dict:
        if len(payload) < 16:
            return {}
        mode_low = struct.unpack("<I", payload[6:10])[0]
        byte_count = payload[15]
        extra = payload[16 : 16 + byte_count]
        mode_bits = mode_low | (int.from_bytes(extra, "little") << 32 if extra else 0)
        arming_disable = None
        off = 16 + byte_count
        if len(payload) >= off + 5:
            arming_disable = struct.unpack("<I", payload[off + 1 : off + 5])[0]
        return {"mode_bits": mode_bits, "mode_flags": mode_low, "arming_disable": arming_disable}

    def msp_status_armed(payload: bytes, *, is_ex: bool = False) -> bool:
        return bool(int(parse_msp_status(payload, is_ex=is_ex).get("mode_bits", 0)) & 1)

    def msp_rcmap_to_wire_map(rcmap: list[int]) -> str:
        letters = "AERT"
        out = ["?"] * 4
        for letter_idx, wire_idx in enumerate(rcmap[:4]):
            if 0 <= wire_idx < 4:
                out[wire_idx] = letters[letter_idx]
        wire = "".join(out)
        return wire if "?" not in wire else "AETR"

    def msp_rc_sticks_to_frame(
        *,
        roll_us: int = 1500,
        pitch_us: int = 1500,
        throttle_us: int = BETAFLIGHT_IDLE_THROTTLE_US,
        yaw_us: int = 1500,
        rc_map: str = "AETR",
    ) -> list[int]:
        sticks = {"A": roll_us, "E": pitch_us, "T": throttle_us, "R": yaw_us}
        letters = (rc_map or "AETR").strip().upper()[:4] or "AETR"
        frame = [1500, 1500, 1500, 1500]
        for idx, letter in enumerate(letters):
            if letter in sticks:
                frame[idx] = int(sticks[letter])
        return frame

MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3
MSP_STATUS = 101
MSP_MOTOR = 104
MSP_RC = 105
MSP_RX_CONFIG = 44
MSP_RX_MAP = 64
MSP_MODE_RANGES = 34
MSP_FEATURE_CONFIG = 36
MSP_STATUS_EX = 150
MSP_SET_ARMING_DISABLED = 99  # 0 = разрешить ARM (снять ARMING_DISABLED_MSP)
MSP_SET_RAW_RC = 200

# armingDisableFlags_e — см. inav_msp.ARMING_DISABLE_FLAG_NAMES
ARMING_DISABLE_NAMES = {
    0: "NO_GYRO",
    1: "FAILSAFE",
    2: "RX_FAILSAFE",
    7: "THROTTLE",
    8: "ANGLE",
    13: "CLI",
    16: "MSP",
    24: "MOTOR_PROTOCOL",
    28: "ARM_SWITCH",
}

FEATURE_RX_MSP_BIT = 1 << 14
BOX_PERMANENT_ARM = 0
BOX_PERMANENT_ANGLE = 1
# ANGLE — 2-й active box после ARM; bit1 в mode_bits = режим ANGLE включён
MODE_BITS_ANGLE = 1 << 1

# rcData[] в Betaflight (MSP_RC): Roll, Pitch, Yaw, Throttle, AUX1…
RC_DATA_LABELS = ("Roll", "Pitch", "Yaw", "Throttle", "AUX1", "AUX2", "AUX3", "AUX4")

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


def msp_transaction(
    ser,
    cmd: int,
    payload: bytes = b"",
    timeout: float = 0.8,
    *,
    reset_rx: bool = True,
    io_lock: threading.Lock | None = None,
) -> bytes | None:
    lock = io_lock or threading.Lock()
    with lock:
        if reset_rx:
            ser.reset_input_buffer()
        ser.write(build_msp_request(cmd, payload))
        ser.flush()
    resp = read_msp_response(ser, cmd, timeout=timeout)
    return resp[1] if resp else None


def parse_msp_rc(payload: bytes) -> list[int]:
    return [struct.unpack("<H", payload[i : i + 2])[0] for i in range(0, len(payload) - 1, 2)]


def msp_allow_arming(ser, *, io_lock: threading.Lock | None = None) -> None:
    lock = io_lock or threading.Lock()
    with lock:
        ser.write(build_msp_request(MSP_SET_ARMING_DISABLED, b"\x00"))
        ser.flush()
    time.sleep(0.05)


def format_rc_channels(values: list[int]) -> str:
    parts: list[str] = []
    for i, v in enumerate(values[:8]):
        label = RC_DATA_LABELS[i] if i < len(RC_DATA_LABELS) else f"ch{i + 1}"
        parts.append(f"{label}={v}")
    return ", ".join(parts)


def decode_arming_disable(flags: int) -> list[str]:
    try:
        return decode_arming_disable_flags(flags)
    except NameError:
        names: list[str] = []
        for bit, name in sorted(ARMING_DISABLE_NAMES.items()):
            if flags & (1 << bit):
                names.append(name)
        if not names and flags:
            names.append(f"0x{flags:08X}")
        return names


def parse_status(payload: bytes) -> dict:
    """Обёртка над parse_msp_status (Betaflight 1.47+)."""
    parsed = parse_msp_status(payload, is_ex=False)
    if not parsed:
        return {"raw": payload.hex(" ")}
    return {
        "cycle_ms": parsed.get("cycle_us", 0) // 1000,
        "i2c_errors": parsed.get("i2c_errors", 0),
        "sensors": parsed.get("sensors", 0),
        "mode_flags": parsed.get("mode_bits", 0),
        "profile": parsed.get("profile", 0),
        "arming_disable": parsed.get("arming_disable"),
    }


def parse_msp_motor(payload: bytes) -> list[int]:
    return [
        struct.unpack("<H", payload[i : i + 2])[0]
        for i in range(0, len(payload) - 1, 2)
    ]


def motors_active(payload: bytes, *, min_us: int = 1000) -> bool:
    values = parse_msp_motor(payload)
    return any(v > min_us for v in values[:4])


def parse_rx_config(payload: bytes) -> dict:
    """MSP_RX_CONFIG: mincheck на offset 5 (U16 LE)."""
    if len(payload) < 7:
        return {}
    return {
        "maxcheck": struct.unpack("<H", payload[1:3])[0],
        "midrc": struct.unpack("<H", payload[3:5])[0],
        "mincheck": struct.unpack("<H", payload[5:7])[0],
    }


def mode_step_to_us(step: int) -> int:
    return 900 + int(step) * 25


def parse_mode_ranges(payload: bytes) -> list[dict]:
    """MSP_MODE_RANGES: 20× (permanentId, auxIdx, startStep, endStep)."""
    entries: list[dict] = []
    for i in range(0, min(len(payload), 80), 4):
        if i + 4 > len(payload):
            break
        permanent_id, aux_idx, start, end = payload[i : i + 4]
        if start >= end:
            continue
        entries.append(
            {
                "permanent_id": permanent_id,
                "aux_idx": aux_idx,
                "start": start,
                "end": end,
                "msp_channel": aux_idx + 5,
                "range_us": (mode_step_to_us(start), mode_step_to_us(end) - 1),
            }
        )
    return entries


def find_mode_range(entries: list[dict], permanent_id: int) -> dict | None:
    for entry in entries:
        if entry["permanent_id"] == permanent_id:
            return entry
    return None


def format_mode_range(entry: dict) -> str:
    lo, hi = entry["range_us"]
    name = {BOX_PERMANENT_ARM: "ARM", BOX_PERMANENT_ANGLE: "ANGLE"}.get(
        entry["permanent_id"], f"id{entry['permanent_id']}"
    )
    return (
        f"{name}: AUX{entry['aux_idx'] + 1} "
        f"(MSP ch {entry['msp_channel']}) "
        f"{lo}-{hi} µs"
    )


def read_fc_mode_ranges(ser, *, io_lock: threading.Lock | None = None) -> list[dict]:
    payload = msp_transaction(ser, MSP_MODE_RANGES, timeout=0.4, io_lock=io_lock)
    if not payload:
        return []
    return parse_mode_ranges(payload)


def resolve_aux_channels(
    args: argparse.Namespace, ranges: list[dict]
) -> tuple[int, int, list[str]]:
    """Подстроить arm/angle channel под FC или выдать WARN."""
    notes: list[str] = []
    arm_ch = args.arm_channel
    angle_ch = args.angle_channel
    arm = find_mode_range(ranges, BOX_PERMANENT_ARM)
    angle = find_mode_range(ranges, BOX_PERMANENT_ANGLE)
    if arm:
        fc_arm = arm["msp_channel"]
        if fc_arm != arm_ch:
            notes.append(
                f"WARN: FC ARM на MSP ch {fc_arm} (AUX{arm['aux_idx'] + 1}), "
                f"скрипт шлёт ch {arm_ch} → используем ch {fc_arm}"
            )
            arm_ch = fc_arm
    else:
        notes.append("WARN: FC не сообщает режим ARM (aux пуст?) — задай в CLI: aux 0 0 0 1700 2100")
    if angle:
        fc_angle = angle["msp_channel"]
        if fc_angle != angle_ch:
            notes.append(
                f"WARN: FC ANGLE на MSP ch {fc_angle}, скрипт шлёт ch {angle_ch} → ch {fc_angle}"
            )
            angle_ch = fc_angle
    return arm_ch, angle_ch, notes


def read_fc_wire_map(ser, *, io_lock: threading.Lock | None = None) -> str | None:
    payload = msp_transaction(ser, MSP_RX_MAP, timeout=0.4, io_lock=io_lock)
    if not payload or len(payload) < 4:
        return None
    rcmap = list(payload[:4])
    return msp_rcmap_to_wire_map(rcmap)


def build_channels(
    num_channels: int,
    throttle_us: int = BETAFLIGHT_IDLE_THROTTLE_US,
    arm_us: int = 1000,
    angle_us: int = 1000,
    yaw_us: int = 1500,
    arm_channel: int = 5,
    angle_channel: int = 6,
    rc_map: str = "AETR",
) -> list[int]:
    """AETR/AERT map + AUX. arm_channel 5 = AUX1 в Configurator."""
    ch = msp_rc_sticks_to_frame(
        throttle_us=throttle_us,
        yaw_us=yaw_us,
        rc_map=rc_map,
    )
    while len(ch) < num_channels:
        ch.append(1500)
    if 1 <= arm_channel <= num_channels:
        ch[arm_channel - 1] = arm_us
    if 1 <= angle_channel <= num_channels:
        ch[angle_channel - 1] = angle_us
    return ch


class RcStreamer:
    """Betaflight MSP RX требует непрерывный поток SET_RAW_RC."""

    def __init__(
        self,
        ser,
        hz: float = 100.0,
        num_channels: int = 8,
        io_lock: threading.Lock | None = None,
    ) -> None:
        self._ser = ser
        self._hz = hz
        self._num_channels = num_channels
        self._rc_map = "AETR"
        self._channels = build_channels(num_channels)
        self._lock = threading.Lock()
        self._io_lock = io_lock or threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_channels(self, **kwargs) -> None:
        with self._lock:
            if "rc_map" in kwargs:
                self._rc_map = kwargs.pop("rc_map")
            self._channels = build_channels(
                self._num_channels, rc_map=self._rc_map, **kwargs
            )

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
                with self._io_lock:
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
    print("Пропеллеры СНЯТЫ. Без feature MOTOR_STOP моторы крутятся на idle при ARM.")
    print("Рекомендуется в CLI:  feature MOTOR_STOP  →  save")
    print("=" * 60)
    ans = input("Продолжить? [y/N]: ").strip().lower()
    if ans not in ("y", "yes", "д", "да"):
        print("Отменено.")
        raise SystemExit(0)


def run_check(ser, args: argparse.Namespace) -> int:
    api = msp_transaction(ser, MSP_API_VERSION)
    if not api or len(api) < 3:
        print("FAIL: нет ответа MSP API_VERSION")
        print("Проверь: Betaflight → Receiver=MSP, Ports → USB MSP ON, порт свободен")
        print("Configurator должен быть ЗАКРЫТ (иначе порт занят / CLI блокирует ARM).")
        return 1
    variant = msp_transaction(ser, MSP_FC_VARIANT)
    version = msp_transaction(ser, MSP_FC_VERSION)
    var_str = variant.decode("ascii", errors="replace") if variant else "?"
    ver = ".".join(str(b) for b in version[:3]) if version and len(version) >= 3 else "?"
    print(f"OK: Betaflight/MSP  variant={var_str}  version={ver}")

    io_lock = threading.Lock()
    fc_map = read_fc_wire_map(ser, io_lock=io_lock)
    rx_cfg_payload = msp_transaction(ser, MSP_RX_CONFIG, timeout=0.4, io_lock=io_lock)
    rx_cfg = parse_rx_config(rx_cfg_payload) if rx_cfg_payload else {}
    mincheck = int(rx_cfg.get("mincheck", 1050))
    idle_throttle = args.idle_throttle_us

    if fc_map:
        print(f"FC map (MSP_RX_MAP): {fc_map}1234  — для SET_RAW_RC нужен --rc-map {fc_map}")
    if rx_cfg:
        print(f"mincheck={mincheck}  (газ для ARM: rcData[Throttle] < {mincheck}, шлём {idle_throttle})")

    rc_map = args.rc_map.upper()[:4] or "AETR"
    if fc_map and rc_map != fc_map:
        print(
            f"WARN: --rc-map {rc_map} ≠ FC map {fc_map}. "
            f"Газ может попасть не в rcData[Throttle] → arming_disable THROTTLE."
        )
        print(f"      Попробуй: --rc-map {fc_map}")

    mode_ranges = read_fc_mode_ranges(ser, io_lock=io_lock)
    arm_ch, angle_ch, mode_notes = resolve_aux_channels(args, mode_ranges)
    for note in mode_notes:
        print(note)
    for entry in mode_ranges:
        if entry["permanent_id"] in (BOX_PERMANENT_ARM, BOX_PERMANENT_ANGLE):
            print(f"FC mode: {format_mode_range(entry)}")

    feat_payload = msp_transaction(ser, MSP_FEATURE_CONFIG, timeout=0.35, io_lock=io_lock)
    if feat_payload and len(feat_payload) >= 4:
        features = struct.unpack("<I", feat_payload[:4])[0]
        if not (features & FEATURE_RX_MSP_BIT):
            print("WARN: feature RX_MSP выключен — Receiver должен быть MSP в Configurator + save.")

    st = msp_transaction(ser, MSP_STATUS, io_lock=io_lock)
    if st:
        print("STATUS (без RC-потока):", parse_status(st))
        print("  ↑ mode_flags=0 здесь нормально, пока не идёт SET_RAW_RC")

    msp_allow_arming(ser, io_lock=io_lock)
    streamer = RcStreamer(ser, num_channels=args.channels, io_lock=io_lock)
    streamer._rc_map = rc_map
    streamer.start()
    time.sleep(0.4)
    streamer.set_channels(
        rc_map=rc_map,
        throttle_us=idle_throttle,
        arm_us=2000,
        angle_us=2000,
        arm_channel=arm_ch,
        angle_channel=angle_ch,
    )
    time.sleep(0.8)
    rc_payload = msp_transaction(
        ser, MSP_RC, timeout=0.4, reset_rx=False, io_lock=io_lock
    )
    st2 = msp_transaction(
        ser, MSP_STATUS, timeout=0.35, reset_rx=False, io_lock=io_lock
    )
    streamer.stop()

    if not rc_payload:
        print("FAIL: нет MSP_RC во время потока SET_RAW_RC")
        print("Receiver=MSP? USB VCP MSP ON? Configurator закрыт?")
        return 1

    rc = parse_msp_rc(rc_payload)
    print(f"MSP_RC (ARM ON, ch {arm_ch}): {format_rc_channels(rc)}")
    arm_idx = arm_ch - 1
    angle_idx = angle_ch - 1
    arm_us = rc[arm_idx] if 0 <= arm_idx < len(rc) else None
    angle_us = rc[angle_idx] if 0 <= angle_idx < len(rc) else None
    throttle_rc = rc[3] if len(rc) >= 4 else None
    status2 = parse_status(st2) if st2 else {}
    mode_bits = int(status2.get("mode_flags", 0))
    armed = msp_status_armed(st2) if st2 else False
    disable_mask = status2.get("arming_disable")

    print(f"mode_bits=0x{mode_bits:08X}  armed={'YES' if armed else 'NO'}")
    if disable_mask is not None:
        reasons = decode_arming_disable(disable_mask)
        print(
            f"arming_disable=0x{disable_mask:08X}"
            + (f"  ({', '.join(reasons)})" if reasons else "  (нет блокировок)")
        )

    ok = True
    if arm_us is None or arm_us < 1700:
        ok = False
        print(
            f"FAIL: канал {args.arm_channel} (AUX1 в UI) = {arm_us} — FC не видит ARM."
        )
        print("  Modes: ARM на AUX1 (не AUX5), диапазон 1700-2100.")
    if angle_us is not None and angle_us < 1700:
        print(
            f"WARN: канал {angle_ch} (AUX2) = {angle_us} — ANGLE не активен."
        )
        print("  Modes: ANGLE на AUX2, диапазон 1700-2100 (backend шлет 2000).")
    elif angle_us is not None and angle_us >= 1700 and (mode_bits & MODE_BITS_ANGLE):
        print("  OK: FC видит режим ANGLE (mode_bits bit1).")
    elif angle_us is not None and angle_us >= 1700 and mode_bits < MODE_BITS_ANGLE:
        ok = False
        print(
            f"FAIL: AUX2/ch{angle_ch}={angle_us}, но FC не активировал ANGLE "
            f"(mode_bits=0x{mode_bits:08X}, нужен bit1). "
            "Modes не работают — feature RX_MSP, aux, save, power cycle."
        )
    if throttle_rc is not None and throttle_rc >= mincheck:
        ok = False
        print(
            f"FAIL: rcData[Throttle]={throttle_rc} ≥ mincheck={mincheck}. "
            f"Нужно < {mincheck} (обычно --idle-throttle-us {mincheck - 1} или 988)."
        )
        if fc_map and rc_map != fc_map:
            print(f"      Или неверный --rc-map: FC={fc_map}, вы шлёте {rc_map}.")
    if not armed:
        if disable_mask:
            reasons = decode_arming_disable(int(disable_mask))
            if reasons == ["ARM_SWITCH"] or disable_mask == (1 << 28):
                print(
                    "  ARM switch ON, но FC ещё не armed — часто помогает: "
                    "ARM OFF 2s → ARM ON, power cycle, msp_allow_arming."
                )
            print("  ↑ arming_disable есть — см. список выше.")
        elif throttle_rc is not None and throttle_rc < mincheck and (arm_us or 0) >= 1700:
            print(
                "  AUX+гaz OK, arming_disable=0, но armed=NO.\n"
                "  → power cycle FC (USB off/on)\n"
                "  → Betaflight CLI: status (ищи ARMING DISABLED: ...)\n"
                "  → Modes: aux 0 0 0 1700 2100  (ARM AUX1)\n"
                "  → set small_angle = 180  (если дрон наклонён на столе)"
            )
        print(
            "ARM не произошёл. Частые причины:\n"
            "  1) ARMING_DISABLED_MSP — power cycle после Configurator\n"
            "  2) feature RX_MSP / Receiver=MSP не сохранены\n"
            "  3) ARM не на AUX1 или диапазон не включает 2000\n"
            "  4) ANGLE/small_angle — дрон слишком наклонён (arming_disable ANGLE)\n"
            "  5) CLI открыт — Configurator полностью закрыт?"
        )
        ok = False
    elif ok:
        print("OK: FC armed через MSP.")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Betaflight arm/disarm через MSP с Pi")
    ap.add_argument("--port", default=DEFAULT_PORT, help="USB: /dev/ttyACM0, GPIO: /dev/serial0")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--channels", type=int, default=8, help="Число RC-каналов в SET_RAW_RC")
    ap.add_argument(
        "--arm-channel",
        type=int,
        default=5,
        help="Канал MSP для ARM: 5 = AUX1 в Configurator (не «AUX 5»!)",
    )
    ap.add_argument(
        "--angle-channel",
        type=int,
        default=6,
        help="Канал MSP для ANGLE: 6 = AUX2 в Configurator",
    )
    ap.add_argument(
        "--rc-map",
        default="AETR",
        help="map Betaflight для первых 4 каналов MSP (должен совпадать с CLI ``map``)",
    )
    ap.add_argument(
        "--idle-throttle-us",
        type=int,
        default=BETAFLIGHT_IDLE_THROTTLE_US,
        help="Газ «стоп» для ARM (µs, должно быть < mincheck; по умолч. 988)",
    )
    ap.add_argument("--check", action="store_true", help="Проверка MSP + ARM (read-only тест)")
    ap.add_argument("--arm-only", action="store_true", help="ARM → пауза → DISARM")
    ap.add_argument(
        "--quiet-arm-seconds",
        type=float,
        default=5.0,
        help="Секунд только RC-поток без MSP-опроса (перед проверкой armed)",
    )
    ap.add_argument(
        "--hold",
        type=float,
        default=10.0,
        help="Макс. секунд ждать ARM (опрос MSP); после ARM — disarm-after-arm-s",
    )
    ap.add_argument(
        "--disarm-after-arm-s",
        type=float,
        default=1.0,
        help="После успешного ARM держать столько секунд, затем DISARM (без --spin-seconds)",
    )
    ap.add_argument(
        "--stick-arm",
        action="store_true",
        help="Если AUX ARM не работает: arm через sticks (yaw right + throttle low)",
    )
    ap.add_argument("--spin-seconds", type=float, default=0.0, help="Секунд газа после ARM (осторожно!)")
    ap.add_argument(
        "--throttle-us",
        type=int,
        default=1020,
        help="Газ для --spin-seconds (1020=max bench без props; >1050 только с --force-throttle)",
    )
    ap.add_argument(
        "--force-throttle",
        action="store_true",
        help="Разрешить --throttle-us > 1050 (опасно на столе)",
    )
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
    io_lock = threading.Lock()
    armed_seen = False
    try:
        if args.check:
            return run_check(ser, args)

        confirm(args)

        spin_throttle = args.throttle_us
        if args.spin_seconds > 0 and spin_throttle > 1050 and not args.force_throttle:
            print(
                f"WARN: throttle-us={spin_throttle} > 1050 — ограничиваем до 1020 "
                "(используй --force-throttle если осознанно)."
            )
            spin_throttle = 1020

        rc_map = args.rc_map.upper()[:4] or "AETR"
        idle = args.idle_throttle_us
        mode_ranges = read_fc_mode_ranges(ser, io_lock=io_lock)
        arm_ch, angle_ch, mode_notes = resolve_aux_channels(args, mode_ranges)
        for note in mode_notes:
            print(note)
        for entry in mode_ranges:
            if entry["permanent_id"] in (BOX_PERMANENT_ARM, BOX_PERMANENT_ANGLE):
                print(f"FC mode: {format_mode_range(entry)}")

        streamer = RcStreamer(ser, num_channels=args.channels, io_lock=io_lock)
        streamer._rc_map = rc_map
        msp_allow_arming(ser, io_lock=io_lock)
        streamer.start()
        time.sleep(0.3)

        print("→ RC stream: ANGLE + ARM OFF (стабилизация, 2.5 s)")
        streamer.set_channels(
            rc_map=rc_map,
            throttle_us=idle,
            arm_us=1000,
            angle_us=2000,
            arm_channel=arm_ch,
            angle_channel=angle_ch,
        )
        time.sleep(2.5)

        if args.stick_arm:
            print("→ stick ARM (yaw right 2 s, AUX off)")
            streamer.set_channels(
                rc_map=rc_map,
                throttle_us=idle,
                yaw_us=2000,
                arm_us=1000,
                angle_us=2000,
                arm_channel=arm_ch,
                angle_channel=angle_ch,
            )
            time.sleep(2.0)
        else:
            print("→ ARM ON (AUX high)")
            streamer.set_channels(
                rc_map=rc_map,
                throttle_us=idle,
                arm_us=2000,
                angle_us=2000,
                arm_channel=arm_ch,
                angle_channel=angle_ch,
            )

        quiet = max(0.0, float(args.quiet_arm_seconds))
        if quiet > 0:
            print(f"→ RC only {quiet:.1f}s (без MSP-опроса, 100 Hz)")
            msp_allow_arming(ser, io_lock=io_lock)
            time.sleep(quiet)

        st_quiet = msp_transaction(
            ser, MSP_STATUS, timeout=0.3, reset_rx=False, io_lock=io_lock
        )
        motor_quiet = msp_transaction(
            ser, MSP_MOTOR, timeout=0.25, reset_rx=False, io_lock=io_lock
        )
        if st_quiet and msp_status_armed(st_quiet):
            armed_seen = True
        if motor_quiet and motors_active(motor_quiet):
            armed_seen = True
        if st_quiet and not armed_seen:
            bits = int(parse_status(st_quiet).get("mode_flags", 0))
            dis = int(parse_status(st_quiet).get("arming_disable") or 0)
            dis_s = decode_arming_disable(dis)
            extra = f"  arming_disable={', '.join(dis_s)}" if dis_s else ""
            print(f"  after quiet: mode_bits=0x{bits:08X}{extra}")
            if bits & MODE_BITS_ANGLE:
                print("  OK: ANGLE mode active (bit1). Waiting for ARM (bit0)...")

        hold_left = max(0.0, float(args.hold) - quiet)
        deadline = time.monotonic() + hold_left
        last_allow = 0.0
        while not armed_seen and hold_left > 0 and time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_allow >= 0.5:
                msp_allow_arming(ser, io_lock=io_lock)
                last_allow = now
            st = msp_transaction(
                ser,
                MSP_STATUS,
                timeout=0.25,
                reset_rx=False,
                io_lock=io_lock,
            )
            motor = msp_transaction(
                ser,
                MSP_MOTOR,
                timeout=0.2,
                reset_rx=False,
                io_lock=io_lock,
            )
            if st and msp_status_armed(st):
                armed_seen = True
            if motor and motors_active(motor):
                armed_seen = True
            if st:
                bits = int(parse_status(st).get("mode_flags", 0))
                dis = parse_status(st).get("arming_disable")
                dis_s = decode_arming_disable(int(dis)) if dis else []
                extra = f"  arming_disable={', '.join(dis_s)}" if dis_s else ""
                print(f"  status mode_bits=0x{bits:08X}{extra}")
            if armed_seen:
                print("  OK: ARM detected → скоро DISARM (MOTOR_STOP выкл = моторы на idle!)")
                break
            time.sleep(0.35)

        if not armed_seen:
            rc_payload = msp_transaction(
                ser, MSP_RC, timeout=0.35, reset_rx=False, io_lock=io_lock
            )
            st_fail = msp_transaction(
                ser, MSP_STATUS, timeout=0.25, reset_rx=False, io_lock=io_lock
            )
            motor_payload = msp_transaction(
                ser, MSP_MOTOR, timeout=0.2, reset_rx=False, io_lock=io_lock
            )
            rc = parse_msp_rc(rc_payload) if rc_payload else []
            print("FAIL: FC не заармился.")
            if rc:
                print("  MSP_RC:", format_rc_channels(rc))
            if st_fail:
                dis = parse_status(st_fail).get("arming_disable") or 0
                reasons = decode_arming_disable(int(dis))
                if reasons:
                    print(f"  arming_disable: {', '.join(reasons)}")
            if motor_payload:
                motors = parse_msp_motor(motor_payload)[:4]
                print(f"  MSP_MOTOR (first 4): {motors}")
            print(
                "  Modes: ARM AUX1 ch",
                arm_ch,
                "ANGLE AUX2 ch",
                angle_ch,
            )
            return 1

        if args.spin_seconds <= 0:
            hold_armed = max(0.0, float(args.disarm_after_arm_s))
            if hold_armed > 0:
                print(f"→ ARM подтверждён, DISARM через {hold_armed:.1f} s")
                time.sleep(hold_armed)
        elif args.spin_seconds > 0:
            print(f"→ throttle {spin_throttle} us на {args.spin_seconds} s")
            streamer.set_channels(
                rc_map=rc_map,
                throttle_us=spin_throttle,
                arm_us=2000,
                angle_us=2000,
                arm_channel=arm_ch,
                angle_channel=angle_ch,
            )
            time.sleep(args.spin_seconds)
            streamer.set_channels(
                rc_map=rc_map,
                throttle_us=idle,
                arm_us=2000,
                angle_us=2000,
                arm_channel=arm_ch,
                angle_channel=angle_ch,
            )
            time.sleep(0.5)

        print("→ DISARM")
        streamer.set_channels(
            rc_map=rc_map,
            throttle_us=idle,
            arm_us=1000,
            angle_us=2000,
            arm_channel=arm_ch,
            angle_channel=angle_ch,
        )
        time.sleep(1.0)
        print("Готово (DISARM).")
        return 0
    finally:
        if streamer is not None:
            streamer.stop()
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
