"""
MSP v1 (MultiWii) — SET_RAW_RC для INAV/Betaflight-подобных прошивок.

Используется, когда по USB доступен только MSP (или нужен именно RC-оверрайд),
а MAVLink MANUAL_CONTROL контроллером не обрабатывается.

Порядок каналов в кадре — как в Configurator: обычно AETR (Roll, Pitch, Throttle, Yaw).
При несовпадении с маппингом приёмника в INAV поправьте порядок в inav_adapter.
"""
from __future__ import annotations

import struct

import serial

# Cleanflight / Betaflight / INAV family
MSP_SET_ARMING_DISABLED = 99  # payload 0 = разрешить ARM по MSP (снять ARMING_DISABLED_MSP)
MSP_SET_RAW_RC = 200

# Betaflight: rcData[THROTTLE] < mincheck (строго). 1000 часто не проходит при mincheck=1000.
BETAFLIGHT_IDLE_THROTTLE_US = 988

RC_CHANNEL_LETTERS = "AERT"

# Betaflight armingDisableFlags_e (bit index → name), см. runtime_config.h
ARMING_DISABLE_FLAG_NAMES: dict[int, str] = {
    0: "NO_GYRO",
    1: "FAILSAFE",
    2: "RX_FAILSAFE",
    3: "NOT_DISARMED",
    4: "BOXFAILSAFE",
    7: "THROTTLE",
    8: "ANGLE",
    9: "BOOT_GRACE",
    10: "NOPREARM",
    12: "CALIBRATING",
    13: "CLI",
    16: "MSP",
    21: "REBOOT_REQUIRED",
    24: "MOTOR_PROTOCOL",
    28: "ARM_SWITCH",
}


def decode_arming_disable_flags(flags: int) -> list[str]:
    names: list[str] = []
    for bit, name in sorted(ARMING_DISABLE_FLAG_NAMES.items()):
        if flags & (1 << bit):
            names.append(name)
    if not names and flags:
        names.append(f"0x{flags:08X}")
    return names


def parse_msp_status(payload: bytes, *, is_ex: bool = False) -> dict:
    """
    Betaflight MSP_STATUS / MSP_STATUS_EX (API 1.47+).
    mode_bits[0] = BOXARM (permanentId 0) — armed, когда FC реально заармлен.
    """
    if len(payload) < 16:
        return {"raw_len": len(payload)}
    cycle_us = struct.unpack("<H", payload[0:2])[0]
    i2c_errors = struct.unpack("<H", payload[2:4])[0]
    sensors = struct.unpack("<H", payload[4:6])[0]
    mode_low = struct.unpack("<I", payload[6:10])[0]
    profile = payload[10]
    load = struct.unpack("<H", payload[11:13])[0]
    byte_count_offset = 15  # после gyro U16 (STATUS) или rate idx (EX)
    byte_count = payload[byte_count_offset]
    extra_offset = byte_count_offset + 1
    extra = payload[extra_offset : extra_offset + byte_count]
    mode_bits = mode_low | (int.from_bytes(extra, "little") << 32 if extra else 0)
    arming_offset = extra_offset + byte_count
    arming_disable: int | None = None
    if len(payload) >= arming_offset + 5:
        arming_disable = struct.unpack("<I", payload[arming_offset + 1 : arming_offset + 5])[0]
    return {
        "cycle_us": cycle_us,
        "i2c_errors": i2c_errors,
        "sensors": sensors,
        "mode_bits": mode_bits,
        "mode_flags": mode_low,  # legacy alias
        "profile": profile,
        "load": load,
        "arming_disable": arming_disable,
    }


def msp_status_armed(payload: bytes, *, is_ex: bool = False) -> bool:
    """BOXARM — первый active box; bit0 mode_bits = armed."""
    parsed = parse_msp_status(payload, is_ex=is_ex)
    return bool(int(parsed.get("mode_bits", 0)) & 1)


def msp_rcmap_to_wire_map(rcmap: list[int]) -> str:
    """Betaflight rcmap[] → строка map для SET_RAW_RC (AETR / AERT)."""
    out = ["?"] * 4
    for letter_idx, wire_idx in enumerate(rcmap[:4]):
        if 0 <= wire_idx < 4:
            out[wire_idx] = RC_CHANNEL_LETTERS[letter_idx]
    wire = "".join(out)
    return wire if "?" not in wire else "AETR"


def msp_allow_arming(port: serial.Serial) -> None:
    """Снять блокировку ARMING_DISABLED_MSP для этого MSP-клиента (Pi/backend)."""
    port.write(build_msp_v1(MSP_SET_ARMING_DISABLED, b"\x00"))
    port.flush()


def msp_rc_sticks_to_frame(
    *,
    roll_us: int = 1500,
    pitch_us: int = 1500,
    throttle_us: int = BETAFLIGHT_IDLE_THROTTLE_US,
    yaw_us: int = 1500,
    rc_map: str = "AETR",
) -> list[int]:
    """
    Первые 4 канала MSP SET_RAW_RC по map Betaflight (CLI ``map AETR1234``).
    AETR: [roll, pitch, throttle, yaw]. AERT: [roll, pitch, yaw, throttle].
    """
    sticks = {"A": roll_us, "E": pitch_us, "T": throttle_us, "R": yaw_us}
    letters = (rc_map or "AETR").strip().upper()[:4]
    if len(letters) < 4:
        letters = "AETR"
    frame = [1500, 1500, 1500, 1500]
    for idx, letter in enumerate(letters):
        if letter in sticks:
            frame[idx] = int(sticks[letter])
    return frame


def build_msp_v1(cmd: int, payload: bytes = b"") -> bytes:
    if len(payload) > 255:
        raise ValueError("MSP v1: payload > 255 байт")
    size = len(payload)
    data = bytearray((ord("$"), ord("M"), ord("<"), size & 0xFF, cmd & 0xFF))
    data.extend(payload)
    ck = 0
    for b in data[3:]:
        ck ^= b
    data.append(ck)
    return bytes(data)


def _stick_to_us(v: int) -> int:
    """-1000..1000 → 1000..2000 мкс (центр 1500)."""
    return max(1000, min(2000, 1500 + int(v * 0.5)))


def _thrust_to_us(t: int) -> int:
    """0..1000 → 1000..2000 мкс."""
    return max(1000, min(2000, 1000 + int(t)))


def manual_to_raw_rc_channels(
    pitch: int,
    roll: int,
    thrust: int,
    yaw: int,
    num_channels: int = 8,
) -> list[int]:
    """
    Оси из API manual-control → значения каналов в мкс.
    По умолчанию AETR: roll, pitch, throttle, yaw; остальные — центр (режим/арм с пульта).
    """
    r = _stick_to_us(roll)
    p = _stick_to_us(pitch)
    t = _thrust_to_us(thrust)
    y = _stick_to_us(yaw)
    ch = msp_rc_sticks_to_frame(
        roll_us=r,
        pitch_us=p,
        throttle_us=t,
        yaw_us=y,
    )
    mid = 1500
    while len(ch) < num_channels:
        ch.append(mid)
    return ch[:num_channels]


def set_raw_rc(
    port: serial.Serial,
    pitch: int,
    roll: int,
    thrust: int,
    yaw: int,
    num_channels: int = 8,
) -> None:
    ch = manual_to_raw_rc_channels(pitch, roll, thrust, yaw, num_channels=num_channels)
    payload = struct.pack("<" + "H" * len(ch), *ch)
    port.write(build_msp_v1(MSP_SET_RAW_RC, payload))
    port.flush()
