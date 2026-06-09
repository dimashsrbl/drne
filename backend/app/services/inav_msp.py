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
MSP_SET_RAW_RC = 200


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
    ch = [r, p, t, y]
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
