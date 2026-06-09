from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import dataclass

import websockets

try:
    import serial  # type: ignore
except Exception:  # pragma: no cover
    serial = None


@dataclass
class EulerDeg:
    roll: float
    pitch: float
    yaw: float


def parse_rpy_line(line: str) -> EulerDeg | None:
    """
    Ожидаем формат: roll,pitch,yaw (градусы), например: "-3.2,12.5,180.0"
    """
    s = line.strip()
    if not s:
        return None
    parts = s.split(",")
    if len(parts) != 3:
        return None
    try:
        r, p, y = (float(x) for x in parts)
    except ValueError:
        return None
    return EulerDeg(roll=r, pitch=p, yaw=y)


async def ws_server(state: dict, host: str, port: int) -> None:
    async def handler(ws):
        # simple “push loop”
        while True:
            payload = state.get("payload")
            if payload is not None:
                await ws.send(json.dumps(payload))
            await asyncio.sleep(1 / 30)

    async with websockets.serve(handler, host, port):
        await asyncio.Future()


async def simulate_loop(state: dict) -> None:
    t0 = time.monotonic()
    while True:
        t = time.monotonic() - t0
        r = 25.0 * math.sin(t * 0.9)
        p = 18.0 * math.sin(t * 0.7 + 1.2)
        y = (t * 35.0) % 360.0
        state["payload"] = {"rpy_deg": {"roll": r, "pitch": p, "yaw": y}, "source": "simulate"}
        await asyncio.sleep(1 / 60)


async def serial_loop(state: dict, port: str, baud: int) -> None:
    if serial is None:
        raise RuntimeError("pyserial не установлен. Установи: pip install -r requirements.txt")

    ser = serial.Serial(port=port, baudrate=baud, timeout=0.2)
    try:
        while True:
            raw = ser.readline()
            if not raw:
                await asyncio.sleep(0.01)
                continue
            line = raw.decode("utf-8", errors="ignore")
            rpy = parse_rpy_line(line)
            if rpy is None:
                continue
            state["payload"] = {
                "rpy_deg": {"roll": rpy.roll, "pitch": rpy.pitch, "yaw": rpy.yaw},
                "source": f"serial:{port}@{baud}",
            }
    finally:
        ser.close()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--ws-port", type=int, default=8765)
    ap.add_argument("--simulate", action="store_true", help="Генерировать тестовые углы (без железа)")
    ap.add_argument("--port", help="COM-порт (например COM7)")
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()

    state: dict = {"payload": None}

    tasks = [asyncio.create_task(ws_server(state, args.host, args.ws_port))]
    if args.simulate:
        tasks.append(asyncio.create_task(simulate_loop(state)))
    else:
        if not args.port:
            raise SystemExit("Укажи --port COMx или включи --simulate")
        tasks.append(asyncio.create_task(serial_loop(state, args.port, args.baud)))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())

