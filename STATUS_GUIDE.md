# Статус дрона (кратко)

**Сейчас:** железо и софт на стенде готовы, тесты **без пропеллеров**.  
**Ждём:** готовый куб/раму для полноценных лётных тестов.

---

## Что за система

| Часть | Роль |
|--------|------|
| **Pixhawk 2.4.8** | Автопилот, ArduCopter 4.6.x |
| **Raspberry Pi 5** | Companion: backend + UI + камера |
| **ESC iFlight BLITZ E55** | Моторы по DShot (AUX 1–4) |
| **Браузер** | Миссии и телеметрия (`/betaflight` → Pixhawk runner) |

Цепочка команд: **браузер → Pi (FastAPI) → UART TELEM2 → Pixhawk (MAVLink)**.

---

## Что уже сделано

### Железо / Pixhawk
- Переход с Betaflight/MSP на **Pixhawk + ArduPilot**
- Связь Pi ↔ Pixhawk: **TELEM2 → `/dev/serial0`**, baud **57600** (как `SERIAL2_BAUD`)
- Моторы на **AUX 1–4 + DShot**; wiring — `backend/VTOL_wiring_guide.md`
- Параметры для стенда: `ARMING_CHECK=0`, `BRD_SAFETY_DEFLT=0`, force-arm с Pi
- Профиль backend: `DRONE_BACKEND_PROFILE=ardupilot`

### Софт
- Backend на Pi: телеметрия, ARM/DISARM, миссии (`/mission`, stop LAND/RTL/DISARM)
- Frontend: страница миссий Pixhawk (пресеты «Взлёт 1 м → LAND», **Bench ARM → DISARM**)
- Диагностика: `/drone/link-debug`, `/drone/arm-debug`

### Камера (Raspberry Pi)
- Подключена **камера Raspberry Pi**
- Отдельный сервис **vision-tracker** (порт **8001**): стрим / захват цели под трекинг
- В UI есть раздел **«Камера»**; интеграция с полётом — дальше, после куба

### Тесты (без пропеллеров)
- Link / heartbeat / телеметрия с Pixhawk — ок
- Стендовый **Bench ARM → DISARM** (force-arm) — отладка arming
- Motor Test / проверка выходов AUX (часть моторов крутилась на стенде)
- **Пропеллеры не ставить**, пока нет куба и явной готовности к полёту

---

## Что не делаем, пока нет куба

- Полёты с пропеллерами / взлёт на улице с GPS
- Финальная калибровка под раму (вибрации, compass после сборки)
- Полный vision-follow в воздухе

---

## Как поднять стенд (когда снова сядете)

1. Питание Pixhawk + Pi, UART на месте  
2. На Pi: `sudo systemctl restart drone-mission` (+ vision-tracker, если нужен стрим)  
3. В `.env`: `ardupilot`, `/dev/serial0`, `57600`, для стенда `DRONE_SITL_FORCE_ARM=true`  
4. UI: `http://<ip-pi или localhost:5173>/betaflight`  
5. Сначала **Bench ARM → DISARM** без пропов; взлёт — только с GPS на улице и после сборки куба

---

## Итог одной фразой

Стенд **Pi + Pixhawk + камера + софт миссий** собран и проверен без пропов; **ждём куб** — дальше калибровка под раму и лётные тесты.
