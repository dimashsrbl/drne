from __future__ import annotations

import base64
import logging
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ── Ленивый импорт YOLO ───────────────────────────────────────────────────────
# Приоритет загрузки:
#   1. yolov8n_int8.onnx (INT8 ONNX — быстрее на ARM/RPi5)
#   2. yolo11n.onnx      (FP32 ONNX — если экспортировали)
#   3. yolo11n.pt        (стандартная PyTorch — автоскачивание)
_INT8_MODEL_PATH = Path("yolov8n_int8.onnx")
_ONNX_MODEL_PATH = Path("yolo11n.onnx")


def _try_load_yolo(model_name: str):
    """Загружает YOLO. Если доступна INT8 ONNX — берёт её (лучше на RPi5)."""
    try:
        from ultralytics import YOLO  # type: ignore[import-untyped]

        if _INT8_MODEL_PATH.exists():
            log.info("[vision] Загружаем INT8 ONNX: %s", _INT8_MODEL_PATH)
            return YOLO(str(_INT8_MODEL_PATH)), "yolo-int8"
        if _ONNX_MODEL_PATH.exists():
            log.info("[vision] Загружаем FP32 ONNX: %s", _ONNX_MODEL_PATH)
            return YOLO(str(_ONNX_MODEL_PATH)), "yolo-onnx"

        log.info("[vision] Загружаем %s (PyTorch)", model_name)
        return YOLO(model_name), "yolo"
    except Exception as e:
        log.warning("[vision] YOLO не загружен (%s), fallback на OpenCV Canny", e)
        return None, "canny"


# ── Классы которые считаем препятствиями (COCO 80-класс) ──────────────────
# Расширяй по необходимости
OBSTACLE_CLASSES: set[str] = {
    "person", "bicycle", "car", "motorcycle", "bus", "truck",
    "bird", "cat", "dog", "horse", "cow", "sheep",
    "tree",          # нет в COCO, но на случай дообученной модели
    "wall",          # то же
}

# Классы "серьёзной угрозы" — в будущем сюда DeepSeek
HIGH_THREAT_CLASSES: set[str] = {"person", "car", "truck", "bus"}


@dataclass
class DetectedObject:
    cls_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    track_id: int | None = None   # ByteTrack ID (None если трекер недоступен)

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def center_y(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def area(self) -> float:
        return (self.x2 - self.x1) * (self.y2 - self.y1)
  

@dataclass
class ObstacleInfo:
    detected: bool = False
    area_ratio: float = 0.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    # YOLO-специфика
    objects: list[DetectedObject] = field(default_factory=list)
    high_threat: bool = False   # True если обнаружен человек/машина/etc
    primary_class: str = ""     # класс ближайшего/крупнейшего объекта


@dataclass
class VisionSnapshot:
    running: bool = False
    camera_index: int = 0
    obstacle: ObstacleInfo = field(default_factory=ObstacleInfo)
    frame_b64: str = ""
    fps: float = 0.0
    error: str | None = None
    backend: str = "none"  # "yolo" | "canny" | "none"


class VisionService:
    """
    Захват кадров с камеры + детектирование объектов.

    Backends (в порядке приоритета):
      1. YOLO (ultralytics) — классифицирует объекты, high_threat для серьёзных целей
      2. OpenCV Canny — fallback если ultralytics не установлен

    Сглаживание: переключает obstacle.detected только после CONFIRM_FRAMES
    кадров подряд (устраняет ложные срабатывания).
    """

    YOLO_MODEL        = "yolo11n.pt"    # nano — быстрая, ~6MB
    YOLO_CONF         = 0.40            # минимальная уверенность для детекции
    OBSTACLE_AREA_THR = 0.05            # YOLO: если bbox > 5% кадра — считаем угрозой
    CONFIRM_FRAMES    = 3               # кадров подряд для смены состояния
    ENABLE_TRACKING   = True            # ByteTrack — присваивает объектам ID

    # Fallback OpenCV Canny
    MIN_CONTOUR_AREA      = 800
    CANNY_AREA_THRESHOLD  = 0.10

    def __init__(self) -> None:
        self._lock   = threading.RLock()
        self._stop   = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot     = VisionSnapshot()
        self._camera_index = 0
        self._yolo         = None        # загружается при первом start()
        self._yolo_backend = "canny"     # строка для отображения в UI
        self._consec_det   = 0
        self._consec_clear = 0

    # ── Управление ────────────────────────────────────────────────────────────

    def start(self, camera_index: int = 0) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._camera_index = camera_index
            self._stop.clear()
            self._consec_det = 0
            self._consec_clear = 0
            self._snapshot = VisionSnapshot(running=True, camera_index=camera_index)
            self._thread = threading.Thread(target=self._run, daemon=True, name="vision")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._snapshot.running = False

    def get_snapshot(self) -> VisionSnapshot:
        with self._lock:
            s = self._snapshot
            return VisionSnapshot(
                running=s.running,
                camera_index=s.camera_index,
                obstacle=ObstacleInfo(
                    detected=s.obstacle.detected,
                    area_ratio=s.obstacle.area_ratio,
                    offset_x=s.obstacle.offset_x,
                    offset_y=s.obstacle.offset_y,
                    objects=list(s.obstacle.objects),
                    high_threat=s.obstacle.high_threat,
                    primary_class=s.obstacle.primary_class,
                ),
                frame_b64=s.frame_b64,
                fps=s.fps,
                error=s.error,
                backend=s.backend,
            )

    def mjpeg_frames(self) -> Generator[bytes, None, None]:
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
        while True:
            with self._lock:
                b64 = self._snapshot.frame_b64
                running = self._snapshot.running
            if b64 and running:
                try:
                    raw = base64.b64decode(b64)
                    yield boundary + raw + b"\r\n"
                except Exception:
                    pass
            time.sleep(0.04)

    # ── Внутренний цикл ───────────────────────────────────────────────────────

    def _run(self) -> None:
        # Загружаем YOLO один раз
        if self._yolo is None:
            self._yolo, self._yolo_backend = _try_load_yolo(self.YOLO_MODEL)

        use_yolo = self._yolo is not None
        backend_name = self._yolo_backend if use_yolo else "canny"

        cap = cv2.VideoCapture(self._camera_index)
        if not cap.isOpened():
            with self._lock:
                self._snapshot.error = f"Не удалось открыть камеру {self._camera_index}"
                self._snapshot.running = False
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        frame_times: list[float] = []

        try:
            while not self._stop.is_set():
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.05)
                    continue

                t_now = time.monotonic()
                frame_times.append(t_now)
                frame_times = [t for t in frame_times if t_now - t < 1.0]
                fps = float(len(frame_times))

                if use_yolo:
                    raw_obs, annotated = self._detect_yolo(frame)
                else:
                    raw_obs, annotated = self._detect_canny(frame)

                # Сглаживание: переключаем только после CONFIRM_FRAMES подряд
                if raw_obs.detected:
                    self._consec_det += 1
                    self._consec_clear = 0
                else:
                    self._consec_clear += 1
                    self._consec_det = 0

                with self._lock:
                    prev_detected = self._snapshot.obstacle.detected

                if raw_obs.detected and self._consec_det >= self.CONFIRM_FRAMES:
                    smoothed = raw_obs
                elif not raw_obs.detected and self._consec_clear >= self.CONFIRM_FRAMES:
                    smoothed = raw_obs
                else:
                    smoothed = ObstacleInfo(
                        detected=prev_detected,
                        area_ratio=raw_obs.area_ratio,
                        offset_x=raw_obs.offset_x,
                        offset_y=raw_obs.offset_y,
                        objects=raw_obs.objects,
                        high_threat=raw_obs.high_threat,
                        primary_class=raw_obs.primary_class,
                    )

                _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 72])
                b64 = base64.b64encode(buf.tobytes()).decode()

                with self._lock:
                    self._snapshot.obstacle = smoothed
                    self._snapshot.frame_b64 = b64
                    self._snapshot.fps = fps
                    self._snapshot.error = None
                    self._snapshot.backend = backend_name
        finally:
            cap.release()
            with self._lock:
                self._snapshot.running = False

    # ── YOLO детектор ─────────────────────────────────────────────────────────

    def _detect_yolo(self, frame: np.ndarray) -> tuple[ObstacleInfo, np.ndarray]:
        h, w = frame.shape[:2]
        frame_area = float(w * h)
        annotated = frame.copy()

        # Трекинг: model.track() добавляет уникальный ID каждому объекту
        # persist=True — трекер помнит объекты между кадрами
        if self.ENABLE_TRACKING:
            try:
                results = self._yolo.track(
                    frame,
                    conf=self.YOLO_CONF,
                    verbose=False,
                    persist=True,
                    tracker="bytetrack.yaml",
                )
            except Exception:
                # bytetrack.yaml недоступен (редко) — fallback на обычный инференс
                results = self._yolo(frame, conf=self.YOLO_CONF, verbose=False)
        else:
            results = self._yolo(frame, conf=self.YOLO_CONF, verbose=False)

        detected_objects: list[DetectedObject] = []
        for r in results:
            for box in r.boxes:
                cls_id   = int(box.cls[0])
                cls_name = self._yolo.names[cls_id]
                conf     = float(box.conf[0])
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                track_id = int(box.id[0]) if (box.id is not None and len(box.id) > 0) else None

                obj = DetectedObject(
                    cls_name=cls_name,
                    confidence=conf,
                    x1=x1, y1=y1, x2=x2, y2=y2,
                    track_id=track_id,
                )
                detected_objects.append(obj)

                # Цвет рамки: красный — угроза, зелёный — прочее препятствие, серый — нейтрально
                is_threat = cls_name in OBSTACLE_CLASSES
                color = (0, 0, 255) if cls_name in HIGH_THREAT_CLASSES else (0, 200, 100) if is_threat else (150, 150, 150)
                cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
                # Метка: "person#3 92%" — класс, track_id, уверенность
                tid_str = f"#{track_id}" if track_id is not None else ""
                label = f"{cls_name}{tid_str} {conf:.0%}"
                cv2.putText(annotated, label, (int(x1), int(y1) - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

        # Фильтруем только "опасные" объекты с достаточным размером
        obstacle_objs = [
            o for o in detected_objects
            if o.cls_name in OBSTACLE_CLASSES and o.area / frame_area >= self.OBSTACLE_AREA_THR
        ]

        detected = len(obstacle_objs) > 0
        primary_class = ""
        offset_x = offset_y = 0.0
        area_ratio = 0.0
        high_threat = False

        if detected:
            # Берём крупнейший объект как главное препятствие
            main = max(obstacle_objs, key=lambda o: o.area)
            primary_class = main.cls_name
            high_threat   = main.cls_name in HIGH_THREAT_CLASSES
            offset_x = (main.center_x - w / 2) / (w / 2)
            offset_y = (main.center_y - h / 2) / (h / 2)
            area_ratio = round(main.area / frame_area, 4)

            # HUD поверх видео
            threat_label = "⚠ HIGH THREAT" if high_threat else "OBSTACLE"
            hud_color = (0, 0, 255) if high_threat else (0, 140, 255)
            cv2.putText(annotated, f"{threat_label}: {primary_class}  dx={offset_x:+.2f}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, hud_color, 2)
            cv2.rectangle(annotated, (4, 4), (w - 4, h - 4), hud_color, 3)
        else:
            cv2.putText(annotated, "CLEAR", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 0), 2)

        # Бейдж с backend (YOLO / YOLO-INT8 / YOLO-ONNX)
        badge = self._yolo_backend.upper()
        cv2.putText(annotated, badge, (w - max(60, len(badge) * 10), h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (100, 100, 255), 1)

        return (
            ObstacleInfo(
                detected=detected,
                area_ratio=area_ratio,
                offset_x=round(offset_x, 3),
                offset_y=round(offset_y, 3),
                objects=obstacle_objs,
                high_threat=high_threat,
                primary_class=primary_class,
            ),
            annotated,
        )

    # ── Canny fallback ────────────────────────────────────────────────────────

    def _detect_canny(self, frame: np.ndarray) -> tuple[ObstacleInfo, np.ndarray]:
        h, w = frame.shape[:2]
        gray    = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (11, 11), 0)
        edges   = cv2.Canny(blurred, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        annotated = frame.copy()
        total_area = cx_sum = cy_sum = weight = 0.0

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self.MIN_CONTOUR_AREA:
                continue
            total_area += area
            M = cv2.moments(cnt)
            if M["m00"] > 0:
                cx = M["m10"] / M["m00"]
                cy = M["m01"] / M["m00"]
                cx_sum += cx * area
                cy_sum += cy * area
                weight += area
            cv2.drawContours(annotated, [cnt], -1, (0, 255, 0), 2)

        frame_area = float(w * h)
        area_ratio = total_area / frame_area
        detected   = area_ratio >= self.CANNY_AREA_THRESHOLD
        offset_x   = offset_y = 0.0

        if detected and weight > 0:
            offset_x = (cx_sum / weight - w / 2) / (w / 2)
            offset_y = (cy_sum / weight - h / 2) / (h / 2)

        label = f"OBSTACLE  area={area_ratio:.1%}  dx={offset_x:+.2f}" if detected else "CLEAR"
        color = (0, 0, 255) if detected else (0, 255, 0)
        cv2.putText(annotated, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        if detected:
            cv2.rectangle(annotated, (5, 5), (w - 5, h - 5), color, 3)
        cv2.putText(annotated, "Canny", (w - 70, h - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)

        return (
            ObstacleInfo(
                detected=detected,
                area_ratio=round(area_ratio, 4),
                offset_x=round(offset_x, 3),
                offset_y=round(offset_y, 3),
            ),
            annotated,
        )
