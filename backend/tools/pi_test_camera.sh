#!/bin/bash
# Быстрый тест веб-камеры на Pi. Запуск: bash ~/drone/backend/tools/pi_test_camera.sh
set -eu

echo "=== user / groups ==="
id
echo
echo "=== v4l2 devices ==="
v4l2-ctl --list-devices 2>&1 || true
echo
echo "=== OpenCV probe ==="
VT="${HOME}/drone/vision-tracker"
if [ -d "$VT/.venv" ]; then
  # shellcheck disable=SC1091
  source "$VT/.venv/bin/activate"
  python3 <<'PY'
import os
import cv2

paths = ["/dev/video0", "/dev/video1", 0, 1]
extra = (os.environ.get("VISION_V4L_DEVICES") or "").strip()
if extra:
    paths = [p.strip() for p in extra.split(",") if p.strip()] + paths

api = int(cv2.CAP_V4L2) if hasattr(cv2, "CAP_V4L2") else int(cv2.CAP_ANY)
seen = set()
for p in paths:
    key = str(p)
    if key in seen:
        continue
    seen.add(key)
    try:
        cap = cv2.VideoCapture(p, api) if isinstance(p, str) else cv2.VideoCapture(int(p), api)
    except Exception as e:
        print(f"{p}: open error {e}")
        continue
    if not cap.isOpened():
        print(f"{p}: not opened")
        cap.release()
        continue
    ok, frame = cap.read()
    shape = frame.shape if ok and frame is not None else None
    print(f"{p}: opened={cap.isOpened()} read={ok} shape={shape}")
    cap.release()
PY
else
  echo "нет $VT/.venv — сначала pi_install_vision_tracker.sh"
fi
echo
echo "=== vision-tracker /target ==="
curl -s -m 5 http://127.0.0.1:8001/target || echo FAIL
