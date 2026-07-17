from __future__ import annotations

import threading
import time
from dataclasses import dataclass
import math

from pymavlink import mavutil

from app.core.config import settings
from app.schemas.mission import MissionAction
from app.services.ardupilot_mission import (
    ArduMissionPlan,
    build_mission_plan,
    start_auto_mission,
    upload_mission_plan,
)
from app.services.drone_types import DroneCapabilities, SafetyGate, TelemetrySnapshot

# ArduCopter режимы (custom_mode number).
# Полный список: https://ardupilot.org/copter/docs/flight-modes.html
ARDU_MODES: dict[str, int] = {
    "STABILIZE": 0,
    "ALT_HOLD":  2,
    "AUTO":      3,
    "GUIDED":    4,
    "LOITER":    5,
    "RTL":       6,
    "LAND":      9,
    "POSHOLD":   16,
    "BRAKE":     17,
}


@dataclass(frozen=True)
class MavlinkTargets:
    target_system: int
    target_component: int


class DroneControlService:
    """
    Сервис управления ArduCopter через MAVLink.
    Подключение: по умолчанию TCP tcp:127.0.0.1:5760 (ArduCopter SITL).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._conn: mavutil.mavfile | None = None
        self._targets: MavlinkTargets | None = None
        self._gcs_hb_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # GCS heartbeat — ArduPilot также мониторит GCS heartbeat;
    # при его отсутствии срабатывает GCS failsafe (отключён в SITL через FS_GCS_ENABLE=0).
    # Для надёжности шлём 1 Гц.
    # ------------------------------------------------------------------

    def _ensure_gcs_heartbeat_loop(self) -> None:
        with self._lock:
            if self._gcs_hb_thread is not None and self._gcs_hb_thread.is_alive():
                return

            def _worker() -> None:
                while True:
                    time.sleep(1.0)
                    try:
                        with self._lock:
                            c = self._conn
                        if c is not None:
                            self._kick_gcs(c)
                    except Exception:
                        pass

            self._gcs_hb_thread = threading.Thread(target=_worker, name="gcs-heartbeat", daemon=True)
            self._gcs_hb_thread.start()

    def _kick_gcs(self, conn: mavutil.mavfile) -> None:
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

    # ------------------------------------------------------------------
    # Интервалы сообщений телеметрии
    # ------------------------------------------------------------------

    def _request_message_intervals(self, conn: mavutil.mavfile, ts: int, tc: int) -> None:
        def set_interval(msg_id: int, hz: float) -> None:
            conn.mav.command_long_send(
                ts, tc,
                mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                0,
                float(msg_id),
                float(int(1_000_000 / hz)),
                0, 0, 0, 0, 0,
            )
        try:
            set_interval(0,  1.0)   # HEARTBEAT
            set_interval(1,  2.0)   # SYS_STATUS
            set_interval(33, 5.0)   # GLOBAL_POSITION_INT
            set_interval(24, 2.0)   # GPS_RAW_INT
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Снятие preflight-блокировок в SITL
    # ------------------------------------------------------------------

    def _sitl_relax_preflight(self, conn: mavutil.mavfile, ts: int, tc: int) -> None:
        if not settings.sitl_relax_preflight:
            return
        try:
            params: list[tuple[str, float]] = [
                ("ARMING_CHECK", 0.0),   # отключает все проверки перед ARM
                ("FS_THR_ENABLE", 0.0),  # throttle failsafe — не нужен в SITL
                ("FS_GCS_ENABLE", 0.0),  # GCS failsafe — не нужен в SITL
            ]
            for name, val in params:
                pid = name.encode("ascii").ljust(16, b"\x00")[:16]
                conn.mav.param_set_send(
                    ts, tc,
                    pid,
                    val,
                    mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
                )
                time.sleep(0.05)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # ARM / DISARM
    # ------------------------------------------------------------------

    def _arm_cmd(self, conn: mavutil.mavfile, t: MavlinkTargets, arm: bool) -> None:
        p1 = 1.0 if arm else 0.0
        # param2=21196 — force arm/disarm, стандартный MAVLink параметр.
        p2 = 21196.0 if (settings.sitl_force_arm and arm) else 0.0
        conn.mav.command_long_send(
            t.target_system, t.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0, p1, p2, 0, 0, 0, 0, 0,
        )

    # ------------------------------------------------------------------
    # Подключение
    # ------------------------------------------------------------------

    def _parse_csv(self, value: str) -> list[str]:
        return [c.strip() for c in value.split(",") if c.strip()]

    def connect(self) -> None:
        with self._lock:
            if self._conn is not None and self._targets is not None:
                return

            candidates = [settings.mavlink_connection, *self._parse_csv(settings.mavlink_fallbacks)]
            last_error: Exception | None = None

            for conn_str in candidates:
                conn = None
                try:
                    conn = mavutil.mavlink_connection(
                        conn_str,
                        baud=settings.mavlink_baud,
                        source_system=settings.mavlink_system_id,
                        source_component=settings.mavlink_component_id,
                    )
                    # Пробуждаем SITL / даём знать о себе
                    self._kick_gcs(conn)

                    hb = conn.wait_heartbeat(timeout=settings.mavlink_heartbeat_timeout_s)
                    if hb is None:
                        raise TimeoutError(
                            f"Нет heartbeat за {settings.mavlink_heartbeat_timeout_s}s ({conn_str})"
                        )

                    target_system = (
                        settings.mavlink_target_system
                        if settings.mavlink_target_system is not None
                        else int(hb.get_srcSystem())
                    )
                    target_component = (
                        settings.mavlink_target_component
                        if settings.mavlink_target_component is not None
                        else int(hb.get_srcComponent())
                    )

                    self._request_message_intervals(conn, target_system, target_component)
                    self._sitl_relax_preflight(conn, target_system, target_component)

                    self._conn = conn
                    self._targets = MavlinkTargets(
                        target_system=target_system,
                        target_component=target_component,
                    )
                    self._ensure_gcs_heartbeat_loop()
                    conn = None
                    return
                except Exception as e:
                    last_error = e
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass

            assert last_error is not None
            raise RuntimeError(
                f"Не удалось подключиться к ArduPilot MAVLink. "
                f"Пробовали: {candidates}. Последняя ошибка: {last_error}"
            ) from last_error

    def disconnect(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.close()
            finally:
                self._conn = None
                self._targets = None

    def _require(self) -> tuple[mavutil.mavfile, MavlinkTargets]:
        if self._conn is None or self._targets is None:
            self.connect()
        assert self._conn is not None and self._targets is not None
        return self._conn, self._targets

    # ------------------------------------------------------------------
    # Чтение MAVLink (телеметрия)
    # ------------------------------------------------------------------

    def poll_telemetry_message(self, wait_s: float = 1.0):
        with self._lock:
            need_conn = self._conn is None
        if need_conn:
            try:
                self.connect()
            except Exception:
                return None

        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            with self._lock:
                conn = self._conn
            if conn is None:
                break
            try:
                m = conn.recv_match(blocking=False, timeout=0)
                if m is not None and m.get_type() != "BAD_DATA":
                    return m
            except Exception:
                pass
            time.sleep(0.02)
        return None

    def poll_telemetry(self, wait_s: float = 1.0) -> TelemetrySnapshot | None:
        msg = self.poll_telemetry_message(wait_s=wait_s)
        if msg is None:
            return None

        snap = TelemetrySnapshot(source="ardupilot")
        mtype = msg.get_type()
        now = time.monotonic()

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

        if mtype == "GPS_RAW_INT":
            fix = int(getattr(msg, "fix_type", 0))
            sats = int(getattr(msg, "satellites_visible", 0))
            snap.gps_fix = fix
            snap.gps_sats = sats if sats > 0 else None
            snap.status = "connected"
            snap.updated_at_monotonic = now
            return snap

        if mtype == "HEARTBEAT":
            base_mode = getattr(msg, "base_mode", 0)
            snap.armed = bool(base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            custom_mode = getattr(msg, "custom_mode", None)
            if custom_mode is not None:
                mode_by_num = {v: k for k, v in ARDU_MODES.items()}
                snap.mode = mode_by_num.get(int(custom_mode))
            snap.status = "idle"
            snap.updated_at_monotonic = now
            return snap

        return None

    def get_capabilities(self) -> DroneCapabilities:
        mode = (settings.ardupilot_mission_mode or "guided").strip().lower()
        warnings: tuple[str, ...] = (
            "Миссии: GUIDED (пошагово) или AUTO (загрузка waypoints на FC, см. DRONE_ARDUPILOT_MISSION_MODE).",
            "Для goto / маршрута нужен GPS 3D (≥6 спутников).",
            "На реальном FC: DRONE_SITL_FORCE_ARM=false, ARM с пульта или шаг arm в миссии.",
        )
        if mode == "auto":
            warnings = warnings + (
                "Режим auto: wait-паузы в конструкторе не выполняются на FC — используй guided.",
            )
        return DroneCapabilities(
            profile="ardupilot",
            label="ArduPilot / MAVLink",
            supports_missions=True,
            supports_manual_control=True,
            supports_direct_commands=True,
            supports_video=bool(settings.video_stream_url),
            video_url=settings.video_stream_url,
            warnings=warnings,
            safety_gates=(
                SafetyGate("no_props", "Первый тест команд только без пропеллеров", "warning"),
                SafetyGate("manual_first", "Первый реальный вылет только в ручном режиме", "warning"),
                SafetyGate("gps_fix", "Для takeoff/goto нужен GPS-фикс", "error"),
            ),
        )

    def recv_match(self, *, blocking: bool, timeout: float | None = None):
        with self._lock:
            if self._conn is None:
                self.connect()
            conn = self._conn
        assert conn is not None
        return conn.recv_match(blocking=blocking, timeout=timeout)

    # ------------------------------------------------------------------
    # Команды управления
    # ------------------------------------------------------------------

    def arm(self) -> None:
        with self._lock:
            conn, t = self._require()
            self._arm_cmd(conn, t, True)

    def disarm(self) -> None:
        with self._lock:
            conn, t = self._require()
            self._arm_cmd(conn, t, False)

    def _set_mode(self, conn: mavutil.mavfile, t: MavlinkTargets, mode_name: str) -> None:
        """Переключение режима ArduCopter через MAV_CMD_DO_SET_MODE."""
        mode_num = ARDU_MODES.get(mode_name)
        if mode_num is None:
            raise ValueError(f"Неизвестный режим ArduCopter: {mode_name}. Доступны: {sorted(ARDU_MODES)}")
        conn.mav.command_long_send(
            t.target_system, t.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_MODE,
            0,
            float(mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED),
            float(mode_num),
            0, 0, 0, 0, 0,
        )

    def takeoff(self, altitude_m: float, no_gps: bool = False) -> None:
        """
        ArduCopter: GUIDED + MAV_CMD_NAV_TAKEOFF.
        altitude_m — AGL. no_gps игнорируется (нужен GPS для GUIDED).
        """
        if no_gps:
            pass  # ArduPilot GUIDED требует GPS; флаг оставлен для совместимости API.
        alt = float(max(1.0, min(altitude_m, 120.0)))
        with self._lock:
            conn, t = self._require()
            self._set_mode(conn, t, "GUIDED")
            time.sleep(0.3)
            if settings.sitl_force_arm:
                self._arm_cmd(conn, t, True)
                time.sleep(0.5)
            conn.mav.command_long_send(
                t.target_system, t.target_component,
                mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                0,
                0, 0, 0, 0,   # pitch / empty / empty / yaw — не используются для мультикоптера
                0, 0,          # lat/lon — 0 означает «текущее положение»
                alt,           # высота AGL
            )

    def land(self) -> None:
        with self._lock:
            conn, t = self._require()
            conn.mav.command_long_send(
                t.target_system, t.target_component,
                mavutil.mavlink.MAV_CMD_NAV_LAND,
                0, 0, 0, 0, 0, 0, 0, 0,
            )

    def return_home(self) -> None:
        with self._lock:
            conn, t = self._require()
            conn.mav.command_long_send(
                t.target_system, t.target_component,
                mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
                0, 0, 0, 0, 0, 0, 0, 0,
            )

    def goto(self, lat: float, lon: float, alt_agl_m: float) -> None:
        """
        Перелёт к точке в режиме GUIDED.
        lat/lon — градусы; alt_agl_m — высота над домом (AGL).
        ArduPilot GUIDED + MAV_FRAME_GLOBAL_RELATIVE_ALT_INT ждёт AGL.
        """
        with self._lock:
            conn, t = self._require()
            # Убеждаемся, что мы в GUIDED перед goto
            self._set_mode(conn, t, "GUIDED")
            time.sleep(0.1)
            conn.mav.set_position_target_global_int_send(
                int(time.time() * 1e3) & 0xFFFFFFFF,
                t.target_system,
                t.target_component,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                0b0000111111111000,  # только позиция, без velocity/accel/yaw
                int(lat * 1e7),
                int(lon * 1e7),
                float(alt_agl_m),
                0, 0, 0,    # vx, vy, vz
                0, 0, 0,    # afx, afy, afz
                0, 0,       # yaw, yaw_rate
            )

    def manual_control(self, pitch: int, roll: int, thrust: int, yaw: int) -> None:
        """
        MANUAL_CONTROL: pitch/roll/yaw −1000…1000, thrust 0…1000.
        ArduCopter обрабатывает этот пакет в режимах STABILIZE, ALT_HOLD, LOITER и др.
        """
        def _cl(v: int, lo: int, hi: int) -> int:
            return max(lo, min(hi, int(v)))

        p = _cl(pitch,  -1000, 1000)
        r = _cl(roll,   -1000, 1000)
        yv = _cl(yaw,   -1000, 1000)
        z  = _cl(thrust, 0,    1000)
        with self._lock:
            conn, t = self._require()
            conn.mav.manual_control_send(t.target_system, p, r, z, yv, 0)

    def set_home_global(self, lat: float, lon: float, alt_amsl_m: float) -> None:
        """Установка точки дома (AMSL) для RTL и относительных высот."""
        with self._lock:
            conn, t = self._require()
            conn.mav.command_long_send(
                t.target_system, t.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_HOME,
                0,
                1.0,           # param1=1 → использовать переданные координаты
                0, 0, 0,
                float(lat),
                float(lon),
                float(alt_amsl_m),
            )

    def set_flight_mode(self, mode_name: str) -> None:
        """
        Смена режима полёта ArduCopter.
        Допустимые: STABILIZE, ALT_HOLD, LOITER, POSHOLD, GUIDED, LAND, RTL, AUTO.
        """
        allowed = frozenset(ARDU_MODES.keys())
        if mode_name not in allowed:
            raise ValueError(f"Режим должен быть одним из: {sorted(allowed)}")
        with self._lock:
            conn, t = self._require()
            self._set_mode(conn, t, mode_name)

    def upload_and_start_auto_mission(
        self,
        actions: list[MissionAction],
        *,
        arm_first: bool = True,
    ) -> ArduMissionPlan:
        """Загрузить миссию на FC и запустить AUTO (waypoints / takeoff / land)."""
        plan = build_mission_plan(actions)
        with self._lock:
            conn, t = self._require()
            upload_mission_plan(conn, t.target_system, t.target_component, plan)
            start_auto_mission(
                conn,
                t.target_system,
                t.target_component,
                arm_first=arm_first,
            )
        return plan
