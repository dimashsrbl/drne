#!/bin/bash
# Установка vision-tracker на Pi (testv1, ~/drone). Запуск на Pi:
#   bash ~/drone/backend/tools/pi_install_vision_tracker.sh
set -eu

DRONE_ROOT="${DRONE_ROOT:-$HOME/drone}"
VT="$DRONE_ROOT/vision-tracker"

echo "=== Drone root: $DRONE_ROOT ==="
if [ ! -d "$VT/app" ]; then
  echo "ERROR: нет $VT/app — сначала scp vision-tracker с ПК:"
  echo "  scp -r vision-tracker/app testv1@PI:~/drone/vision-tracker/"
  exit 1
fi

cd "$VT"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

if [ ! -f .env ]; then
  cp -n .env.example .env 2>/dev/null || true
  cat >> .env <<'EOF'
VISION_VIDEO_SOURCE=/dev/video0
VISION_CAPTURE_WIDTH=640
VISION_YOLO_MODEL=yolo11n.pt
VISION_CAMERA_FALLBACK=0,1,2
EOF
  echo "Создан $VT/.env — проверь VISION_VIDEO_SOURCE"
fi

sudo cp "$VT/deploy/vision-tracker.service" /etc/systemd/system/vision-tracker.service
sudo sed -i "s|/home/testv1|$HOME|g" /etc/systemd/system/vision-tracker.service 2>/dev/null || true
sudo systemctl daemon-reload
sudo systemctl enable vision-tracker
sudo systemctl restart vision-tracker

sleep 5
echo "=== status ==="
systemctl is-active vision-tracker
ss -tlnp 2>/dev/null | grep ':8001' || echo "WARN: порт 8001 не слушает — см. journalctl -u vision-tracker -n 50"
curl -s -m 5 http://127.0.0.1:8001/health || true
echo
curl -s -m 5 http://127.0.0.1:8001/target | head -c 400 || true
echo
echo "Готово. UI: http://$(hostname -I | awk '{print $1}'):8001/ui"
