"""
Экспорт YOLOv8n → INT8 ONNX для Raspberry Pi 5 (ARM Cortex-A76).

Запускать ОДИН РАЗ на мощном компе, затем скопировать
yolov8n_int8.onnx → папку, откуда запускается uvicorn.

Требования:
  pip install ultralytics onnx onnxruntime

Использование:
  cd backend
  python tools/export_int8.py

Шаги:
  1. Скачать yolov8n.pt (авто через ultralytics)
  2. Экспортировать в FP32 ONNX
  3. Применить динамическую INT8-квантизацию (onnxruntime)
     → Dynamic quant не требует калибровочных данных,
       даёт ~1.5-2× прирост на ARM при незначительной потере точности.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

SRC_MODEL  = "yolov8n.pt"
ONNX_FP32  = Path("yolov8n_fp32.onnx")
ONNX_INT8  = Path("yolov8n_int8.onnx")
IMG_SIZE   = 640


def step1_export_onnx() -> None:
    """Экспортируем PyTorch-модель в FP32 ONNX."""
    if ONNX_FP32.exists():
        log.info("FP32 ONNX уже есть: %s — пропускаем", ONNX_FP32)
        return

    try:
        from ultralytics import YOLO  # type: ignore[import-untyped]
    except ImportError:
        log.error("ultralytics не установлен: pip install ultralytics")
        sys.exit(1)

    log.info("Загружаем %s и экспортируем в ONNX FP32...", SRC_MODEL)
    model = YOLO(SRC_MODEL)
    export_path = model.export(format="onnx", imgsz=IMG_SIZE, simplify=True)
    exported = Path(export_path)

    # ultralytics кладёт рядом с .pt — переименуем
    if exported != ONNX_FP32:
        exported.rename(ONNX_FP32)

    log.info("FP32 ONNX сохранён: %s", ONNX_FP32)


def step2_quantize_int8() -> None:
    """Квантизируем FP32 ONNX → INT8 ONNX (dynamic quantization)."""
    if ONNX_INT8.exists():
        log.info("INT8 ONNX уже есть: %s — пропускаем", ONNX_INT8)
        return

    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic  # type: ignore[import-untyped]
    except ImportError:
        log.error("onnxruntime не установлен: pip install onnxruntime")
        sys.exit(1)

    log.info("Квантизируем %s → %s (INT8 dynamic)...", ONNX_FP32, ONNX_INT8)
    quantize_dynamic(
        str(ONNX_FP32),
        str(ONNX_INT8),
        weight_type=QuantType.QInt8,
    )
    log.info("Готово! INT8 ONNX: %s", ONNX_INT8)
    log.info(
        "Теперь скопируйте %s в папку backend/ на Raspberry Pi 5 рядом с uvicorn.",
        ONNX_INT8,
    )


def print_sizes() -> None:
    for p in [Path(SRC_MODEL), ONNX_FP32, ONNX_INT8]:
        if p.exists():
            size_mb = p.stat().st_size / 1_048_576
            log.info("%-25s  %.1f MB", p.name, size_mb)


if __name__ == "__main__":
    step1_export_onnx()
    step2_quantize_int8()
    print_sizes()
