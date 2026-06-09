#!/usr/bin/env python3
"""
Диагностика UART (Pi <-> FC): сырые байты и опциональный loopback.

Перед запуском останови сервис, который держит порт:
  sudo systemctl stop drone-mission.service

Loopback (проверка, что UART на Pi живой): замкни TX и RX этого UART
(на Pi 5 для /dev/serial0 это GPIO14 и GPIO15 — физически перемычка dupont),
затем:
  python3 tools/serial_listen.py /dev/serial0 --loopback

Прослушивание FC (питание FC должно быть, MAVLink на этом UART):
  python3 tools/serial_listen.py /dev/serial0 --seconds 10
"""
from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser(description="Сырой RX на serial + опциональный loopback TX->RX.")
    ap.add_argument("port", help="Например /dev/serial0 или COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=5.0, help="Длительность чтения (сек)")
    ap.add_argument(
        "--loopback",
        action="store_true",
        help="Отправить тестовые байты; при замкнутых TX/RX на одном UART должны вернуться",
    )
    args = ap.parse_args()

    try:
        import serial
    except ImportError:
        print("Нужен пакет pyserial: pip install pyserial", file=sys.stderr)
        return 1

    try:
        ser = serial.Serial(args.port, baudrate=args.baud, timeout=0.1)
    except serial.SerialException as e:
        print(f"Не открыть порт {args.port!r}: {e}", file=sys.stderr)
        print("Часто: порт занят (останови drone-mission) или нет прав (группа dialout).", file=sys.stderr)
        return 1

    t_end = time.monotonic() + args.seconds
    buf = bytearray()
    rx_total = 0

    if args.loopback:
        payload = b"LOOPBACK_TEST_\xaa\xfe"
        ser.reset_input_buffer()
        ser.write(payload)
        ser.flush()
        time.sleep(0.05)

    print(f"Порт {args.port} @ {args.baud}, слушаю {args.seconds} с...")
    while time.monotonic() < t_end:
        chunk = ser.read(4096)
        if chunk:
            rx_total += len(chunk)
            buf.extend(chunk)
            # печатаем по мере поступления
            print(chunk.hex(" ", 1))

    ser.close()

    print(f"\nИтого байт RX: {rx_total}")
    if args.loopback:
        if rx_total == 0:
            print(
                "Loopback: ничего не вернулось — UART не замкнут TX-RX на ЭТОМ порту, "
                "или не тот порт.",
                file=sys.stderr,
            )
            return 2
        if bytes(buf).startswith(b"LOOPBACK_TEST_"):
            print("Loopback: OK — Pi UART принимает то, что сам отправил.")
        else:
            print("Loopback: пришли байты, но не совпали с тестом (шум или не тот замыкатель).")

    if not args.loopback and rx_total == 0:
        print(
            "Тишина на RX. Варианты: FC без питания, не тот пин UART на FC, "
            "в INAV на этом UART не MAVLink/не та скорость, обрыв, TX/RX всё же перепутаны.",
            file=sys.stderr,
        )
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
