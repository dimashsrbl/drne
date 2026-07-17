#!/usr/bin/env bash
# Переключить backend на Raspberry Pi с Betaflight/MSP на ArduPilot/MAVLink.
# Запуск на Pi:  bash ~/drone/backend/tools/pi_switch_ardupilot.sh
set -euo pipefail

ROOT="${HOME}/drone"
BACKEND="${ROOT}/backend"
ENV_FILE="${BACKEND}/.env"
BAUD="${1:-115200}"

if [[ ! -d "${BACKEND}" ]]; then
  echo "Нет каталога ${BACKEND}"
  exit 1
fi

cd "${ROOT}"
git pull --ff-only || true

cat > "${ENV_FILE}" <<EOF
DRONE_BACKEND_PROFILE=ardupilot
DRONE_MAVLINK_CONNECTION=/dev/serial0
DRONE_MAVLINK_BAUD=${BAUD}
DRONE_MAVLINK_FALLBACKS=
DRONE_SITL_FORCE_ARM=false
DRONE_SITL_RELAX_PREFLIGHT=false
DRONE_ARDUPILOT_MISSION_MODE=guided
DRONE_ARDUPILOT_MIN_GPS_SATS=6
DRONE_ARDUPILOT_GOTO_TOL_M=3.0
DRONE_VISION_TRACKER_URL=http://127.0.0.1:8001
EOF

echo "=== .env ==="
cat "${ENV_FILE}"
echo
echo "=== serial devices ==="
ls -l /dev/serial0 /dev/ttyAMA0 /dev/ttyACM* 2>/dev/null || true
echo

if [[ ! -e /dev/serial0 ]]; then
  echo "WARN: /dev/serial0 нет. Включи UART:"
  echo "  sudo raspi-config → Interface Options → Serial Port"
  echo "  login shell: No | serial hardware: Yes"
  echo "  затем reboot"
fi

cd "${BACKEND}"
if [[ -x .venv/bin/pip ]]; then
  .venv/bin/pip install -q -r requirements-mission.txt
fi

if systemctl list-unit-files drone-mission.service >/dev/null 2>&1; then
  sudo systemctl restart drone-mission
  sleep 2
  systemctl is-active drone-mission || true
else
  echo "systemd unit drone-mission не найден — перезапусти uvicorn вручную"
fi

echo
echo "=== /drone/profile ==="
curl -sS --max-time 5 http://127.0.0.1:8000/drone/profile || echo "backend не отвечает"
echo
echo
echo "=== MAVLink probe ==="
if [[ -x .venv/bin/python ]]; then
  .venv/bin/python tools/link_check.py --port /dev/serial0 --baud "${BAUD}" || true
fi

echo
echo "Готово. Во фронте открой /mission-control (не /betaflight)."
echo "Если heartbeat нет — проверь SERIAL2_PROTOCOL=2 и SERIAL2_BAUD на Pixhawk (TELEM2)."
