# Pixhawk 2.4.8 — наши значения (стенд)

Источник: рабочий дамп `pix.param` + `.env` на Raspberry Pi из сессии настройки.  
**Не путать с пресетом Mission Planner `3DR_Iris+.param`** — это чужой квад, его на плату не писать.

После перепрошивки: Config → Full Parameter List → правишь вручную эти строки → **Write Params** → reboot.  
Или Load from file, если сохранился наш `pix.param`.

Прошивка: **ArduCopter, плата `fmuv3`** (запасной: `Pixhawk 1`; если 1 МБ flash — `fmuv2`).

---

## Связь Pi ↔ Pixhawk

| Сторона | Значение |
|---------|----------|
| Разъём | **TELEM2** (не USB для полёта) |
| Pixhawk TX | Pi GPIO15 RXD (pin 10) |
| Pixhawk RX | Pi GPIO14 TXD (pin 8) |
| GND | общий |
| 5V TELEM | **не** на Pi |

### Параметры SERIAL (как было в `pix.param`)

| Параметр | Значение | Смысл |
|----------|----------|--------|
| `SERIAL0_PROTOCOL` | **2** | USB MAVLink2 |
| `SERIAL0_BAUD` | **115** | USB 115200 (только ПК / MP) |
| `SERIAL1_PROTOCOL` | 2 | TELEM1 MAVLink |
| `SERIAL1_BAUD` | 57 | 57600 |
| `SERIAL2_PROTOCOL` | **2** | **TELEM2 → Pi**, MAVLink2 |
| `SERIAL2_BAUD` | **57** | **57600** — так и работало с Pi |
| `SERIAL3_PROTOCOL` | 5 | GPS1 |
| `SERIAL3_BAUD` | 230 | 230400 |
| `SERIAL4_PROTOCOL` | 5 | GPS2 (не используем) |
| `SERIAL4_BAUD` | 230 | |
| `SERIAL5_PROTOCOL` | -1 | выкл |

В гайде wiring иногда 921600 — **у нас не так**. Pi и SERIAL2 = **57600**.

---

## Рама и моторы

| Параметр | Значение | Смысл |
|----------|----------|--------|
| `FRAME_CLASS` | **1** | Quad |
| `FRAME_TYPE` | **1** | X |
| `SERVO1_FUNCTION` … `SERVO4_FUNCTION` | **0** | MAIN свободны |
| `SERVO9_FUNCTION` | **33** | AUX1 = Motor1 (перед-право) |
| `SERVO10_FUNCTION` | **34** | AUX2 = Motor2 (зад-лево) |
| `SERVO11_FUNCTION` | **35** | AUX3 = Motor3 (перед-лево) |
| `SERVO12_FUNCTION` | **36** | AUX4 = Motor4 (зад-право) |
| `MOT_PWM_TYPE` | **6** | DShot600 |
| `MOT_PWM_MIN` | 1000 | |
| `MOT_PWM_MAX` | 2000 | |
| `MOT_SPIN_ARM` | 0.1 | idle после ARM |
| `MOT_SPIN_MIN` | 0.15 | |
| `MOT_SPIN_MAX` | 0.95 | |
| `MOT_THST_HOVER` | 0.35 | |

Проводка: ESC M1–M4 → AUX 1–4. На стенде Motor Test: **B и D крутились, A и C нет** (железо/разъём, не эти параметры).

---

## Стенд без пульта / без GPS (то, что мы ослабляли)

| Параметр | Было | Зачем |
|----------|------|--------|
| `ARMING_CHECK` | **0** | все prearm выкл (только стенд) |
| `ARMING_NEED_LOC` | 0 | не требовать GPS home |
| `ARMING_OPTIONS` | 0 | |
| `ARMING_RUDDER` | 2 | rudder arm/disarm (пульта нет — не используем) |
| `BRD_SAFETY_DEFLT` | **0** | safety switch выкл |
| `BRD_SAFETYOPTION` | 0 | |
| `FS_GCS_ENABLE` | **0** | нет GCS failsafe |
| `FS_THR_ENABLE` | **0** | нет throttle failsafe |
| `INITIAL_MODE` | **0** | STABILIZE |
| `SYSID_THISMAV` | 1 | |
| `SYSID_MYGCS` | 255 | наш backend |

**Поправить после прошивки (в дампе мешало ARM без пульта):**

| Параметр | Было в дампе | Ставить на стенд |
|----------|----------------|------------------|
| `RC_OPTIONS` | **32** (idle throttle) | **1** (игнор RC, пульта нет) |

Пульт не подключали. `RC_PROTOCOLS=1` в дампе.

---

## Датчики / батарея (как в дампе)

| Параметр | Значение | Комментарий |
|----------|----------|-------------|
| `BATT_MONITOR` | **0** | монитор батареи не настроен |
| `GPS1_TYPE` | 1 | GPS включён, в помещении fix=0 |
| `AHRS_GPS_MINSATS` | 6 | |
| `AHRS_EKF_TYPE` | 3 | EKF3 |
| `COMPASS_ENABLE` | 1 | |
| `COMPASS_OFS_X/Y/Z` | -238 / -231 / -44 | калибровка компаса была |
| `INS_ACCOFFS_X/Y/Z` | **0 / 0 / 0** | аксели **не** калиброваны — сделать в MP |

Для взлёта GUIDED нужно GPS 3D ≥ 6 спутников. Стенд ARM — без GPS, force-arm с Pi.

---

## Raspberry Pi `backend/.env` (рабочее)

```env
DRONE_BACKEND_PROFILE=ardupilot
DRONE_MAVLINK_CONNECTION=/dev/serial0
DRONE_MAVLINK_BAUD=57600
DRONE_SITL_FORCE_ARM=true
DRONE_SITL_RELAX_PREFLIGHT=true
DRONE_ARDUPILOT_MISSION_MODE=guided
DRONE_ARDUPILOT_MIN_GPS_SATS=6
DRONE_ARDUPILOT_GOTO_TOL_M=3.0
DRONE_VISION_TRACKER_URL=http://127.0.0.1:8001
```

Сервис: `sudo systemctl restart drone-mission`  
UI стенда: `http://localhost:5173/betaflight` → **Bench ARM → DISARM** (пропы сняты).

`DRONE_SITL_*` — имена от SITL, на реальном FC для **стола** оставляем `true`. На улицу с пропами потом `false`.

---

## После перепрошивки — минимум заново

1. `SERIAL2_PROTOCOL=2`, `SERIAL2_BAUD=57`
2. `FRAME_CLASS=1`, `FRAME_TYPE=1`
3. `SERVO9..12_FUNCTION=33,34,35,36`
4. `MOT_PWM_TYPE=6`
5. `ARMING_CHECK=0`, `BRD_SAFETY_DEFLT=0`, `FS_GCS_ENABLE=0`, `FS_THR_ENABLE=0`
6. `RC_OPTIONS=1`
7. Write Params → reboot
8. На Pi baud **57600**, `systemctl restart drone-mission`

USB в MP: тот COM, который появляется в Диспетчере (не COM1). Скорость USB **115200**.

---

## Чего не делать

- Не грузить **`3DR_Iris+.param`** на нашу плату.
- Не ставить `SERIAL2_BAUD=921`, пока Pi не переведён на 921600.
- Не шить `CUAV-Pixhack-v3` / Cube* — только **`fmuv3`**.
- Не ставить пропы, пока нет куба и калибровки акселей.
