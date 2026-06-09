# Drone Mission (бортовой backend)

Исходный код миссий и INAV/MAVLink остаётся в каталоге **`backend/`** в корне репозитория (история и импорты не ломаем). Эта папка — **точка входа для деплоя и документации** mission-only режима.

## Что здесь зачем

- Зафиксировать, что «полётный» сервис — отдельный контур от vision.
- Держать пример **systemd** под Raspberry Pi без дублирования `requirements` (они в `backend/requirements-mission.txt`).

## Запуск (как раньше, из `backend/`)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-mission.txt
uvicorn app.main_mission_only:app --host 0.0.0.0 --port 8000
```

Переменные окружения — в `backend/.env` (см. `backend/.env.example.pi` в репозитории при наличии).

## Интеграция с vision-tracker

Основной backend может опрашивать vision по HTTP, например:

- `http://127.0.0.1:8001/health`
- `http://127.0.0.1:8001/target`

Порт **8001** зарезервирован под `vision-tracker`, **8000** — под mission API.

## systemd

Пример юнита: `deploy/drone-mission.service.example` — скопировать на Pi в `/etc/systemd/system/`, поправить пути и пользователя, затем `daemon-reload`, `enable`, `start`.
