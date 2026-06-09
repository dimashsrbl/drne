# Drone (INAV/ArduPilot) · Backend + Frontend + Mission Engine

## Архитектура

```
Frontend (React) → Backend (FastAPI) → MAVLink/MSP → Flight Controller (INAV/ArduPilot)
```

Поддерживаемые режимы работы:
- **INAV (железо)**: миссии/RTL, ручное управление (MAVLink или MSP fallback), телеметрия.
- **ArduPilot SITL (симуляция)**: быстрые проверки логики без железа.
- **Mission-only backend**: облегчённый запуск только миссий/навигации (без vision/YOLO).

## Монорепо: mission и vision отдельно

- **`drone-mission/`** — описание деплоя mission-only и пример `systemd` (код по-прежнему в `backend/`).
- **`vision-tracker/`** — отдельный сервис захвата цели (порт **8001**), пока заглушка с JSON API `/target` для будущей интеграции YOLO/OpenCV.

## Быстрый старт (Windows) — backend

### Backend (полный)

В `backend/.env` (пример для INAV через COM-порт):

```env
DRONE_BACKEND_PROFILE=inav
DRONE_MAVLINK_CONNECTION=COM4
DRONE_MAVLINK_BAUD=115200
# auto — сначала MAVLink, если heartbeat нет — MSP SET_RAW_RC на том же порту
DRONE_INAV_RC_TRANSPORT=auto
DRONE_MAVLINK_FALLBACKS=
```

Запуск:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000 --reload
```

Swagger: `http://127.0.0.1:8000/docs`

### Backend (только миссии / облегчённый)
Это вариант для Raspberry Pi / companion computer, когда нужно только:
`/mission`, `/nav`, `/telemetry`, `/drone`.

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements-mission.txt
.\.venv\Scripts\uvicorn.exe app.main_mission_only:app --host 0.0.0.0 --port 8000 --reload
```

## Frontend (Windows)

```powershell
cd frontend
npm install
npm run dev
```

UI: `http://localhost:5173` (proxy на backend через `/api`).

## Миссии (waypoints + RTL)

API: `POST /api/mission` принимает список действий. Пример:

```json
{
  "mission": [
    { "action": "arm" },
    { "action": "takeoff", "alt": 10 },
    { "action": "goto", "lat": 51.1694, "lon": 71.4491, "alt": 15 },
    { "action": "goto", "lat": 51.1698, "lon": 71.4496, "alt": 15 },
    { "action": "rtl" }
  ]
}
```

Статус: `GET /api/mission/status`.

### Тест на реальном INAV без GPS (взлёт ~2 м + посадка)

Только барометр: у шага `takeoff` задай `no_gps: true` — FC перейдёт в **ALT_HOLD**, затем `MAV_CMD_NAV_TAKEOFF`.
Первый раз — **без пропеллеров**; поведение зависит от настроек INAV.

```json
{
  "mission": [
    { "action": "arm" },
    { "action": "takeoff", "alt": 2, "no_gps": true },
    { "action": "wait", "seconds": 3 },
    { "action": "land" }
  ]
}
```

В UI: **Mission Builder** → кнопка **«пресет: тест 2м (no_gps)»** или чекбокс у шага takeoff.

Одиночный взлёт без миссии: `POST /api/drone/takeoff` с телом `{ "altitude": 2, "no_gps": true }`.

## Vision / Obstacle Avoidance (опционально)

Полный backend включает:
- MJPEG stream: `GET /api/vision/stream`
- Статус: `GET /api/vision/status`
- Облёт: `POST /api/vision/avoidance/start`

Backends детекции:
- `YOLO` (ultralytics) — основной
- `Canny` — fallback, если YOLO недоступен

## Safety: Battery Watchdog (опционально)
В фоне проверяет батарею по телеметрии и при критике триггерит RTL.
Пороги задаются через env (см. `backend/app/core/config.py`).

## Raspberry Pi 5 (companion computer) — схема

Идея: фронт/админка отправляет миссию → backend на Pi → Pi управляет FC.

Рекомендуемый запуск на Pi: **mission-only** (`app.main_mission_only:app`).

Минимальные команды на Pi:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
cd ~/drone/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-mission.txt
uvicorn app.main_mission_only:app --host 0.0.0.0 --port 8000
```

Порт FC на Linux обычно `/dev/ttyACM0` или `/dev/ttyUSB0`:

```bash
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

Важно: **не копируй на Pi Windows `.env` с `COM4`**. На Pi используй шаблон
`backend/.env.example.pi` (порт `/dev/serial0` или `/dev/ttyUSB0|/dev/ttyACM0`).

## Gesture Control (прототип “рука → ориентация”)

Отдельный мини‑проект: `gesture-control/`
- Python мост: COM → WebSocket (`gesture-control/pc/ws_imu_bridge.py`)
- Web viewer: 3D “дрончик” (`gesture-control/web/index.html`)

См. `gesture-control/README.md`.

## Unity симулятор (настоящая физика/графика — следующий этап)

MVP-ветка симулятора на Unity: **Unity = физика/картинка**, **наш backend = мозг** (миссии + ручные команды).

Профиль backend:

```env
DRONE_BACKEND_PROFILE=unity_sim
DRONE_UNITY_CMD_HOST=127.0.0.1
DRONE_UNITY_CMD_PORT=15000
DRONE_UNITY_TELEM_PORT=15001
```

Инструкция и скрипты: `sim-unity/README.md`.

## ArduPilot SITL (опционально, для тестов)

Если SITL запускается в WSL2 — backend удобнее запускать тоже в WSL2.
По умолчанию backend в режиме ArduPilot использует `tcp:127.0.0.1:5760`.

Скрипты SITL (если используются): `scripts/`.

---

### Примечание про порты (важно)
Если команды к дрону возвращают 500/timeout — почти всегда причина в том, что:
- порт занят (например INAV Configurator открыт),
- указан неверный `COMx`/`/dev/tty*`,
- нет прав на serial (`dialout` на Linux).
