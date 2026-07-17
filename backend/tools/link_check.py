#!/usr/bin/env python3
"""Диагностика Pi <-> FC: MSP-проба + MAVLink heartbeat."""
from __future__ import annotations

import argparse
import struct
import sys
import time


def msp_request(cmd: int, payload: bytes = b"") -> bytes:
    size = len(payload)
    data = bytearray((ord("$"), ord("M"), ord("<"), size, cmd))
    data.extend(payload)
    ck = 0
    for b in data[3:]:
        ck ^= b
    data.append(ck)
    return bytes(data)


def msp_probe(port: str, baud: int) -> bool:
    import serial

    ser = serial.Serial(port, baudrate=baud, timeout=0.5)
    ser.reset_input_buffer()
    ser.write(msp_request(1))  # MSP_API_VERSION
    ser.flush()
    time.sleep(0.2)
    resp = ser.read(256)
    ser.close()
    ok = resp.startswith(b"$M>") or resp.startswith(b"$M!")
    print(f"MSP probe: {len(resp)} bytes", resp[:12].hex(" ") if resp else "none")
    if ok:
        print("  -> MSP OK (USB/UART живой, FC отвечает)")
    else:
        print("  -> MSP NO (провода, порт, CLI занят, или MSP выключен на этом UART)")
    return ok


def raw_listen(port: str, baud: int, seconds: float = 2.0) -> int:
    import serial

    ser = serial.Serial(port, baudrate=baud, timeout=0.3)
    ser.reset_input_buffer()
    print(f"Passive RX {seconds}s (может быть 0 — FC часто молчит без запросов)...")
    t_end = time.monotonic() + seconds
    total = 0
    while time.monotonic() < t_end:
        total += len(ser.read(512))
    ser.close()
    print(f"Passive RAW: {total} bytes")
    return total


def mavlink_heartbeat(port: str, baud: int) -> bool:
    from pymavlink import mavutil

    print("MAVLink heartbeat (8s)...")
    conn = mavutil.mavlink_connection(
        port, baud=baud, source_system=255, source_component=0
    )
    conn.mav.heartbeat_send(
        mavutil.mavlink.MAV_TYPE_GCS,
        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
        0,
        0,
        mavutil.mavlink.MAV_STATE_ACTIVE,
    )
    deadline = time.monotonic() + 8.0
    hb = None
    while time.monotonic() < deadline:
        msg = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
        if msg is None:
            continue
        # Нужен heartbeat автопилота, не наш GCS (sys 255 / AUTOPILOT_INVALID).
        if int(getattr(msg, "autopilot", -1)) == int(mavutil.mavlink.MAV_AUTOPILOT_INVALID):
            continue
        if int(msg.get_srcSystem()) == 255:
            continue
        hb = msg
        break
    conn.close()
    if hb:
        print(
            f"HEARTBEAT: OK  sys={hb.get_srcSystem()} "
            f"autopilot={getattr(hb, 'autopilot', '?')}"
        )
        return True
    print("HEARTBEAT: NO")
    print("  -> Нет heartbeat автопилота (baud / TX-RX / SERIAL*_PROTOCOL / порт занят)")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка связи Pi <-> FC")
    ap.add_argument("--port", default="/dev/serial0", help="serial0=GPIO, ttyACM0=USB")
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()

    try:
        import serial  # noqa: F401
        from pymavlink import mavutil  # noqa: F401
    except ImportError as e:
        print("FAIL import:", e)
        return 1

    print("=== Pi <-> FC link check ===")
    print(f"Port: {args.port} @ {args.baud}")
    print("(sudo systemctl stop drone-mission перед запуском)\n")

    raw_listen(args.port, args.baud)
    print()
    msp_ok = msp_probe(args.port, args.baud)
    print()
    hb_ok = mavlink_heartbeat(args.port, args.baud)

    print()
    if args.port.startswith("/dev/ttyACM"):
        print("USB: .env -> DRONE_MAVLINK_CONNECTION=/dev/ttyACM0")
        print("INAV USB VCP: MSP + (желательно) Telemetry MAVLink для heartbeat")
    else:
        print("GPIO: .env -> DRONE_MAVLINK_CONNECTION=/dev/serial0")
        print("INAV UART4: MSP 115200 + Telemetry MAVLink")

    if hb_ok:
        print("\nRESULT: OK — MAVLink heartbeat есть (MSP для ArduPilot/Pixhawk не нужен)")
        return 0
    if msp_ok:
        print("\nRESULT: MSP OK, MAVLink NO — для Pixhawk нужен MAVLink на этом UART")
        return 2
    print("\nRESULT: FAIL — нет heartbeat, проверь baud/TX-RX/GND и SERIAL*_PROTOCOL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
