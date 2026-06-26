from __future__ import annotations

import struct

MSP_RAW_GPS = 106


def parse_msp_raw_gps(payload: bytes) -> dict[str, float | int] | None:
    """MSP_RAW_GPS: fix, sats, lat/lon (deg*1e7), alt m, speed cm/s, course deg*10."""
    if len(payload) < 16:
        return None
    fix, sats = struct.unpack_from("<BB", payload, 0)
    lat_i, lon_i = struct.unpack_from("<ii", payload, 2)
    alt_m, speed_cms, course_x10 = struct.unpack_from("<hHH", payload, 10)
    lat = lat_i / 1e7
    lon = lon_i / 1e7
    speed = speed_cms / 100.0
    heading = course_x10 / 10.0
    if heading < 0:
        heading += 360.0
    return {
        "gps_fix": int(fix),
        "gps_sats": int(sats),
        "lat": lat,
        "lon": lon,
        "alt_gps_m": float(alt_m),
        "speed": speed,
        "heading": heading,
    }
