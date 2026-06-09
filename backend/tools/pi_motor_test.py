#!/usr/bin/env python3
"""
Тест «Pi → backend → INAV → моторы» через HTTP API (localhost:8000).

Проверяет цепочку: telemetry connected → ANGLE → ARM → (опционально газ) → DISARM.

⚠️  БЕЗОПАСНОСТЬ:
  - Первый раз — БЕЗ пропеллеров (--arm-only).
  - С пропеллерами — крепко держи дрон, никого рядом, --yes только осознанно.
  - INAV может отказать ARM (prearm, режим LAND, нет arm switch на пульте).

Примеры на Pi:
  cd ~/drone/backend
  source .venv/bin/activate

  # только проверка связи
  python3 tools/pi_motor_test.py --check

  # только arm/disarm без газа (моторы idle, если пропеллеры стоят)
  python3 tools/pi_motor_test.py --arm-only --yes

  # arm + короткий газ 3 сек (thrust 150 из 1000)
  python3 tools/pi_motor_test.py --spin-seconds 3 --thrust 150 --yes

Требуется запущенный сервис:
  systemctl is-active drone-mission   # active
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def api(base: str, method: str, path: str, body: dict | None = None) -> dict:
    url = f"{base.rstrip('/')}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {"ok": True}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {path}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Нет связи с backend {base}: {e}") from e


def telemetry(base: str) -> dict:
    return api(base, "GET", "/telemetry")


def wait_armed(base: str, want: bool, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        t = telemetry(base)
        armed = t.get("armed")
        if armed is want:
            return True
        time.sleep(0.5)
    return False


def manual_burst(base: str, thrust: int, seconds: float, hz: float = 14.0) -> None:
    """Короткая отправка MANUAL_CONTROL (INAV может игнорировать — см. README)."""
    dt = 1.0 / hz
    end = time.monotonic() + seconds
    n = 0
    while time.monotonic() < end:
        api(
            base,
            "POST",
            "/drone/manual-control",
            {"pitch": 0, "roll": 0, "thrust": thrust, "yaw": 0},
        )
        n += 1
        time.sleep(dt)
    print(f"  manual-control: {n} пакетов, thrust={thrust}")


def confirm(args: argparse.Namespace) -> None:
    if args.yes:
        return
    print()
    print("=" * 60)
    print("ВНИМАНИЕ: дрон может ARM и крутить моторы!")
    print("Убедись: пропеллеры сняты ИЛИ дрон надёжно зафиксирован.")
    print("=" * 60)
    ans = input("Продолжить? [y/N]: ").strip().lower()
    if ans not in ("y", "yes", "д", "да"):
        print("Отменено.")
        raise SystemExit(0)


def main() -> int:
    ap = argparse.ArgumentParser(description="Тест ARM/моторов через backend на Pi")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="URL backend")
    ap.add_argument("--check", action="store_true", help="Только health + telemetry")
    ap.add_argument("--arm-only", action="store_true", help="ANGLE → ARM → пауза → DISARM")
    ap.add_argument("--spin-seconds", type=float, default=0.0, help="Секунд газа после ARM (0=выкл)")
    ap.add_argument("--thrust", type=int, default=120, help="Газ 0..1000 (по умолч. 120)")
    ap.add_argument("--hold", type=float, default=4.0, help="Секунд держать ARM без газа")
    ap.add_argument("--mode", default="ANGLE", help="Режим перед ARM (ANGLE/HORIZON/ALTHOLD)")
    ap.add_argument("--yes", action="store_true", help="Без интерактивного подтверждения")
    args = ap.parse_args()

    base = args.base
    print(f"Backend: {base}")

    # 1) health
    try:
        h = api(base, "GET", "/health")
        print(f"health: {h}")
    except RuntimeError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        print("Запусти: sudo systemctl start drone-mission", file=sys.stderr)
        return 1

    t0 = telemetry(base)
    print(f"telemetry: status={t0.get('status')} mode={t0.get('mode')} armed={t0.get('armed')}")
    if t0.get("status") != "connected":
        print("FAIL: backend не connected к FC. Проверь /dev/serial0 и INAV MAVLink.", file=sys.stderr)
        return 2

    if args.check:
        print("OK: backend и FC связаны.")
        return 0

    confirm(args)

    # 2) режим
    print(f"→ flight-mode {args.mode}")
    api(base, "POST", "/drone/flight-mode", {"mode": args.mode.upper()})
    time.sleep(1.5)
    t1 = telemetry(base)
    print(f"  mode={t1.get('mode')}")

    # 3) ARM
    print("→ ARM")
    try:
        api(base, "POST", "/drone/arm")
    except RuntimeError as e:
        print(f"FAIL ARM: {e}", file=sys.stderr)
        print("Подсказки: режим не LAND; prearm в INAV; arm switch на пульте.", file=sys.stderr)
        return 3

    if wait_armed(base, True):
        print("  OK: armed=true")
    else:
        t = telemetry(base)
        print(f"  WARN: armed всё ещё {t.get('armed')} — FC мог отказать (смотри INAV / STATUSTEXT)")

    # 4) hold / spin
    if args.spin_seconds > 0:
        print(f"→ газ {args.thrust}/1000 на {args.spin_seconds} с")
        try:
            manual_burst(base, args.thrust, args.spin_seconds)
        except RuntimeError as e:
            print(f"  WARN manual-control: {e}")
            print("  INAV часто игнорирует MAVLink MANUAL_CONTROL — idle ARM всё равно тест цепочки.")
    else:
        print(f"→ держим ARM {args.hold} с (idle моторы, если пропеллеры на месте)")
        time.sleep(args.hold)

    # 5) DISARM
    print("→ DISARM")
    api(base, "POST", "/drone/disarm")
    time.sleep(1)
    if wait_armed(base, False, timeout_s=5):
        print("  OK: armed=false")
    else:
        print("  WARN: проверь disarm вручную (пульт / INAV)")

    t2 = telemetry(base)
    print(f"итог: status={t2.get('status')} mode={t2.get('mode')} armed={t2.get('armed')}")
    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
