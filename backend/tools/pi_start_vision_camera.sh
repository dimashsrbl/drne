#!/bin/bash
# Запуск камеры + vision-tracker на Pi: авто-поиск /dev/video*, рестарт, проверка стрима.
# На Pi: bash ~/drone/backend/tools/pi_start_vision_camera.sh
set -eu

VT="${HOME}/drone/vision-tracker"
ENV_FILE="${VT}/.env"
PI_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

echo "=== 1) OpenCV: какой /dev/video* даёт кадр ==="
if [ ! -d "${VT}/.venv" ]; then
  echo "FAIL: нет ${VT}/.venv — сначала: bash ~/drone/backend/tools/pi_install_vision_tracker.sh"
  exit 1
fi
# shellcheck disable=SC1091
source "${VT}/.venv/bin/activate"

BEST_DEV=""
python3 <<'PY' | tee /tmp/pi_camera_probe.txt
import cv2

api = int(cv2.CAP_V4L2) if hasattr(cv2, "CAP_V4L2") else int(cv2.CAP_ANY)
best = None
for dev in ["/dev/video1", "/dev/video0", "/dev/video2", 1, 0, 2]:
    try:
        cap = cv2.VideoCapture(dev, api) if isinstance(dev, str) else cv2.VideoCapture(int(dev), api)
    except Exception as e:
        print(f"{dev}: error {e}")
        continue
    if not cap.isOpened():
        print(f"{dev}: not opened")
        cap.release()
        continue
    ok, frame = cap.read()
    shape = frame.shape if ok and frame is not None else None
    print(f"{dev}: read={ok} shape={shape}")
    if ok and frame is not None and frame.size > 0 and best is None:
        best = dev if isinstance(dev, str) else f"/dev/video{dev}"
    cap.release()
if best:
    print(f"BEST={best}")
PY

BEST_DEV="$(grep -E '^BEST=' /tmp/pi_camera_probe.txt 2>/dev/null | tail -1 | cut -d= -f2- || true)"
if [ -z "${BEST_DEV}" ]; then
  echo
  echo "FAIL: ни один video-узел не отдал кадр."
  echo "  id  → нужна группа video?"
  id
  echo "  sudo usermod -aG video $(whoami)  # затем перелогиниться"
  echo "  v4l2-ctl --list-devices"
  exit 1
fi
echo "Рабочий узел: ${BEST_DEV}"

echo
echo "=== 2) .env VISION_VIDEO_SOURCE ==="
mkdir -p "${VT}"
touch "${ENV_FILE}"
if grep -q '^VISION_VIDEO_SOURCE=' "${ENV_FILE}"; then
  sed -i "s|^VISION_VIDEO_SOURCE=.*|VISION_VIDEO_SOURCE=${BEST_DEV}|" "${ENV_FILE}"
else
  echo "VISION_VIDEO_SOURCE=${BEST_DEV}" >> "${ENV_FILE}"
fi
grep -E '^VISION_' "${ENV_FILE}" | sed 's/PASSWORD=.*/PASSWORD=***/' || true

echo
echo "=== 3) restart vision-tracker ==="
sudo systemctl restart vision-tracker
sleep 3
systemctl is-active vision-tracker

echo
echo "=== 4) /target (ждём camera_status=ok, до 15 с) ==="
OK=0
for _ in $(seq 1 15); do
  TGT="$(curl -s -m 2 http://127.0.0.1:8001/target || echo FAIL)"
  echo "${TGT}"
  if echo "${TGT}" | grep -q '"camera_status":"ok"'; then
    OK=1
    break
  fi
  sleep 1
done

if [ "${OK}" -ne 1 ]; then
  echo
  echo "Камера всё ещё не ok. Логи:"
  journalctl -u vision-tracker -n 25 --no-pager || true
  exit 1
fi

HOST="${PI_IP:-127.0.0.1}"
echo
echo "=== Готово. Смотри видео и захват цели ==="
echo "  Стрим MJPEG:     http://${HOST}:8001/stream"
echo "  UI + lock:       http://${HOST}:8001/ui"
echo "  Статус цели:     http://${HOST}:8001/target"
echo "  Захват (POST):   curl -X POST http://${HOST}:8001/lock"
echo
echo "С ПК (frontend): npm run dev → Betaflight Sequence → Check Vision → Захват + полёт"
echo "  .env.local: VITE_VISION_TRACKER_URL=http://${HOST}:8001"
