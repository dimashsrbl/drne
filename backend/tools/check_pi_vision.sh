#!/bin/bash
# Проверка камеры и vision-tracker на Raspberry Pi.
set -eu

echo "=== hostname ==="
hostname
echo
echo "=== USB video devices ==="
ls -la /dev/video* 2>&1 || true
echo
echo "=== v4l2 (if installed) ==="
v4l2-ctl --list-devices 2>&1 | head -25 || echo "v4l2-ctl not installed (optional: sudo apt install v4l-utils)"
echo
echo "=== services ==="
systemctl is-active drone-mission 2>&1 || true
systemctl is-active vision-tracker 2>&1 || true
echo
echo "=== listening ports ==="
ss -tlnp 2>/dev/null | grep -E ':8000|:8001' || true
echo
echo "=== backend health :8000 ==="
curl -s -m 3 http://127.0.0.1:8000/health || echo FAIL
echo
echo "=== vision health :8001 ==="
if ! curl -s -m 3 http://127.0.0.1:8001/health; then
  echo FAIL
  echo "=== vision-tracker logs (last 30 lines) ==="
  journalctl -u vision-tracker -n 30 --no-pager 2>&1 || true
fi
echo
echo "=== vision /target ==="
curl -s -m 3 http://127.0.0.1:8001/target 2>/dev/null | head -c 600 || echo FAIL
echo
echo
echo "=== vision env (if .env exists) ==="
grep -E '^VISION_' ~/drone/vision-tracker/.env 2>/dev/null | sed 's/PASSWORD=.*/PASSWORD=***/' || echo "no vision-tracker/.env"
echo
PI_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "=== URLs (when camera ok) ==="
echo "  stream: http://${PI_IP:-<pi-ip>}:8001/stream"
echo "  ui+lock: http://${PI_IP:-<pi-ip>}:8001/ui"
echo
echo "Done. camera_status should be 'ok', backend should be 'yolo' (not stub)."
echo "If no_device: bash ~/drone/backend/tools/pi_start_vision_camera.sh"
