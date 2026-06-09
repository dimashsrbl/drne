#!/usr/bin/env python3
"""
Raspberry Pi 5 — тест линии UART TX (GPIO14, физ. pin 8) для мультиметра.

Pi 5: /dev/serial0 -> ttyAMA0, TX = GPIO14 (pin 8), RX = GPIO15 (pin 10).

Перед запуском останови сервисы, которые держат UART:
  sudo systemctl stop drone-mission

Примеры:
  # TX постоянно 3.3 V (удобнее всего для мультиметра)
  python3 tools/uart_tx_meter.py --mode high

  # TX постоянно 0 V
  python3 tools/uart_tx_meter.py --mode low

  # Медленное переключение 0 <-> 3.3 V каждые 2 сек (видно «скачки» на DC)
  python3 tools/uart_tx_meter.py --mode square --period 2

  # Непрерывный UART-поток (линия «живая», но DC-мультиметр может плавать)
  python3 tools/uart_tx_meter.py --mode flood

Как мерить мультиметром
-----------------------
1) Режим DC V (постоянное напряжение):
   - Чёрный щуп -> GND Pi (pin 6, 9, 14, 20, 25, 30, 34 или 39)
   - Красный щуп -> TX Pi GPIO14 (физ. pin 8)
   - mode high  -> ожидается ~3.2–3.3 V
   - mode low   -> ожидается ~0.0 V
   - mode square -> значение прыгает между ~0 и ~3.3 V

2) Режим прозвонки (continuity):
   - TX Pi (pin 8) <-> RX на контроллере — должно пикнуть
   - GND Pi <-> GND FC — должно пикнуть

3) Режим тока (A/mA):
   - На линии TX ток почти не измерить (высокоомный выход ~mA и меньше).
   - Ток по 5V USB/GND питания FC — отдельная проверка, этот скрипт её не усиливает.

Ctrl+C — выход, TX возвращается в безопасное состояние (idle HIGH).
"""
from __future__ import annotations

import argparse
import sys
import time

# Pi 5: основной UART на GPIO14/15
DEFAULT_PORT = "/dev/serial0"


def _open_serial(port: str, baud: int):
    try:
        import serial
    except ImportError:
        print("Нужен pyserial: pip install pyserial", file=sys.stderr)
        raise SystemExit(1)

    try:
        ser = serial.Serial(
            port,
            baudrate=baud,
            timeout=0.1,
            write_timeout=0.1,
        )
    except serial.SerialException as e:
        print(f"Не открыть {port!r}: {e}", file=sys.stderr)
        print(
            "Часто: порт занят (sudo systemctl stop drone-mission) "
            "или UART выключен (raspi-config -> Serial Port -> hardware Yes).",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return ser


def _hold_high(ser) -> None:
    """UART idle: TX = HIGH (~3.3 V)."""
    ser.break_condition = False


def _hold_low(ser) -> None:
    """UART break: TX = LOW (0 V)."""
    ser.break_condition = True


def _run_high(ser) -> None:
    _hold_high(ser)
    print("TX = HIGH (~3.3 V). Мультиметр DC V: pin 8 относительно GND.")
    print("Ctrl+C — выход.")
    while True:
        time.sleep(1)


def _run_low(ser) -> None:
    _hold_low(ser)
    print("TX = LOW (0 V). Мультиметр DC V: pin 8 относительно GND.")
    print("Ctrl+C — выход.")
    while True:
        time.sleep(1)


def _run_square(ser, period: float) -> None:
    half = period / 2.0
    state = True
    print(f"TX square wave: period={period}s (HIGH {half}s / LOW {half}s)")
    print("Мультиметр DC V на pin 8: значение должно меняться ~0 <-> ~3.3 V.")
    print("Ctrl+C — выход.")
    while True:
        if state:
            _hold_high(ser)
            label = "HIGH (~3.3 V)"
        else:
            _hold_low(ser)
            label = "LOW (0 V)"
        print(time.strftime("%H:%M:%S"), label)
        state = not state
        time.sleep(half)


def _run_flood(ser, baud: int, chunk: int) -> None:
    """Непрерывная передача — линия активна, но DC-мультиметр может «плавать»."""
    _hold_high(ser)
    payload = b"\x55" * chunk  # 01010101 — заметная активность на осциллографе
    interval = chunk * 10 / baud  # примерное время одного блока
    print(
        f"Flood: непрерывная отправка {chunk} байт 0x55 @ {baud} "
        f"(~{interval * 1000:.0f} ms на пакет)."
    )
    print("DC-мультиметр может показывать ~1.5–2.5 V (среднее). Надёжнее modes high/low.")
    print("Ctrl+C — выход.")
    n = 0
    while True:
        ser.write(payload)
        ser.flush()
        n += 1
        if n % 20 == 0:
            print(time.strftime("%H:%M:%S"), f"sent {n} blocks")
        time.sleep(0)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Держит или переключает UART TX (GPIO14) для проверки мультиметром."
    )
    ap.add_argument(
        "--port",
        default=DEFAULT_PORT,
        help=f"Serial-порт (по умолчанию {DEFAULT_PORT})",
    )
    ap.add_argument("--baud", type=int, default=115200, help="Скорость UART (для mode flood)")
    ap.add_argument(
        "--mode",
        choices=("high", "low", "square", "flood"),
        default="high",
        help="high=3.3V, low=0V, square=медленное переключение, flood=UART-поток",
    )
    ap.add_argument(
        "--period",
        type=float,
        default=2.0,
        help="Период square wave, сек (только --mode square)",
    )
    ap.add_argument(
        "--chunk",
        type=int,
        default=256,
        help="Размер блока для flood (байт)",
    )
    args = ap.parse_args()

    if args.period <= 0:
        print("--period должен быть > 0", file=sys.stderr)
        return 1

    print("=== UART TX test (Raspberry Pi) ===")
    print(f"Порт: {args.port}")
    print("TX pin: GPIO14 = физический pin 8")
    print("GND:    pin 6 / 9 / 14 / 20 / 25 / 30 / 34 / 39")
    print(f"Режим:  {args.mode}\n")

    ser = _open_serial(args.port, args.baud)

    try:
        if args.mode == "high":
            _run_high(ser)
        elif args.mode == "low":
            _run_low(ser)
        elif args.mode == "square":
            _run_square(ser, args.period)
        else:
            _run_flood(ser, args.baud, max(1, args.chunk))
    except KeyboardInterrupt:
        print("\nОстановка.")
    finally:
        try:
            _hold_high(ser)
            ser.close()
        except Exception:
            pass
        print("TX возвращён в idle HIGH, порт закрыт.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
