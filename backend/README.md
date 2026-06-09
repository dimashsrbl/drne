# Backend

Минимальный бэкенд на **FastAPI** для управления **ArduCopter** через **MAVLink** (pymavlink).

## Быстрый старт

1. Создай venv и поставь зависимости:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. Запусти ArduCopter SITL (в WSL):

```bash
./scripts/wsl-run-ardupilot.sh
```

3. Запусти API:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Профили бэкенда

Выбери профиль через переменную `DRONE_BACKEND_PROFILE`:

| Профиль | Когда использовать |
|---|---|
| `ardupilot` | ArduPilot SITL / реальный VTOL / ArduCopter |
| `inav` | Реальный дрон на INAV (BOB57 и т.п.) через USB-UART |
| `bob57_bridge` | BOB57 на Betaflight через внешний bridge-скрипт |

## MAVLink подключение

Бэкенд подключается через `DRONE_MAVLINK_CONNECTION`:

| Переменная окружения | Значение по умолчанию | Описание |
|---|---|---|
| `DRONE_MAVLINK_CONNECTION` | `tcp:127.0.0.1:5760` | Основное подключение |
| `DRONE_MAVLINK_BAUD` | `115200` | Baud rate для COM-порта (INAV) |
| `DRONE_MAVLINK_FALLBACKS` | `udpin:0.0.0.0:14550,...` | Запасные варианты (пусто для INAV) |
| `DRONE_INAV_RC_TRANSPORT` | `mavlink` | `mavlink` — только MAVLink; `msp` — RC с ПК через MSP `SET_RAW_RC` (нужен COM); `auto` — сначала MAVLink, иначе MSP |
| `DRONE_INAV_MSP_RC_CHANNELS` | `8` | Число каналов в MSP `SET_RAW_RC` (часто 8 или 16) |
| `DRONE_SITL_RELAX_PREFLIGHT` | `true` | Отключить ARM-проверки (только ArduPilot SITL) |
| `DRONE_SITL_FORCE_ARM` | `true` | Force-ARM param2=21196 (только ArduPilot) |

Файл `.env` в папке `backend/` переопределяет настройки.

### Пример запуска под INAV (BOB57 по USB-UART):

```powershell
$env:DRONE_BACKEND_PROFILE="inav"
$env:DRONE_MAVLINK_CONNECTION="COM7"        # твой COM-порт USB-UART адаптера
$env:DRONE_MAVLINK_BAUD="115200"
$env:DRONE_MAVLINK_FALLBACKS=""
# Джойстик с ПК по USB без MAVLink heartbeat: MSP SET_RAW_RC
# $env:DRONE_INAV_RC_TRANSPORT="msp"   # или "auto" — попытка MAVLink, затем MSP
$env:DRONE_SITL_RELAX_PREFLIGHT="false"
$env:DRONE_SITL_FORCE_ARM="false"
.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000
```

### Пример запуска под ArduPilot SITL:

```powershell
$env:DRONE_BACKEND_PROFILE="ardupilot"
$env:DRONE_MAVLINK_CONNECTION="tcp:127.0.0.1:5760"
.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000
```

## Эндпоинты

| Метод | Путь | Тело | Описание |
|---|---|---|---|
| `POST` | `/drone/arm` | — | ARM двигателей |
| `POST` | `/drone/disarm` | — | DISARM |
| `POST` | `/drone/takeoff` | `{"altitude": 10}` | Взлёт на N метров **AGL** |
| `POST` | `/drone/land` | — | Посадка |
| `POST` | `/drone/goto` | `{"lat": 43.2, "lon": 76.9, "alt": 15}` | Перелёт (alt = AGL, м) |
| `POST` | `/drone/return-home` | — | RTL |
| `POST` | `/drone/manual-control` | `{"pitch":0,"roll":0,"thrust":500,"yaw":0}` | Ручное управление (~14 Гц) |
| `POST` | `/drone/home` | `{"lat":…,"lon":…,"alt":…}` | Установить точку дома (AMSL) |
| `POST` | `/drone/flight-mode` | `{"mode": "LOITER"}` | Смена режима |
| `GET` | `/telemetry` | — | Снимок телеметрии |
| `POST` | `/mission` | список действий | Запуск миссии |
| `GET` | `/mission/status` | — | Статус миссии |
| `POST` | `/nav/route` | список waypoints | Маршрут по точкам |

### Доступные режимы полёта

`STABILIZE`, `ALT_HOLD`, `LOITER`, `POSHOLD`, `GUIDED`, `LAND`, `RTL`, `AUTO`, `BRAKE`
