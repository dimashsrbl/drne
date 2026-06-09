ТЗ: Система управления автономным дроном (MVP)
1. 🎯 Цель продукта

Разработать систему управления дроном, которая:

работает через симуляцию (без железа)

управляет дроном через миссии

отображает всё в интерфейсе

легко переносится на реальный дрон

2. 🧱 Архитектура системы
Frontend (Web / Desktop)
        ↓
Backend (API + логика)
        ↓
Drone Control Service (MAVLink)
        ↓
PX4 Autopilot
        ↓
Gazebo (симуляция)
3. ⚙️ Основные модули
3.1 🚁 Drone Control Service

Описание:
Сервис, который управляет дроном через MAVLink

Функции:

arm / disarm

takeoff

land

goto (координаты)

return to home

API (пример):

POST /drone/takeoff
{
  "altitude": 10
}
POST /drone/goto
{
  "lat": 43.2389,
  "lon": 76.8897,
  "alt": 15
}
3.2 🧠 Mission Engine

Описание:
Система сценариев (логика поведения дрона)

Функции:

выполнение миссий

условия (if / events)

реакция на события

Пример миссии:

{
  "mission": [
    {"action": "takeoff", "alt": 10},
    {"action": "goto", "lat": 43.2, "lon": 76.8},
    {"action": "wait", "seconds": 5},
    {"action": "land"}
  ]
}
3.3 📡 Telemetry Service

Описание:
Получение данных от дрона

Данные:

координаты

скорость

заряд батареи

статус

Формат:

{
  "lat": 43.2389,
  "lon": 76.8897,
  "alt": 12,
  "battery": 87,
  "status": "flying"
}
3.4 🖥️ Frontend (панель управления)

Основные экраны:

1. Dashboard

статус дрона

кнопки:

takeoff

land

return

2. Карта

отображение дрона

построение маршрута

точки (waypoints)

3. Mission Builder

создание миссий

drag & drop действий

3.5 🗺️ Navigation Module (MVP)

Функции:

движение по waypoint

простая логика маршрута

4. 🔌 Технологии
Backend:

Python (FastAPI)
или

Node.js (NestJS)

Drone:

PX4 autopilot

MAVLink

Simulation:

Gazebo simulator

Frontend:

React

Mapbox / Leaflet

5. 🔁 Основные сценарии
Сценарий 1 — взлёт

пользователь нажимает takeoff

запрос → backend

backend → MAVLink

PX4 → выполняет

Сценарий 2 — миссия

пользователь создаёт миссию

отправляет на сервер

Mission Engine выполняет

команды идут в дрон

Сценарий 3 — телеметрия

PX4 отправляет данные

backend принимает

frontend отображает

6. 🧪 Симуляция

Система должна:

работать через Gazebo

не требовать реального дрона

поддерживать тестовые миссии

7. 🧩 Расширения (не MVP)

obstacle avoidance

computer vision

multi-drone

AI planner

интеграция со складскими роботами (🔥 твоя тема)

🚀 Как тебе начать прямо сегодня

Вот без воды:

Шаг 1

Поднять:

PX4

Gazebo

QGroundControl

Шаг 2

Сделать сервис:

takeoff()
goto()
land()
Шаг 3

Подключить API

Шаг 4

Сделать простой UI

