from __future__ import annotations

import math


def geo_offset_m(home_lat: float, home_lon: float, lat: float, lon: float) -> tuple[float, float]:
    """Смещение текущей точки от home: (north_m, east_m)."""
    north = (lat - home_lat) * 111_320.0
    east = (lon - home_lon) * 111_320.0 * math.cos(math.radians(home_lat))
    return north, east


def body_correction_m(north_m: float, east_m: float, heading_deg: float) -> tuple[float, float]:
    """
    Ошибка «как долететь до home» в осях нос/право (м).
    Положительный forward → цель впереди, положительный right → цель справа.
    """
    h = math.radians(heading_deg)
    ch, sh = math.cos(h), math.sin(h)
    to_north = -north_m
    to_east = -east_m
    forward = to_north * ch + to_east * sh
    right = -to_north * sh + to_east * ch
    return forward, right


def stick_deltas_us(
    forward_m: float,
    right_m: float,
    *,
    p_gain: int,
    max_us: int,
    deadband_m: float,
) -> tuple[int, int]:
    dist = math.hypot(forward_m, right_m)
    if dist < deadband_m:
        return 0, 0
    pitch = int(round(p_gain * forward_m))
    roll = int(round(p_gain * right_m))
    pitch = max(-max_us, min(max_us, pitch))
    roll = max(-max_us, min(max_us, roll))
    return roll, pitch
