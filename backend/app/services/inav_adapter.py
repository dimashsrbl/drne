"""
INAV MAVLink adapter.

Реализует DroneAdapter поверх pymavlink для полётных контроллеров
с прошивкой INAV (iNavFlight). Архитектурно — отдельный профиль,
не затрагивающий ArduPilot/VTOL-ветку (DroneControlService).

Ключевые отличия от ArduPilot:
- Другая таблица custom_mode (см. INAV_MODES).
- takeoff реализован через MAV_CMD_NAV_TAKEOFF (INAV поддерживает).
- Нет force-arm param2=21196 (не работает в INAV).
- goto через MAV_FRAME_GLOBAL_RELATIVE_ALT_INT — работает при включённой навигации.
- supports_missions=False: до интеграции waypoint-апи INAV в бэкенд.

Подключение (пример):
  DRONE_BACKEND_PROFILE=inav
  DRONE_MAVLINK_CONNECTION=COM7          # USB-UART адаптер
  DRONE_MAVLINK_BAUD=115200
  DRONE_MAVLINK_FALLBACKS=              # пустая строка — не использовать fallback

Ручное управление с ПК (геймпад):
  DRONE_INAV_RC_TRANSPORT=mavlink       # MAVLink MANUAL_CONTROL (часто не обрабатывается INAV)
  DRONE_INAV_RC_TRANSPORT=msp         # только MSP SET_RAW_RC по COM (джойстик с ПК)
  DRONE_INAV_RC_TRANSPORT=auto        # сначала MAVLink, при отсутствии heartbeat — MSP
"""
from __future__ import annotations

import math
import threading
import time

import serial
import sys
from pymavlink import mavutil

from app.core.config import settings
from app.services import inav_msp
from app.services.drone_types import DroneCapabilities, SafetyGate, TelemetrySnapshot

# -----------------------------------------------------------------------
# Таблица flight-режимов INAV (MAVLink custom_mode).
# Источник: iNavFlight/inav — src/main/fc/fc_msp.c и MAVLink mapping.
# Значения актуальны для INAV 7+ / 9.x.
# -----------------------------------------------------------------------
INAV_MODES: dict[str, int] = {
    "MANUAL":   0,
    "ANGLE":    1,
    "HORIZON":  2,
    "ALTHOLD":  3,
    "POSHOLD":  5,
    "RTH":      6,
    "WP":       7,
    "LAUNCH":   8,
    "FAILSAFE": 9,
    "ACRO":     12,
    "AIRMODE":  20,
    "LAND":     22,
}

_MODE_BY_NUM: dict[int, str] = {v: k for k, v in INAV_MODES.items()}


class InavMavlinkAdapter:
    """
    Адаптер для INAV через MAVLink.
    Совместим с DroneAdapter protocol (duck typing через Protocol).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._conn: mavutil.mavfile | None = None
        self._msp_serial: serial.Serial | None = None
        self._target_system: int | None = None
        self._target_component: int | None = None
        self._hb_thread: threading.Thread | None = None
        self._msp_keepalive_thread: threading.Thread | None = None
        self._msp_stop = threading.Event()
        self._rc_pitch = 0
        self._rc_roll = 0
        self._rc_thrust = 0
        self._rc_yaw = 0
        self._telemetry_cache = TelemetrySnapshot(source="inav")

    # ------------------------------------------------------------------
    # Подключение
    # ------------------------------------------------------------------

    @staticmethod
    def _serial_device_path() -> str | None:
        """Путь COM/tty для MSP; tcp:/udp: не подходят."""
        s = settings.mavlink_connection.strip()
        if s.upper().startswith("COM"):
            return s
        if s.startswith("/dev/"):
            return s
        return None

    def connect(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            if self._msp_serial is not None:
                return

            tr = (settings.inav_rc_transport or "mavlink").strip().lower()
            if tr not in ("mavlink", "msp", "auto"):
                tr = "mavlink"

            # Защита от "COM4 на Raspberry Pi": COM-порты только на Windows.
            # На Linux должны быть /dev/ttyUSB0, /dev/ttyACM0 или /dev/serial0 (GPIO UART).
            if not sys.platform.startswith("win"):
                s = settings.mavlink_connection.strip()
                if s.upper().startswith("COM"):
                    raise RuntimeError(
                        "[INAV] DRONE_MAVLINK_CONNECTION задан как COM-порт, но это Linux/Raspberry Pi. "
                        "Используй /dev/serial0 (GPIO UART) или /dev/ttyUSB0|/dev/ttyACM0 (USB)."
                    )

            if tr == "msp":
                path = self._serial_device_path()
                if path is None:
                    raise RuntimeError(
                        "[INAV] Режим msp требует serial (например COM4), не tcp:/udp:. "
                        "Укажите DRONE_MAVLINK_CONNECTION=COMn."
                    )
                self._msp_serial = serial.Serial(path, int(settings.mavlink_baud), timeout=0.2)
                self._start_msp_keepalive_loop()
                return

            candidates: list[str] = [settings.mavlink_connection]
            if settings.mavlink_fallbacks.strip():
                candidates += [
                    c.strip()
                    for c in settings.mavlink_fallbacks.split(",")
                    if c.strip()
                ]

            bauds: list[int] = []
            for b in (settings.mavlink_baud, 57600, 115200):
                if b not in bauds:
                    bauds.append(b)

            hb_timeout = min(8.0, float(settings.mavlink_heartbeat_timeout_s))

            last_err: Exception | None = None
            for conn_str in candidates:
                for baud in bauds:
                    c = None
                    try:
                        c = mavutil.mavlink_connection(
                            conn_str,
                            baud=baud,
                            source_system=settings.mavlink_system_id,
                            source_component=settings.mavlink_component_id,
                        )
                        self._send_gcs_heartbeat(c)
                        hb = c.wait_heartbeat(timeout=hb_timeout)
                        if hb is None:
                            raise TimeoutError(f"Нет heartbeat ({conn_str} @ {baud})")

                        self._target_system = (
                            settings.mavlink_target_system
                            if settings.mavlink_target_system is not None
                            else int(hb.get_srcSystem())
                        )
                        self._target_component = (
                            settings.mavlink_target_component
                            if settings.mavlink_target_component is not None
                            else int(hb.get_srcComponent())
                        )
                        self._conn = c
                        self._request_streams(c, self._target_system, self._target_component)
                        self._start_gcs_heartbeat_loop()
                        if tr == "auto":
                            self._start_msp_keepalive_loop()
                        c = None
                        return
                    except Exception as e:
                        last_err = e
                    finally:
                        if c is not None:
                            try:
                                c.close()
                            except Exception:
                                pass

            if tr == "mavlink":
                raise RuntimeError(
                    f"[INAV] Не удалось подключиться (MAVLink). Порты: {candidates}, baud: {bauds}. "
                    f"Последняя ошибка: {last_err}"
                ) from last_err

            path = self._serial_device_path()
            if path is None:
                raise RuntimeError(
                    f"[INAV] MAVLink недоступен ({last_err!s}), а MSP нужен COM/tty — "
                    "задайте DRONE_MAVLINK_CONNECTION=COMn для USB VCP."
                ) from last_err
            try:
                self._msp_serial = serial.Serial(path, int(settings.mavlink_baud), timeout=0.2)
            except Exception as e2:
                raise RuntimeError(
                    f"[INAV] MAVLink и MSP недоступны. MAVLink: {last_err!s}. MSP ({path}): {e2!s}"
                ) from e2

    def disconnect(self) -> None:
        self._msp_stop.set()
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
            if self._msp_serial is not None:
                try:
                    self._msp_serial.close()
                finally:
                    self._msp_serial = None
            self._target_system = None
            self._target_component = None
        self._msp_stop.clear()

    def _msp_port(self) -> serial.Serial | None:
        with self._lock:
            if self._msp_serial is not None:
                return self._msp_serial
            conn = self._conn
        if conn is not None and getattr(conn, "port", None) is not None:
            return conn.port
        return None

    def _send_msp_rc(self) -> None:
        port = self._msp_port()
        if port is None:
            return
        with self._lock:
            p, r, z, yv = self._rc_pitch, self._rc_roll, self._rc_thrust, self._rc_yaw
        nch = max(4, int(settings.inav_msp_rc_channels))
        inav_msp.set_raw_rc(port, p, r, z, yv, num_channels=nch)

    def _start_msp_keepalive_loop(self) -> None:
        """INAV receiver=MSP требует периодический MSP SET_RAW_RC (снимает флаг RX)."""
        if self._msp_keepalive_thread is not None and self._msp_keepalive_thread.is_alive():
            return
        self._msp_stop.clear()

        def _worker() -> None:
            while not self._msp_stop.is_set():
                try:
                    self._send_msp_rc()
                except Exception:
                    pass
                time.sleep(0.1)

        self._msp_keepalive_thread = threading.Thread(
            target=_worker, name="inav-msp-rc", daemon=True
        )
        self._msp_keepalive_thread.start()

    def _require_mavlink(self) -> tuple[mavutil.mavfile, int, int]:
        with self._lock:
            if self._conn is None:
                self.connect()
            if self._msp_serial is not None and self._conn is None:
                raise RuntimeError(
                    "INAV: активен только MSP по USB — MAVLink-команды (ARM, режим, взлёт) недоступны. "
                    "Задайте MAVLink на UART (или serial 20 с маской MAVLink) либо управляйте армингом/режимом с пульта."
                )
            if self._conn is None:
                raise RuntimeError("[INAV] MAVLink не подключён")
        assert self._target_system is not None and self._target_component is not None
        return self._conn, self._target_system, self._target_component

    # ------------------------------------------------------------------
    # GCS heartbeat — INAV мониторит GCS heartbeat для failsafe.
    # ------------------------------------------------------------------

    def _send_gcs_heartbeat(self, conn: mavutil.mavfile) -> None:
        try:
            conn.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_GCS,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                0,
                mavutil.mavlink.MAV_STATE_ACTIVE,
            )
        except Exception:
            pass

    def _start_gcs_heartbeat_loop(self) -> None:
        with self._lock:
            if self._hb_thread and self._hb_thread.is_alive():
                return

            def _worker() -> None:
                while True:
                    time.sleep(1.0)
                    try:
                        with self._lock:
                            c = self._conn
                        if c:
                            self._send_gcs_heartbeat(c)
                    except Exception:
                        pass

            self._hb_thread = threading.Thread(
                target=_worker, name="inav-gcs-hb", daemon=True
            )
            self._hb_thread.start()

    def _request_streams(self, conn: mavutil.mavfile, ts: int, tc: int) -> None:
        def _set(msg_id: int, hz: float) -> None:
            try:
                conn.mav.command_long_send(
                    ts, tc,
                    mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                    0,
                    float(msg_id),
                    float(int(1_000_000 / hz)),
                    0, 0, 0, 0, 0,
                )
            except Exception:
                pass

        _set(0,  4.0)   # HEARTBEAT (INAV часто шлёт редко — поднимаем частоту)
        _set(1,  2.0)   # SYS_STATUS
        _set(33, 5.0)   # GLOBAL_POSITION_INT
        _set(74, 2.0)   # VFR_HUD (скорость, высота)

    # ------------------------------------------------------------------
    # Телеметрия
    # ------------------------------------------------------------------

    def _merge_snapshot(self, target: TelemetrySnapshot, snap: TelemetrySnapshot) -> None:
        for field_name, value in snap.__dict__.items():
            if field_name == "status":
                target.status = value
                continue
            if value is not None or isinstance(value, bool):
                setattr(target, field_name, value)

    def poll_telemetry(self, wait_s: float = 1.0) -> TelemetrySnapshot | None:
        with self._lock:
            need_connect = self._conn is None and self._msp_serial is None
        if need_connect:
            try:
                self.connect()
            except Exception:
                return None

        got_any = False
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            with self._lock:
                conn = self._conn
            if conn is None:
                return None
            try:
                m = conn.recv_match(blocking=False, timeout=0)
            except Exception:
                break

            if m is None or m.get_type() == "BAD_DATA":
                time.sleep(0.02)
                continue

            snap = self._parse_message(m)
            if snap is None:
                continue
            got_any = True
            self._merge_snapshot(self._telemetry_cache, snap)
            time.sleep(0.02)

        if got_any or self._telemetry_cache.updated_at_monotonic is not None:
            cached = TelemetrySnapshot(**self._telemetry_cache.__dict__)
            return cached
        return None

    def _parse_message(self, msg) -> TelemetrySnapshot | None:
        mtype = msg.get_type()
        now = time.monotonic()
        snap = TelemetrySnapshot(source="inav")

        if mtype == "GLOBAL_POSITION_INT":
            snap.lat = float(msg.lat) / 1e7
            snap.lon = float(msg.lon) / 1e7
            snap.alt = float(msg.relative_alt) / 1000.0
            vx = float(getattr(msg, "vx", 0)) / 100.0
            vy = float(getattr(msg, "vy", 0)) / 100.0
            snap.speed = round(math.sqrt(vx * vx + vy * vy), 2)
            hdg = getattr(msg, "hdg", None)
            if hdg is not None and hdg != 65535:
                snap.heading = float(hdg) / 100.0
            snap.status = "flying"
            snap.updated_at_monotonic = now
            return snap

        if mtype == "SYS_STATUS":
            br = getattr(msg, "battery_remaining", -1)
            if br is not None and br >= 0:
                snap.battery = float(br)
            snap.status = "connected"
            snap.updated_at_monotonic = now
            return snap

        if mtype == "HEARTBEAT":
            base_mode = getattr(msg, "base_mode", 0)
            snap.armed = bool(base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            custom_mode = getattr(msg, "custom_mode", None)
            if custom_mode is not None:
                snap.mode = _MODE_BY_NUM.get(int(custom_mode), f"MODE_{int(custom_mode)}")
            snap.status = "idle" if not snap.armed else "flying"
            snap.updated_at_monotonic = now
            return snap 

        if mtype == "VFR_HUD":
            snap.speed = round(float(getattr(msg, "groundspeed", 0)), 2)
            snap.alt = round(float(getattr(msg, "alt", 0)), 2)
            snap.heading = round(float(getattr(msg, "heading", 0)), 1)
            snap.status = "connected"
            snap.updated_at_monotonic = now
            return snap

        return None

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def get_capabilities(self) -> DroneCapabilities:
        tr = (settings.inav_rc_transport or "mavlink").strip().lower()
        extra: tuple[str, ...] = ()
        if tr == "msp":
            extra = (
                "RC с ПК: MSP SET_RAW_RC по USB. ARM/режимы — с пульта или включите MAVLink на отдельном UART.",
            )
        elif tr == "auto":
            extra = (
                "RC: при отсутствии MAVLink heartbeat используется MSP SET_RAW_RC по COM (см. DRONE_MAVLINK_CONNECTION).",
            )
        return DroneCapabilities(
            profile="inav",
            label="INAV / MAVLink (BOB57)",
            supports_missions=True,
            supports_manual_control=True,
            supports_direct_commands=(tr != "msp"),
            supports_video=bool(settings.video_stream_url),
            video_url=settings.video_stream_url,
            warnings=(
                "Миссии (goto/takeoff/RTL) требуют GPS-фикс (≥6 спутников) и включённой навигации (feature GPS в INAV).",
                "Takeoff: по умолчанию POSHOLD + NAV_TAKEOFF (нужен GPS). С no_gps=true — ALT_HOLD + NAV_TAKEOFF (баро, без GPS).",
            )
            + extra,
            safety_gates=(
                SafetyGate("no_props", "Первый тест команд только без пропеллеров", "warning"),
                SafetyGate("manual_first", "Первый вылет — ручной режим ANGLE с пульта", "warning"),
                SafetyGate("gps_fix", "Для takeoff/RTH/goto нужен GPS-фикс (≥6 спутников)", "error"),
            ),
        )

    # ------------------------------------------------------------------
    # ARM / DISARM
    # ------------------------------------------------------------------

    def arm(self) -> None:
        with self._lock:
            conn, ts, tc = self._require_mavlink()
            conn.mav.command_long_send(
                ts, tc,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0,
                1.0,  # arm
                0.0,  # INAV не поддерживает force-arm param2=21196
                0, 0, 0, 0, 0,
            )

    def disarm(self) -> None:
        with self._lock:
            conn, ts, tc = self._require_mavlink()
            conn.mav.command_long_send(
                ts, tc,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0,
                0.0,  # disarm
                0.0,
                0, 0, 0, 0, 0,
            )

    # ------------------------------------------------------------------
    # Режимы
    # ------------------------------------------------------------------

    def _set_mode(self, conn: mavutil.mavfile, ts: int, tc: int, mode_name: str) -> None:
        mode_num = INAV_MODES.get(mode_name)
        if mode_num is None:
            raise ValueError(
                f"[INAV] Неизвестный режим: {mode_name}. Доступны: {sorted(INAV_MODES)}"
            )
        conn.mav.command_long_send(
            ts, tc,
            mavutil.mavlink.MAV_CMD_DO_SET_MODE,
            0,
            float(mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED),
            float(mode_num),
            0, 0, 0, 0, 0,
        )

    def set_flight_mode(self, mode_name: str) -> None:
        with self._lock:
            conn, ts, tc = self._require_mavlink()
            self._set_mode(conn, ts, tc, mode_name)

    # ------------------------------------------------------------------
    # Команды полёта
    # ------------------------------------------------------------------

    def takeoff(self, altitude_m: float, no_gps: bool = False) -> None:
        """
        Взлёт через MAV_CMD_NAV_TAKEOFF.

        По умолчанию: POSHOLD + takeoff — ожидается GPS-фикс (как раньше).

        ``no_gps=True``: ALT_HOLD + takeoff — для теста **без GPS**, только барометр.
        Работоспособность зависит от прошивки/настроек INAV; первый раз только без пропов
        или с минимальной высотой и страховкой с пульта.
        """
        alt = float(max(1.0, min(altitude_m, 120.0)))
        with self._lock:
            conn, ts, tc = self._require_mavlink()
            mode = "ALTHOLD" if no_gps else "POSHOLD"
            self._set_mode(conn, ts, tc, mode)
            time.sleep(0.3)
            conn.mav.command_long_send(
                ts, tc,
                mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                0,
                0, 0, 0, 0,
                0, 0,
                alt,
            )

    def land(self) -> None:
        with self._lock:
            conn, ts, tc = self._require_mavlink()
            conn.mav.command_long_send(
                ts, tc,
                mavutil.mavlink.MAV_CMD_NAV_LAND,
                0, 0, 0, 0, 0, 0, 0, 0,
            )

    def return_home(self) -> None:
        with self._lock:
            conn, ts, tc = self._require_mavlink()
            conn.mav.command_long_send(
                ts, tc,
                mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
                0, 0, 0, 0, 0, 0, 0, 0,
            )

    def goto(self, lat: float, lon: float, alt_agl_m: float) -> None:
        """
        Перелёт к точке. Требует GPS-фикс и режим WP/POSHOLD в INAV.
        """
        with self._lock:
            conn, ts, tc = self._require_mavlink()
            self._set_mode(conn, ts, tc, "POSHOLD")
            time.sleep(0.1)
            conn.mav.set_position_target_global_int_send(
                int(time.time() * 1e3) & 0xFFFFFFFF,
                ts, tc,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                0b0000111111111000,
                int(lat * 1e7),
                int(lon * 1e7),
                float(alt_agl_m),
                0, 0, 0,
                0, 0, 0,
                0, 0,
            )

    def manual_control(self, pitch: int, roll: int, thrust: int, yaw: int) -> None:
        def _cl(v: int, lo: int, hi: int) -> int:
            return max(lo, min(hi, int(v)))

        p  = _cl(pitch,  -1000, 1000)
        r  = _cl(roll,   -1000, 1000)
        yv = _cl(yaw,    -1000, 1000)
        z  = _cl(thrust,  0,    1000)
        with self._lock:
            if self._conn is None and self._msp_serial is None:
                self.connect()
            self._rc_pitch, self._rc_roll, self._rc_thrust, self._rc_yaw = p, r, z, yv
            tr = (settings.inav_rc_transport or "mavlink").strip().lower()
            msp_only = self._msp_serial is not None and self._conn is None

        port = self._msp_port()
        if port is not None and tr in ("auto", "msp"):
            nch = max(4, int(settings.inav_msp_rc_channels))
            inav_msp.set_raw_rc(port, p, r, z, yv, num_channels=nch)
            return
        if msp_only:
            raise RuntimeError("[INAV] MSP serial без MAVLink — команды недоступны.")
        with self._lock:
            if self._conn is None or self._target_system is None:
                raise RuntimeError(
                    "[INAV] Нет MAVLink для MANUAL_CONTROL. Задайте DRONE_INAV_RC_TRANSPORT=msp или auto."
                )
            ts = int(self._target_system)
            self._conn.mav.manual_control_send(ts, p, r, z, yv, 0)

    def set_home_global(self, lat: float, lon: float, alt_amsl_m: float) -> None:
        with self._lock:
            conn, ts, tc = self._require_mavlink()
            conn.mav.command_long_send(
                ts, tc,
                mavutil.mavlink.MAV_CMD_DO_SET_HOME,
                0,
                1.0,
                0, 0, 0,
                float(lat),
                float(lon),
                float(alt_amsl_m),
            )
