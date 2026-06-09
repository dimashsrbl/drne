#!/bin/bash
# Запуск ArduCopter SITL (без GUI, только MAVLink-порты)
# Запуск из WSL: ./wsl-run-ardupilot.sh

set -e
cd ~/ardupilot 2>/dev/null || { echo "Сначала выполните wsl-install-ardupilot.sh"; exit 1; }

echo "=== Запуск ArduCopter SITL ==="
echo ""
echo "Порты:"
echo "  TCP 5760  — MAVLink (бэкенд подключается сюда)"
echo "  UDP 14550 — GCS (QGroundControl)"
echo "  UDP 14551 — второй вывод"
echo ""

# sim_vehicle.py запускает ArduCopter + MAVProxy + SITL
# --no-reload — не перезапускать автоматически при изменении параметров
# --out — дополнительный UDP-вывод для QGC
# -v ArduCopter — тип ТС
# -f quad     — тип рамы (quad, hexa, octa, ...)
python3 Tools/autotest/sim_vehicle.py \
    -v ArduCopter \
    -f quad \
    --no-rebuild \
    --no-reload \
    --out=udp:127.0.0.1:14550 \
    --out=udp:127.0.0.1:14551 \
    --console