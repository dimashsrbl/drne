# Vision Tracker

Отдельный HTTP‑сервис для детекции/трекинга цели. Сейчас — **заглушка** с фиксированным JSON‑контрактом; позже сюда подключаются OpenCV + YOLO (INT8 ONNX и т.д.).

## Зачем отдельно от mission backend

- Разные зависимости и нагрузка (камера, inference).
- Можно перезапускать vision без остановки полётного API.
- Интеграция с `drone-mission`: HTTP на `localhost:8001`.

## Поведение (цель в центре)

- Детекция (YOLO, если установлен `ultralytics` и модель грузится).
- Выбирается один объект: по умолчанию **самый «уверенный» по площади**; можно задать класс `VISION_TARGET_CLASS=person`.
- В ответе `/target`: `cx`, `cy` (−1…1) — смещение центра объекта относительно центра кадра; `hint.horizontal` / `hint.vertical` — куда **сдвинуть камеру** (стрелки на `/ui`).

## Переменные окружения

| Переменная | Пример | Смысл |
|------------|--------|--------|
| `VISION_VIDEO_SOURCE` | `0` | Индекс веб‑камеры, **`/dev/video0`** на Pi (предпочтительно), URL или файл |
| `VISION_V4L_DEVICES` | | Через запятую узлы V4L2, если автопоиск USB не сработал |
| `VISION_CAPTURE_WIDTH` | `640` | Запрос ширины кадра у камеры |
| `VISION_CENTER_DEADBAND` | `0.08` | Зона «считаем центр» (меньше — чувствительнее) |
| `VISION_TARGET_CLASS` | `person` | Оставить только этот класс COCO (пусто = все классы, лучший по площади×conf) |
| `VISION_YOLO_MODEL` | `yolo11n.pt` | Веса YOLO (или свой `.pt`) |
| `VISION_CAMERA_FALLBACK` | `0,1,2` | Перебор индексов веб‑камеры, если основной не даёт кадр |
| `VISION_PREVIEW_MAX_WIDTH` | `640` | Ширина JPEG превью (меньше — быстрее по сети) |
| `VISION_PREVIEW_JPEG_QUALITY` | `55` | Качество JPEG превью (30–95; ниже — меньше байт и задержка) |
| `VISION_BUFFER_DRAIN` | `3` | Сколько кадров сбросить из буфера камеры перед обработкой (убирает лаг в браузере) |
| `VISION_YOLO_IMGSZ` | `320` | Размер входа YOLO (320 быстрее на Pi; 0 = размер по умолчанию) |
| `VISION_DETECT_EVERY` | `1` | Детекция каждые N кадров без lock (2–3 снижает нагрузку) |
| `VISION_LOCK_DETECT_EVERY` | `2` | YOLO при lock каждые N кадров; между ними OpenCV-трекер (KCF/CSRT) |
| `VISION_OPENCV_TRACKER` | `on` | `off` — без OpenCV-трекера между YOLO |
| `VISION_OPENCV_TRACKER_KIND` | `CSRT` | `KCF` быстрее на Pi, `CSRT` точнее |
| `VISION_CAMERA_USER` / `VISION_CAMERA_PASSWORD` | | Для IP-камеры: логин/пароль подставляются в HTTP/RTSP URL (если в `VISION_VIDEO_SOURCE` ещё нет `user:pass@`) |
| `VISION_CAMERA_STREAM_PATH` | *(необязательно)* | Свои пути MJPEG через запятую. Если пусто — встроенный список (Dahua/Foscam/…) |
| `VISION_CAMERA_PROBE_PORTS` | `80` | HTTP-порты для перебора. Раньше по умолчанию сканировались 8080/81 — на закрытых портах FFmpeg долго ждал и писал `tcp ... Error number -138`. Нужны доп. порты: `80,8080` |
| `VISION_CAMERA_RTSP_PORTS` | `554` | Порты RTSP (через запятую). Раньше по умолчанию был и 8554 — давал 30 с ожидания на закрытый порт |
| `VISION_CAMERA_RTSP_PATH` | | Дополнительные пути RTSP (через запятую), если автоподбор не сработал |
| `VISION_CAMERA_RTSP_USER` / `VISION_CAMERA_RTSP_PASSWORD` | | Если заданы — используются для RTSP вместо `VISION_CAMERA_*` (на многих камерах **401** на RTSP из‑за отдельной учётки или пароля) |
| `VISION_FFMPEG_CAPTURE_OPTIONS` | | Доп. опции FFmpeg для OpenCV (через `\|`), к ним автоматически добавляется `rtsp_transport;tcp` |
| `VISION_FFMPEG_BIN` | | Полный путь к `ffmpeg.exe`, если не в PATH |
| `VISION_FFMPEG_CLI_RTSP_TIMEOUT_SEC` | `15` | Ожидание первого кадра при RTSP через CLI-ffmpeg |
| `VISION_DISABLE_FFMPEG_CLI` | | `1` — не вызывать системный ffmpeg (только OpenCV) |

После **401** на RTSP в OpenCV сервис автоматически пробует **тот же URL** через **системный ffmpeg** (вывод MJPEG в pipe). Нужен **ffmpeg в PATH** (Windows: `winget install ffmpeg` или [ffmpeg.org](https://ffmpeg.org/download.html)). Полноценный ffmpeg часто проходит **Digest**-авторизацию, с которой падает встроенный libav в OpenCV.
| `VISION_EVENT_LOG_PATH` | *(пусто)* | Путь к JSONL: открытие потока, срывы кадра, цель найдена/потеряна, lock/unlock, периодический `frame_summary`. Пока не задано — файл не пишется; пример: `vision-events.jsonl` |
| `VISION_EVENT_SUMMARY_SEC` | `30` | Как часто писать `frame_summary` (сек.) |

### IP-камера (`http://192.168.88.47/` и т.п.)

Главная страница в браузере — это **HTML**, а не видеопоток. Нужен **точный URL субпотока** (часто MJPEG или RTSP). Если указан только хост `http://IP`, код сам перебирает типовые пути MJPEG на порту **80**, затем типовые **RTSP** (Dahua/Hik/… на 554/8554).

Сообщение в консоли вида `[tcp ...] Error number -138` — обычно **таймаут к закрытому порту** (раньше сканировался 8080; теперь по умолчанию только 80).

Примеры ручного URL:

- MJPEG: `http://192.168.88.47:8080/video.mjpg`, `.../cgi-bin/mjpg/video.cgi`
- RTSP: `rtsp://192.168.88.47:554/stream1`

Задайте полный URL в `VISION_VIDEO_SOURCE` **или** базу + учётные данные:

```powershell
$env:VISION_VIDEO_SOURCE="http://192.168.88.47"
$env:VISION_CAMERA_USER="admin"
$env:VISION_CAMERA_PASSWORD="ваш_пароль"
$env:VISION_CAMERA_STREAM_PATH="/videostream.cgi"
$env:VISION_EVENT_LOG_PATH="vision-events.jsonl"
```

Пароль в логах **не** попадает: в файл и в `camera_open_*` пишется замаскированный URL.

Просмотр последних событий: `GET http://127.0.0.1:8001/events/recent?limit=100`.

### Windows: вебка «подключена», но `0×0` / нет кадра

Частая причина — backend OpenCV по умолчанию; в коде уже пробуется **DirectShow (`CAP_DSHOW`)** и **MSMF**, затем другие индексы.

1. Закрой приложения, которые могут держать камеру (Teams, Zoom, браузер с превью).
2. Укажи другой индекс или перебор:
   ```powershell
   $env:VISION_VIDEO_SOURCE="1"
   $env:VISION_CAMERA_FALLBACK="0,1,2"
   ```
3. Если не помогает — поставь полный пакет **`opencv-python`** вместо `opencv-python-headless` (на Windows с вебкой иногда надёжнее).

Если YOLO не загрузился, `backend=stub`, цель всегда `lost` — проверь установку зависимостей и камеру.

Стартовая страница: **`http://127.0.0.1:8001/`** перенаправляет на **`/ui`** (камера + стрелки). JSON со всеми путями: **`GET /meta`**.

## API

| Метод | Путь      | Описание              |
|-------|-----------|------------------------|
| GET   | `/health` | жив ли сервис         |
| GET   | `/target` | состояние цели (JSON) |
| GET   | `/ui`     | страница: превью камеры + стрелки |
| GET   | `/stream` | MJPEG поток (картинка для `<img src=…>`) |
| GET   | `/snapshot.jpg` | один JPEG кадр |
| POST  | `/lock`   | зафиксировать объект **в центре кадра** (тот же класс дальше одного) |
| POST  | `/unlock` | снять фиксацию, снова авто‑выбор цели |
| GET   | `/docs`   | Swagger               |

## Запуск локально

### Linux / macOS

```bash
cd vision-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

### Windows (PowerShell)

Из каталога репозитория **или** уже находясь в `vision-tracker` (не дублируй путь `vision-tracker\vision-tracker`):

```powershell
cd C:\path\to\drone\vision-tracker
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Если активация скрипта запрещена политикой: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Команда `source` в PowerShell **не работает** — только `Activate.ps1` как выше. Запуск через `python -m uvicorn`, если команда `uvicorn` не в PATH.

## systemd на Raspberry Pi

Пример: `deploy/vision-tracker.service.example` — скопировать в `/etc/systemd/system/`, поправить пути, `daemon-reload`, `enable`, `start`.

## Порты

- **8000** — mission backend (`backend/app/main_mission_only.py`)
- **8001** — этот сервис
