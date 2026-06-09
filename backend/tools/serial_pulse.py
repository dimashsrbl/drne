#!/usr/bin/env python3
"""
Периодическая отправка байтов на serial — для проверки проводки / активности TX.

Зачем: убедиться, что Raspberry Pi реально что-то шлёт на контроллер (или на GPIO UART).

Перед запуском останови backend, если он держит порт:
  sudo systemctl stop drone-mission

Примеры (на Pi, USB к FC):
  cd ~/drone/backend
  source .venv/bin/activate
  python3 tools/serial_pulse.py /dev/ttyACM0 --interval 5

GPIO UART (Pi 5, /dev/serial0 = GPIO14 TX / GPIO15 RX):
  python3 tools/serial_pulse.py /dev/serial0 --interval 5

Как смотреть мультиметром
-------------------------
1) Ток (режим A, mA):
   - На линиях USB DATA (D+/D-) ток почти не измерить обычным мультиметром — импульсы очень короткие.
   - Ток по питанию 5V (красный провод USB): мультиметр В РАЗРЫВ между +5V Pi и +5V FC.
     Скрипт почти не меняет этот ток — виден в основном потребление самого FC (особенно с батареей).

2) Напряжение (режим V, DC) — проще для serial:
   - Чёрный щуп на GND FC/Pi.
   - Красный на TX Pi (GPIO14 или соответствующий пин USB-UART, если не USB VCP).
   - В покое ~3.3V; при отправке каждые N сек может слегка «дёргаться» (не все мультиметры это ловят).
   - Надёжнее: режим continuity/beep между TX Pi и точкой на FC (RX) — должно пикнуть.

3) USB /dev/ttyACM0:
   - Pi шлёт в виртуальный COM FC; на кабеле это всё равно цифровые импульсы, не постоянный ток.
   - Скрипт подтверждает, что порт открывается и write() не падает — косвенно «линия живая».

Ctrl+C — остановка.
"""
from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Периодически отправляет тестовые байты на serial (диагностика TX)."
    )
    ap.add_argument(
        "port",
        nargs="?",
        default="/dev/ttyACM0",
        help="Serial-порт (по умолчанию /dev/ttyACM0 — USB FC на Pi)",
    )
    ap.add_argument("--baud", type=int, default=115200, help="Скорость (по умолчанию 115200)")
    ap.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Пауза между импульсами, сек (по умолчанию 5)",
    )
    ap.add_argument(
        "--burst",
        type=int,
        default=16,
        help="Сколько байт отправить за один импульс (по умолчанию 16)",
    )
    ap.add_argument(
        "--listen",
        action="store_true",
        help="После каждой отправки показать байты, пришедшие от FC (если есть)",
    )
    args = ap.parse_args()

    if args.interval <= 0:
        print("--interval должен быть > 0", file=sys.stderr)
        return 1

    try:
        import serial
    except ImportError:
        print("Нужен pyserial: pip install pyserial", file=sys.stderr)
        return 1

    payload = (b"PULSE_" + bytes([0x55, 0xAA, 0xFE]) * 3)[: max(1, args.burst)]

    try:
        ser = serial.Serial(args.port, baudrate=args.baud, timeout=0.2)
    except serial.SerialException as e:
        print(f"Не открыть порт {args.port!r}: {e}", file=sys.stderr)
        print(
            "Часто: порт занят (sudo systemctl stop drone-mission) "
            "или нет прав (группа dialout, перелогинься после usermod).",
            file=sys.stderr,
        )
        return 1

    print(f"Порт: {args.port} @ {args.baud}")
    print(f"Импульс каждые {args.interval} с, {len(payload)} байт: {payload.hex(' ')}")
    print("Ctrl+C — выход\n")

    pulse = 0
    try:
        while True:
            pulse += 1
            ser.reset_input_buffer()
            n = ser.write(payload)
            ser.flush()
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] pulse #{pulse}: отправлено {n} байт")

            if args.listen:
                time.sleep(0.15)
                rx = ser.read(4096)
                if rx:
                    print(f"         RX {len(rx)} байт: {rx.hex(' ')}")
                else:
                    print("         RX: тишина")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
    finally:
        ser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
