# Unity Sim — план работ

Пошаговый чеклист: от текущего MVP до реалистичной физики и (позже) Betaflight SITL.

**Принцип:** каждая фаза заканчивается **проверяемым сценарием** через тот же UI, что и прод: `gamepad → React → backend (unity_sim) → UDP → Unity`.

**Связанные файлы в монорепо:**

| Где | Что |
|-----|-----|
| `sim-unity/Assets/Scripts/` | C# скрипты симулятора |
| `backend/app/services/unity_sim_adapter.py` | UDP-команды и приём телеметрии |
| `backend/app/core/config.py` | `DRONE_UNITY_*` порты |
| `frontend/src/pages/Sim3DPage.tsx` | UI для симулятора |
| `frontend/src/hooks/useGamepad.ts` | Web Gamepad API |

**Запуск для проверки:**

```env
# backend/.env
DRONE_BACKEND_PROFILE=unity_sim
DRONE_UNITY_CMD_HOST=127.0.0.1
DRONE_UNITY_CMD_PORT=15000
DRONE_UNITY_TELEM_PORT=15001
```

```bash
# backend
uvicorn app.main:app --reload

# frontend (Windows)
npm run dev
# Sim3D / manual-control + Unity Play
```

---

## Фаза 0 — текущее состояние (baseline)

> Уже есть. Не ломать, пока не готов successor.

- [x] `UnityUdpBridge.cs` — UDP cmd:15000, telem:15001, JSON
- [x] `UnitySimDroneController.cs` — упрощённая физика (одна тяга + торки)
- [x] `UnitySimAdapter` — `manual`, `arm`, `takeoff`, `land`, `rtl`, `goto`
- [x] Телеметрия: fake GPS (`lat/lon/alt/yaw`) для карты во frontend

**Ограничения MVP (осознанно):**

- PhysX ~50 Гц по умолчанию, без модели 4 моторов
- RC приходит как `manual` (-1000..1000), не как PWM 1000–2000 µs
- Миссии — грубый P-контроллер к точке, не baro-hold как на Betaflight
- UDP-приём в `Update()`, без фоновой очереди

**Критерий «Фаза 0 работает»:** Play в Unity + backend `unity_sim` → геймпад крутит дрон, WS-телеметрия на UI.

---

## Фаза 1 — реалистичная физика + модульная архитектура

Цель: управляемый квадrocopter на PhysX (500 Гц, 4 мотора, rate/angle PID).  
Продакшн-путь управления **сохраняем**; legacy JSON **не удаляем**.

### 1.1 Структура скриптов

```
Assets/Scripts/
├── Bridge/
│   ├── UdpBridge.cs          # замена UnityUdpBridge (очередь + main thread)
│   └── Protocol.cs           # DTO v1 legacy + v2
├── Flight/
│   ├── IFlightController.cs
│   └── PidFlightController.cs
├── Physics/
│   ├── MotorModel.cs
│   └── DronePhysics.cs
├── Telemetry/
│   └── TelemetryPublisher.cs
├── Camera/
│   └── CameraRig.cs
├── Sim/
│   └── SimManager.cs
└── Legacy/                   # временно
    ├── UnityUdpBridge.cs
    └── UnitySimDroneController.cs
```

- [x] Создать структуру папок
- [x] `DroneSimBootstrap` — одна точка входа на Drone
- [x] Legacy-компоненты в `Legacy/`, отключаются bootstrap'ом

### 1.2 PhysX и модель дрона

- [x] `Time.fixedDeltaTime = 0.002f` (500 Гц) в `DronePhysics.Awake`
- [x] `Rigidbody.mass` ≈ 0.72 кг
- [x] Явный `inertiaTensor` (симметричный X: `Ixx ≈ Iyy < Izz`)
- [x] 4 motor mounts, конфигурация **X** (BR, FR, BL, FL)
- [x] `spinDir` CW/CCW для реактивного yaw-момента
- [x] BoxCollider на корпусе + визуальный proxy
- [x] `MotorModel`: `thrust = k_t · ω²`, lag ~30 ms, реактивный момент
- [x] `AddForceAtPosition` на каждый мотор + суммарный yaw torque

### 1.3 PID-контроллер (Фаза 1)

- [x] `IFlightController`: вход RC, выход 4× 0..1
- [x] Rate loop → angle loop (режимы **rate** / **angle**)
- [x] Throttle: hover ~0.41 (калибровка под BOB57 µs)
- [x] Disarm → моторы 0, сброс PID
- [ ] Serialize gains в Inspector (MonoBehaviour wrapper) — тюнинг в коде

### 1.4 UDP-мост (улучшение)

- [x] Фоновый поток приёма → `ConcurrentQueue` → drain в `Update`
- [x] Отправка телеметрии только с main thread
- [x] Поддержка **legacy** типов
- [x] Поддержка **v2** `type: "rc"` с `channels[8]` в µs
- [x] Ручной JSON parser (без Newtonsoft)

### 1.5 Телеметрия v2 + совместимость с UI

- [x] `TelemetryPublisher` 30 Гц
- [x] v2 поля: `pos`, `vel`, `att_q`, `ang_vel`, `motors`, `baro_alt`
- [x] Legacy поля `lat/lon/alt/yaw/speed`
- [x] Backend читает `baro_alt` в `unity_sim_adapter.py`

### 1.6 Камера и сцена

- [x] `CameraRig`: FPV + chase, клавиша `C`
- [ ] URP project template в репо (сцена — в Unity Editor)
- [ ] Plane/terrain + HDRI polish
- [ ] Indoor box для vision

### 1.7 Миссии (минимум как сейчас)

- [x] `arm` / `disarm`
- [x] `takeoff(alt)` — baro P-hold setpoint
- [x] `land` — снижение + auto disarm у земли
- [x] `goto` — local target из lat/lon (GeoUtil)
- [x] `rtl` → home point

### 1.8 Backend

- [x] `manual_control` дублирует `type: "rc"` v2 (AETR µs)
- [x] `DRONE_UNITY_PROTOCOL_V2` в config + `.env.example`
- [ ] RC 100–250 Гц (сейчас frontend ~14 Гц — опционально поднять)

**Не делать в Фазе 1:** Betaflight SITL, CFD, HDRP, vision pipeline.

---

## Фаза 2 — Betaflight SITL / HIL

Цель: та же прошивка/PID/rates, что на F722; тюнинг переносится 1:1.

- [ ] Собрать/запустить Betaflight SITL на Windows рядом с Unity
- [ ] `SitlAdapter : IFlightController` — обмен IMU/baro/motor outputs
- [ ] Синхронный шаг SITL с `FixedUpdate` (не real-time drift)
- [ ] RC в SITL через MSP `SET_RAW_RC` (как `bob57_bridge`)
- [ ] Переключатель в `SimManager`: PID (dev) ↔ SITL (prod-fidelity)
- [ ] Baro alt-hold ~1 m сопоставим с миссией BOB57

**Критерий:** один и тот же `.env` rates/PID на симе и на Pi дают похожее поведение hover.

**Главный риск:** синхронизация времени SITL ↔ Unity — выделить отдельный spike/prototype до полной интеграции.

---

## Фаза 3 — полировка аэродинамики

Только после стабильного hover в Фазе 1 или 2:

- [ ] Линейный + квадратичный drag
- [ ] Ground effect у поверхности
- [ ] Battery sag (опционально)
- [ ] Более детальные сцены (Poly Haven / Synty / Kenney)

---

## Фаза 4 — vision-follow (отдельно)

Не начинать, пока Фаза 1.6 не зелёная.

- [ ] RenderTexture / отдельный порт видео из Unity
- [ ] Подключение `vision-tracker` к кадрам симулятора
- [ ] End-to-end: follow target в sim scene

---

## Протокол (шпаргалка)

### Legacy (v1, сейчас) — **не удалять**

```json
{"type":"manual","pitch":0,"roll":0,"yaw":0,"thrust":500}
{"type":"arm"}
{"type":"takeoff","alt":1.0}
```

Телеметрия:

```json
{"type":"telemetry","lat":51.17,"lon":71.45,"alt":1.2,"yaw":180,"speed":0.5,"armed":true,"mode":"ANGLE"}
```

### Целевой v2 (расширение)

```json
{"v":2,"type":"rc","channels":[1500,1500,988,1500,2000,2000,1000,1000],"arm":true}
{"v":2,"type":"set_mode","mode":"angle"}
{"v":2,"type":"sim","action":"reset"}
```

```json
{
  "v": 2, "type": "telemetry", "t": 12.3, "armed": true, "mode": "angle",
  "pos": [0, 1.02, 0], "vel": [0,0,0], "att_q": [0,0,0,1],
  "ang_vel": [0,0,0], "motors": [0.41,0.40,0.41,0.40],
  "baro_alt": 1.02,
  "lat": 51.17, "lon": 71.45, "alt": 1.02, "yaw": 0, "speed": 0
}
```

---

## Порядок выполнения (рекомендуемый)

Выполнять **сверху вниз**; не перескакивать на SITL раньше 1.3.

| # | Задача | Оценка |
|---|--------|--------|
| 1 | 1.2 PhysX + MotorModel + DronePhysics | 2–3 дня |
| 2 | 1.3 PidFlightController + hover tune | 3–5 дней |
| 3 | 1.4 UdpBridge с очередью + dual protocol | 1–2 дня |
| 4 | 1.5 TelemetryPublisher + legacy GPS | 1 день |
| 5 | 1.6 CameraRig + URP сцена | 1–2 дня |
| 6 | 1.7 Миссии takeoff/land | 1–2 дня |
| 7 | 1.8 Backend rc channels (опционально) | 0.5 дня |
| 8 | Фаза 2 SITL spike | 2+ недели |

---

## Scope traps (не делать)

- ❌ Свой физический движок вместо PhysX
- ❌ CFD / полноценная аэродинамика на старте
- ❌ «Финальный» свой FC вместо SITL
- ❌ HDRP
- ❌ 1 кГц PhysX без профилирования
- ❌ Vision до стабильного hover
- ❌ Полный rewrite UDP-моста без legacy compat

---

## Журнал прогресса

| Дата | Сделано | Следующий шаг |
|------|---------|---------------|
| 2026-06-16 | Фаза 1: модули Bridge/Flight/Physics/Telemetry/Camera/Sim, PID, v2 RC | Hover tune на Tango 2, URP сцена |

*(Заполняем по мере работы.)*

---

## Ссылки

- Полный RFC: `docs/DRONE_SIM_DESIGN.md` *(если положили в корень монорепо)*
- Текущий MVP README: [README.md](./README.md)
