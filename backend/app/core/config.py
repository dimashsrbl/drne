from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DRONE_", env_file=".env", extra="ignore")

    # Профиль бэкенда: "ardupilot" | "inav" | "bob57_bridge"
    backend_profile: str = "ardupilot"

    bob57_bridge_timeout_s: float = 3.0
    bob57_allow_write_commands: bool = False
    video_stream_url: str | None = None

    # ---- MAVLink transport (общий для ardupilot и inav) ---------------
    # ardupilot SITL:       tcp:127.0.0.1:5760
    # INAV через USB-UART:  COM7  (Windows) / /dev/ttyUSB0 (Linux)
    mavlink_connection: str = "tcp:127.0.0.1:5760"

    # Запасные адреса (через запятую). Для INAV оставляй пустым.
    mavlink_fallbacks: str = "udpin:0.0.0.0:14550,udpin:0.0.0.0:14551,tcp:127.0.0.1:5762"

    # Baud для serial-подключения (COM-порт / USB-UART → INAV UART4).
    mavlink_baud: int = 115200

    mavlink_system_id: int = 255
    mavlink_component_id: int = 0
    mavlink_target_system: int | None = None
    mavlink_target_component: int | None = None
    mavlink_heartbeat_timeout_s: float = 15.0

    # ---- Safety: battery watchdog ----------------------------------------
    # Порог предупреждения (%). При достижении пишем в лог.
    battery_warn_pct: float = 25.0
    # Порог критического разряда (%). Автоматический RTL.
    battery_rtl_pct: float = 15.0
    # Задержка между проверками watchdog (сек).
    battery_check_interval_s: float = 5.0

    # ---- INAV: канал ручника (MAVLink MANUAL_CONTROL vs MSP SET_RAW_RC) ---
    # mavlink — только pymavlink (как раньше).
    # msp     — только pyserial + MSP SET_RAW_RC на DRONE_MAVLINK_CONNECTION (COM/tty).
    # auto    — сначала MAVLink; если нет heartbeat — fallback на MSP (телеметрия только при MAVLink).
    inav_rc_transport: str = "mavlink"
    # Сколько каналов в MSP SET_RAW_RC (часто 8; в новых сборках может быть 16).
    inav_msp_rc_channels: int = 8

    # ---- Betaflight: временный MSP RC runner -------------------------------
    # USB VCP на Pi обычно /dev/ttyACM0. GPIO UART можно указать /dev/serial0.
    betaflight_port: str = "/dev/ttyACM0"
    betaflight_baud: int = 115200
    betaflight_rc_hz: float = 25.0
    betaflight_rc_channels: int = 8
    betaflight_arm_channel: int = 5
    betaflight_angle_channel: int = 6
    # True → AUX angle channel high (ANGLE mode). False for bench ARM on tilted surface.
    betaflight_enable_angle: bool = True
    # GPS Position Hold: дрон сам держит точку (улица, ≥8 спутников, BF 4.5+).
    # 0 = выключено. Иначе номер AUX-канала (например 7), настроенного в Modes как POS HOLD.
    betaflight_poshold_channel: int = 0
    betaflight_poshold_us: int = 2000
    # Поднимать POS HOLD автоматически в фазах висения (после взлёта, hold_alt).
    betaflight_poshold_auto: bool = True
    # Лимиты throttle (µs). BOB57 с пропами: взлёт ~1500, см. arm_test.
    betaflight_max_throttle_us: int = 1550
    betaflight_max_stick_delta: int = 200
    # Фоновый MSP-опрос для /telemetry (ARM, батарея, режим).
    betaflight_telemetry_hz: float = 2.0
    betaflight_battery_cells: int = 6
    # Баро-взлёт/посадка (MSP_ALTITUDE): P-регулятор throttle.
    betaflight_alt_tolerance_m: float = 0.12
    betaflight_alt_ground_m: float = 0.10
    betaflight_alt_hover_us: int = 1410
    betaflight_alt_p_gain: int = 70
    betaflight_alt_max_climb_us: int = 1440
    betaflight_alt_max_descend_us: int = 1280
    # Минимальный газ только когда НИЖЕ цели (не мешает снижаться при перелёте).
    betaflight_alt_hold_min_us: int = 1405
    # Перелёт выше цели: ниже этой высоты разрешаем газ до land_throttle.
    betaflight_alt_overshoot_m: float = 0.20
    # takeoff_alt: сколько секунд держаться в коридоре ±tolerance перед завершением взлёта.
    betaflight_alt_takeoff_stable_s: float = 1.2
    # Посадка (land): дефолты, если в шаге не заданы throttle_us / seconds.
    betaflight_land_seconds: float = 12.0
    betaflight_land_throttle_us: int = 1140
    # Ниже этой высоты (м) — финальная фаза: медленно к land_throttle.
    betaflight_land_final_m: float = 0.18
    # P и slew только для посадки (мягче чем взлёт/hold).
    betaflight_land_p_gain: int = 38
    betaflight_land_slew_us: int = 7
    # Секунд плавно к idle после касания земли (не рубить 1000 мгновенно).
    betaflight_land_touchdown_s: float = 1.2
    # Плавный баро-взлёт: ramp до отрыва, slew газа, стабилизация на высоте.
    betaflight_stick_center_roll_us: int = 1500
    betaflight_stick_center_pitch_us: int = 1500
    betaflight_stick_center_yaw_us: int = 1500
    betaflight_alt_takeoff_settle_s: float = 1.5
    betaflight_alt_throttle_slew_us: int = 14
    betaflight_alt_p_gain_near: int = 55
    betaflight_alt_near_band_m: float = 0.35
    betaflight_alt_liftoff_m: float = 0.06
    betaflight_alt_liftoff_ramp_us: int = 6
    betaflight_alt_baseline_samples: int = 6
    # Баро: опрашивать MSP_ALTITUDE раз в N тиков RC (~25 Гц), чтобы не блокировать SET_RAW_RC.
    betaflight_alt_poll_every_n: int = 3
    # Удержание ARM перед взлётом; Betaflight disarm, если MSP RC пропадает.
    betaflight_arm_hold_s: float = 3.0
    betaflight_arm_switch_us: int = 2000
    # Газ «стоп» для ARM/disarm: rcData[THROTTLE] < mincheck (строго, не 1000 при mincheck=1000).
    betaflight_idle_throttle_us: int = 988
    # Порядок первых 4 каналов MSP SET_RAW_RC — как ``map`` в Betaflight CLI (AETR1234 / AERT1234).
    betaflight_rc_map: str = "AETR"
    betaflight_emergency_land_s: float = 30.0
    # STOP / ошибка: сколько секунд непрерывно слать DISARM по MSP (Betaflight disarm при пропаже RC).
    betaflight_disarm_hold_s: float = 2.5
    # Нет опроса status/heartbeat от UI N сек → авто-DISARM (обрыв Wi‑Fi с ПК).
    betaflight_client_watchdog_enabled: bool = True
    betaflight_client_heartbeat_timeout_s: float = 4.0
    # Обрыв связи UI↔Pi: плавно снизить газ до idle за N с, затем DISARM (не мгновенный сброс).
    betaflight_link_loss_soft_land_enabled: bool = True
    betaflight_link_loss_land_s: float = 15.0
    # Макс. длительность sequence/track (с) с ARM до авто-DISARM. None или ≤0 — выкл.
    betaflight_mission_max_s: float | None = 40.0

    # Vision-tracker + режим follow (localhost:8001 на Pi).
    vision_tracker_url: str = "http://127.0.0.1:8001"
    betaflight_track_target_alt_m: float = 1.0
    betaflight_track_wait_lock_s: float = 90.0
    betaflight_track_kp_stick: int = 100
    betaflight_track_cx_deadband: float = 0.07
    betaflight_track_cy_deadband: float = 0.10
    betaflight_track_use_pitch: bool = False
    betaflight_track_lost_neutral_s: float = 1.5

    # ---- ArduPilot SITL специфика (не применяется для inav) ----------
    # sitl_relax_preflight=True отключает ARMING_CHECK в SITL.
    sitl_relax_preflight: bool = True

    # param2=21196 — force arm для ArduPilot. INAV его не поддерживает.
    sitl_force_arm: bool = True

    # ---- Unity симулятор (unity_sim) ---------------------------------
    # Unity слушает команды на udp://<unity_cmd_host>:<unity_cmd_port>
    unity_cmd_host: str = "127.0.0.1"
    unity_cmd_port: int = 15000
    # Backend слушает телеметрию от Unity на udp://0.0.0.0:<unity_telem_port>
    unity_telem_port: int = 15001
    # Дублировать manual-control как v2 RC (AETR µs) для новой физики Unity
    unity_protocol_v2: bool = True


settings = Settings()
