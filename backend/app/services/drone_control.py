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
        self._last_mode: str | None = None
        self._last_armed: bool | None = None
        self._recent_texts: list[str] = []
        self._last_arm_ack: int | None = None
        self._last_base_mode: int | None = None
        self._last_baro: float | None = None
        self._baro_baseline: float | None = None
        self._last_gps_fix: int = 0
        self._last_gps_sats: int = 0
        self._prefer_baro_land: bool = False
        self._baro_hover_us: int | None = None
        self._baro_climb_us: int | None = None

    def _is_fc_heartbeat(self, msg) -> bool:
        """Только HEARTBEAT автопилота (FMU), не GCS и не IO/companion."""
        if msg is None or msg.get_type() != "HEARTBEAT":
            return False
        src = int(msg.get_srcSystem())
        if src == int(settings.mavlink_system_id):
            return False
        if int(getattr(msg, "autopilot", -1)) == int(mavutil.mavlink.MAV_AUTOPILOT_INVALID):
            return False
        mtype = int(getattr(msg, "type", 0))
        if mtype in (
            int(mavutil.mavlink.MAV_TYPE_GCS),
            int(mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER),
            int(mavutil.mavlink.MAV_TYPE_GIMBAL),
            int(mavutil.mavlink.MAV_TYPE_GENERIC),
            int(mavutil.mavlink.MAV_TYPE_ADSB),
            int(mavutil.mavlink.MAV_TYPE_CAMERA),
        ):
            return False
        comp = int(msg.get_srcComponent())
        if self._targets is not None:
            if src != int(self._targets.target_system):
                return False
            want = int(self._targets.target_component) or 1
            if comp != want:
                return False
        elif comp not in (0, 1, int(mavutil.mavlink.MAV_COMP_ID_AUTOPILOT1)):
            return False
        return True

    def _apply_heartbeat(self, msg) -> tuple[str | None, bool | None]:
        if not self._is_fc_heartbeat(msg):
            return self._last_mode, self._last_armed
        base_mode = getattr(msg, "base_mode", 0)
        self._last_base_mode = int(base_mode)
        armed = bool(base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        mode_name: str | None = None
        custom_mode = getattr(msg, "custom_mode", None)
        if custom_mode is not None:
            mode_by_num = {v: k for k, v in ARDU_MODES.items()}
            mode_name = mode_by_num.get(int(custom_mode), f"MODE_{int(custom_mode)}")
        self._last_mode = mode_name
        self._last_armed = armed
        return mode_name, armed

    def _note_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if text not in self._recent_texts:
            self._recent_texts.append(text)
            if len(self._recent_texts) > 12:
                self._recent_texts = self._recent_texts[-12:]

    def _ingest_side_message(self, msg) -> bool:
        """STATUSTEXT / COMMAND_ACK — сохранить и вернуть True если обработано."""
        mtype = msg.get_type()
        if mtype == "STATUSTEXT":
            self._note_text(str(getattr(msg, "text", "") or ""))
            return True
        if mtype == "COMMAND_ACK":
            cmd = int(getattr(msg, "command", 0))
            if cmd == int(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM):
                self._last_arm_ack = int(getattr(msg, "result", -1))
                self._note_text(f"ARM_ACK result={self._last_arm_ack}")
            return True
        return False

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
                        # pyserial/pymavlink не thread-safe: send только под lock.
                        with self._lock:
                            if self._conn is not None:
                                self._kick_gcs(self._conn)
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
            set_interval(0,  2.0)   # HEARTBEAT
            set_interval(1,  2.0)   # SYS_STATUS
            set_interval(33, 5.0)   # GLOBAL_POSITION_INT
            set_interval(24, 2.0)   # GPS_RAW_INT
            set_interval(30, 2.0)   # ATTITUDE
            set_interval(74, 4.0)   # VFR_HUD (баро/скорость)
            set_interval(77, 2.0)   # COMMAND_ACK
            set_interval(29, 4.0)   # SCALED_PRESSURE
            # Fallback для портов, где SET_MESSAGE_INTERVAL игнорируется.
            for stream_id, rate in (
                (mavutil.mavlink.MAV_DATA_STREAM_EXTRA1, 4),
                (mavutil.mavlink.MAV_DATA_STREAM_EXTRA2, 4),
                (mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS, 2),
                (mavutil.mavlink.MAV_DATA_STREAM_POSITION, 5),
                (mavutil.mavlink.MAV_DATA_STREAM_RAW_SENSORS, 2),
            ):
                conn.mav.request_data_stream_send(ts, tc, stream_id, rate, 1)
            # Явно поднять SR2_* (TELEM2), если GCS сидит на SERIAL2.
            for name, val in (
                ("SR2_EXTRA1", 4.0),
                ("SR2_EXTRA2", 4.0),
                ("SR2_EXTRA3", 2.0),
                ("SR2_EXT_STAT", 2.0),
                ("SR2_POSITION", 4.0),
                ("SR2_RAW_SENS", 2.0),
            ):
                pid = name.encode("ascii").ljust(16, b"\x00")[:16]
                conn.mav.param_set_send(
                    ts, tc, pid, val, mavutil.mavlink.MAV_PARAM_TYPE_REAL32
                )
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
                ("ARMING_CHECK", 0.0),
                ("FS_THR_ENABLE", 0.0),
                ("FS_GCS_ENABLE", 0.0),
                ("BRD_SAFETY_DEFLT", 0.0),
                # Без пульта: игнор RC receiver (bit0=1). Иначе RC_OPTIONS=32 ждёт idle throttle.
                ("RC_OPTIONS", 1.0),
                # SD Pixhawk часто забита логами (ENOSPC /APM/LOGS) — не писать на стенде.
                ("LOG_BACKEND_TYPE", 0.0),
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

    def _arm_cmd(
        self,
        conn: mavutil.mavfile,
        t: MavlinkTargets,
        arm: bool,
        *,
        force: bool | None = None,
    ) -> None:
        p1 = 1.0 if arm else 0.0
        use_force = settings.sitl_force_arm if force is None else bool(force)
        # 21196 нужен и на DISARM: иначе GCS-disarm отклоняется, если FC думает «в полёте».
        p2 = 21196.0 if use_force else 0.0
        # Шлём и в autopilot component, и broadcast (comp=0) — Pixhawk иногда глотает один адрес.
        for comp in {int(t.target_component), 0, 1}:
            conn.mav.command_long_send(
                t.target_system, comp,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0, p1, p2, 0, 0, 0, 0, 0,
            )

    def _send_rc_idle(self, conn: mavutil.mavfile, t: MavlinkTargets) -> None:
        """RC override: throttle min, стики центр — для стенда без пульта."""
        try:
            conn.mav.rc_channels_override_send(
                t.target_system,
                t.target_component,
                1500,  # roll
                1500,  # pitch
                1000,  # throttle idle
                1500,  # yaw
                0, 0, 0, 0,
            )
        except Exception:
            pass

    def _drain_locked(self, conn: mavutil.mavfile, seconds: float) -> None:
        """Читать UART под уже взятым lock; обновлять armed/тексты."""
        deadline = time.monotonic() + max(0.05, seconds)
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                # blocking=False на UART часто ничего не читает → теряем HEARTBEAT/ACK.
                msg = conn.recv_match(blocking=True, timeout=min(0.3, remaining))
            except Exception:
                break
            if msg is None or msg.get_type() == "BAD_DATA":
                continue
            if self._ingest_side_message(msg):
                continue
            self._parse_telemetry_message(msg)

    def _wait_armed_state(
        self,
        want: bool,
        *,
        timeout_s: float,
        used_force: bool,
        hold_lock: bool = False,
    ) -> None:
        """Ждём HEARTBEAT с нужным armed; собираем STATUSTEXT при отказе."""
        if self._last_armed is want:
            return
        deadline = time.monotonic() + max(0.5, timeout_s)

        def _loop(conn: mavutil.mavfile | None) -> None:
            while time.monotonic() < deadline:
                if self._last_armed is want:
                    return
                if (
                    want
                    and self._last_arm_ack is not None
                    and self._last_arm_ack != int(mavutil.mavlink.MAV_RESULT_ACCEPTED)
                    and self._last_arm_ack != int(mavutil.mavlink.MAV_RESULT_IN_PROGRESS)
                    and self._last_arm_ack != int(mavutil.mavlink.MAV_RESULT_FAILED)
                ):
                    break
                if hold_lock and conn is not None:
                    self._drain_locked(conn, 0.25)
                else:
                    msg = self.poll_telemetry_message(wait_s=0.35)
                    if msg is None:
                        continue
                    if self._ingest_side_message(msg):
                        continue
                    self._parse_telemetry_message(msg)

        if hold_lock:
            with self._lock:
                conn, _t = self._require()
                _loop(conn)
                if self._last_armed is want:
                    return
        else:
            _loop(None)
            if self._last_armed is want:
                return

        hint = (
            "Для стенда без GPS: Bench ARM (force) или DRONE_SITL_FORCE_ARM=true / ARMING_CHECK=0 в MP."
            if want and not used_force
            else (
                "Смотри текст FC выше. Частые причины: safety switch (нажми или BRD_SAFETY_DEFLT=0 + reboot), "
                "гироскопы (перезапуск FC неподвижно), нет ACK (проверь TELEM2). "
                "В Mission Planner по USB: Action → Arm — там будет PreArm: ..."
            )
            if want
            else "FC не снял ARM — попробуй DISARM ещё раз или перезагрузку FC."
        )
        texts = list(self._recent_texts[-5:])
        detail = f" FC: {'; '.join(texts)}." if texts else " (нет STATUSTEXT/ACK от FC — команда могла не дойти)."
        extra = (
            f" mode={self._last_mode} base_mode={self._last_base_mode} "
            f"arm_ack={self._last_arm_ack}."
        )
        state = "ARMED" if want else "DISARMED"
        raise RuntimeError(f"Timeout: Pixhawk не стал {state}.{detail}{extra} {hint}")

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

                    # Игнорируем собственный GCS-heartbeat (source_system=255 / AUTOPILOT_INVALID).
                    deadline = time.monotonic() + settings.mavlink_heartbeat_timeout_s
                    while int(getattr(hb, "autopilot", -1)) == int(
                        mavutil.mavlink.MAV_AUTOPILOT_INVALID
                    ):
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                f"Нет heartbeat автопилота за {settings.mavlink_heartbeat_timeout_s}s ({conn_str})"
                            )
                        hb = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
                        if hb is None:
                            continue

                    target_system = (
                        settings.mavlink_target_system
                        if settings.mavlink_target_system is not None
                        else int(hb.get_srcSystem())
                    )
                    # FMU = component 1. Не брать IO-копроцессор с Pixhawk 2.4.8.
                    target_component = (
                        settings.mavlink_target_component
                        if settings.mavlink_target_component is not None
                        else 1
                    )

                    # Запомнить mode/armed из уже полученного heartbeat автопилота.
                    self._apply_heartbeat(hb)

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
            remaining = max(0.05, deadline - time.monotonic())
            try:
                with self._lock:
                    conn = self._conn
                    if conn is None:
                        return None
                    m = conn.recv_match(blocking=True, timeout=min(0.3, remaining))
                if m is not None and m.get_type() != "BAD_DATA":
                    self._ingest_side_message(m)
                    return m
            except Exception:
                return None
        return None

    def _parse_telemetry_message(self, msg) -> TelemetrySnapshot | None:
        snap = TelemetrySnapshot(source="ardupilot")
        mtype = msg.get_type()
        now = time.monotonic()

        if mtype == "GLOBAL_POSITION_INT":
            lat = float(msg.lat) / 1e7
            lon = float(msg.lon) / 1e7
            # Без GPS ArduPilot часто шлёт 0/0 — не засоряем UI нулями.
            if abs(lat) > 1e-8 or abs(lon) > 1e-8:
                snap.lat = lat
                snap.lon = lon
            snap.alt = float(msg.relative_alt) / 1000.0
            vx = float(getattr(msg, "vx", 0)) / 100.0
            vy = float(getattr(msg, "vy", 0)) / 100.0
            snap.speed = round(math.sqrt(vx * vx + vy * vy), 2)
            hdg = getattr(msg, "hdg", None)
            if hdg is not None and hdg != 65535:
                snap.heading = float(hdg) / 100.0
            snap.status = "connected"
            snap.updated_at_monotonic = now
            return snap

        if mtype == "SYS_STATUS":
            br = getattr(msg, "battery_remaining", -1)
            if br is not None and br >= 0:
                snap.battery = float(br)
            else:
                mv = getattr(msg, "voltage_battery", None)
                if mv is not None and int(mv) > 5000:
                    # Грубый % для 6S, если FC не отдаёт battery_remaining.
                    volts = float(mv) / 1000.0
                    cell = volts / 6.0
                    snap.battery = max(0.0, min(100.0, (cell - 3.5) / 0.7 * 100.0))
                    snap.note = f"Vbat={volts:.1f}V"
            snap.status = "connected"
            snap.updated_at_monotonic = now
            return snap

        if mtype == "GPS_RAW_INT":
            fix = int(getattr(msg, "fix_type", 0))
            sats = int(getattr(msg, "satellites_visible", 0))
            snap.gps_fix = fix
            snap.gps_sats = sats if sats >= 0 else None
            self._last_gps_fix = fix
            self._last_gps_sats = max(0, sats)
            snap.status = "connected"
            snap.updated_at_monotonic = now
            return snap

        if mtype == "ATTITUDE":
            yaw = getattr(msg, "yaw", None)
            if yaw is not None:
                deg = math.degrees(float(yaw)) % 360.0
                snap.heading = deg
            snap.status = "connected"
            snap.updated_at_monotonic = now
            return snap

        if mtype == "HEARTBEAT":
            if not self._is_fc_heartbeat(msg):
                return None
            mode_name, armed = self._apply_heartbeat(msg)
            snap.armed = armed
            snap.mode = mode_name
            snap.status = "idle"
            snap.updated_at_monotonic = now
            return snap

        if mtype == "VFR_HUD":
            if getattr(msg, "alt", None) is not None:
                baro = float(msg.alt)
                self._last_baro = baro
                snap.baro_alt_m = baro
                if self._baro_baseline is None and self._last_armed is True:
                    self._baro_baseline = baro
                snap.baro_baseline_m = self._baro_baseline
                if self._baro_baseline is not None:
                    snap.alt = baro - self._baro_baseline
                else:
                    snap.alt = baro
            if getattr(msg, "groundspeed", None) is not None:
                snap.speed = float(msg.groundspeed)
            if getattr(msg, "heading", None) is not None:
                snap.heading = float(msg.heading)
            snap.status = "connected"
            snap.updated_at_monotonic = now
            return snap

        if mtype == "SCALED_PRESSURE":
            press = float(getattr(msg, "press_abs", 0) or 0)
            if press > 200:
                # ISA, press_abs в гПа
                baro = 44330.77 * (1.0 - (press / 1013.25) ** 0.190263)
                self._last_baro = baro
                snap.baro_alt_m = baro
                snap.baro_baseline_m = self._baro_baseline
                if self._baro_baseline is not None:
                    snap.alt = baro - self._baro_baseline
            snap.status = "connected"
            snap.updated_at_monotonic = now
            return snap

        return None

    @staticmethod
    def _merge_telemetry(dst: TelemetrySnapshot, src: TelemetrySnapshot) -> TelemetrySnapshot:
        for field_name, value in src.__dict__.items():
            if value is None:
                continue
            if field_name == "status" and value in ("connected", "idle") and dst.status == "flying":
                continue
            setattr(dst, field_name, value)
        return dst

    def poll_telemetry(self, wait_s: float = 1.0) -> TelemetrySnapshot | None:
        """
        Слить несколько MAVLink-сообщений за wait_s и собрать один snapshot.
        Иначе первый STATUSTEXT/ACK обнуляет цикл и mode/gps остаются пустыми.
        """
        deadline = time.monotonic() + wait_s
        merged: TelemetrySnapshot | None = None
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            msg = self.poll_telemetry_message(wait_s=min(0.25, remaining))
            if msg is None:
                if merged is not None:
                    break
                continue
            part = self._parse_telemetry_message(msg)
            if part is None:
                continue
            if merged is None:
                merged = part
            else:
                self._merge_telemetry(merged, part)

        # Mode/armed всегда подмешиваем из последнего heartbeat (в т.ч. с connect).
        if merged is None and (self._last_mode is not None or self._last_armed is not None):
            merged = TelemetrySnapshot(source="ardupilot", status="connected")
        if merged is not None:
            if merged.mode is None and self._last_mode is not None:
                merged.mode = self._last_mode
            if merged.armed is None and self._last_armed is not None:
                merged.armed = self._last_armed
            if merged.source is None:
                merged.source = "ardupilot"
            if merged.updated_at_monotonic is None:
                merged.updated_at_monotonic = time.monotonic()
        return merged

    def link_debug(self, seconds: float = 3.0) -> dict:
        """Диагностика: baud, target, какие типы сообщений приходят."""
        self.connect()
        counts: dict[str, int] = {}
        deadline = time.monotonic() + max(0.5, seconds)
        while time.monotonic() < deadline:
            msg = self.poll_telemetry_message(wait_s=0.2)
            if msg is None:
                continue
            name = msg.get_type()
            counts[name] = counts.get(name, 0) + 1
        snap = self.poll_telemetry(wait_s=0.5)
        with self._lock:
            targets = self._targets
        return {
            "connection": settings.mavlink_connection,
            "baud": settings.mavlink_baud,
            "target_system": targets.target_system if targets else None,
            "target_component": targets.target_component if targets else None,
            "last_mode": self._last_mode,
            "last_armed": self._last_armed,
            "message_counts": counts,
            "telemetry_sample": None if snap is None else {
                "mode": snap.mode,
                "armed": snap.armed,
                "alt": snap.alt,
                "heading": snap.heading,
                "battery": snap.battery,
                "gps_fix": snap.gps_fix,
                "gps_sats": snap.gps_sats,
                "lat": snap.lat,
                "lon": snap.lon,
                "status": snap.status,
                "source": snap.source,
            },
            "seconds": seconds,
        }

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

    def arm(self, force: bool | None = None, timeout_s: float | None = None) -> None:
        use_force = settings.sitl_force_arm if force is None else bool(force)
        wait_s = float(timeout_s if timeout_s is not None else settings.ardupilot_arm_timeout_s)
        self._recent_texts.clear()
        self._last_arm_ack = None
        accepted = int(mavutil.mavlink.MAV_RESULT_ACCEPTED)

        with self._lock:
            conn, t = self._require()
            if settings.sitl_relax_preflight:
                self._sitl_relax_preflight(conn, t.target_system, t.target_component)
            try:
                self._set_mode(conn, t, "STABILIZE")
            except Exception:
                pass
            for _ in range(3):
                self._send_rc_idle(conn, t)
                self._drain_locked(conn, 0.08)
            self._arm_cmd(conn, t, True, force=use_force)
            self._drain_locked(conn, min(wait_s, 8.0))
            if self._last_armed is True or self._last_arm_ack == accepted:
                if self._last_baro is not None:
                    self._baro_baseline = self._last_baro
                return
            if use_force:
                self._arm_cmd(conn, t, True, force=True)
                self._drain_locked(conn, 3.0)
                if self._last_armed is True or self._last_arm_ack == accepted:
                    if self._last_baro is not None:
                        self._baro_baseline = self._last_baro
                    return

        self._wait_armed_state(True, timeout_s=max(3.0, wait_s * 0.4), used_force=use_force, hold_lock=True)

    def disarm(self, timeout_s: float | None = None) -> None:
        wait_s = float(timeout_s if timeout_s is not None else settings.ardupilot_arm_timeout_s)
        self._recent_texts.clear()
        self._last_arm_ack = None
        self._baro_hover_us = None
        self._baro_climb_us = None
        with self._lock:
            conn, t = self._require()
            # Снять RC override / газ, иначе FC считает что летит и обычный DISARM = FAILED.
            try:
                conn.mav.rc_channels_override_send(
                    t.target_system, t.target_component, 0, 0, 0, 0, 0, 0, 0, 0
                )
            except Exception:
                pass
            self._send_rc_idle(conn, t)
            self._drain_locked(conn, 0.3)
            self._arm_cmd(conn, t, False, force=True)
            self._drain_locked(conn, min(wait_s, 5.0))
            if self._last_armed is False:
                return
            self._arm_cmd(conn, t, False, force=True)
            self._drain_locked(conn, 2.0)
            if self._last_armed is False:
                return
        self._wait_armed_state(False, timeout_s=max(3.0, wait_s * 0.4), used_force=True, hold_lock=True)

    def arm_debug(self, force: bool = True, seconds: float = 8.0) -> dict:
        """Диагностика ARM: тексты FC, ACK, mode, armed."""
        err: str | None = None
        try:
            self.arm(force=force, timeout_s=seconds)
        except Exception as e:
            err = str(e)
        with self._lock:
            targets = self._targets
        return {
            "ok": err is None and self._last_armed is True,
            "error": err,
            "armed": self._last_armed,
            "mode": self._last_mode,
            "base_mode": self._last_base_mode,
            "arm_ack": self._last_arm_ack,
            "statustext": list(self._recent_texts),
            "target_system": targets.target_system if targets else None,
            "target_component": targets.target_component if targets else None,
            "force": force,
            "sitl_force_arm": settings.sitl_force_arm,
            "sitl_relax_preflight": settings.sitl_relax_preflight,
        }

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

    def _rc_send(
        self,
        conn: mavutil.mavfile,
        t: MavlinkTargets,
        *,
        pitch: int = 1500,
        roll: int = 1500,
        throttle: int = 1500,
        yaw: int = 1500,
    ) -> None:
        conn.mav.rc_channels_override_send(
            t.target_system,
            t.target_component,
            int(roll),
            int(pitch),
            int(throttle),
            int(yaw),
            0, 0, 0, 0,
        )

    def _agl_m(self) -> float | None:
        if self._last_baro is None:
            return None
        if self._baro_baseline is not None:
            return self._last_baro - self._baro_baseline
        return self._last_baro

    def _gps_fix_ok(self) -> bool:
        min_sats = max(1, int(settings.ardupilot_min_gps_sats))
        return self._last_gps_fix >= 3 and self._last_gps_sats >= min_sats

    def _smooth_throttle_us(self, current: int, target: int, *, max_step: int) -> int:
        step = max(1, int(max_step))
        if target > current:
            return min(target, current + step)
        if target < current:
            return max(target, current - step)
        return current

    def _apply_baro_power(self, hover_us: int | None, climb_us: int | None) -> tuple[int, int]:
        hover = int(hover_us if hover_us is not None else settings.ardupilot_baro_hover_us)
        climb = int(climb_us if climb_us is not None else settings.ardupilot_baro_climb_us)
        hover = max(1200, min(1800, hover))
        climb = max(hover + 20, min(1900, climb))
        self._baro_hover_us = hover
        self._baro_climb_us = climb
        return hover, climb

    def _active_hover_us(self) -> int:
        if self._baro_hover_us is not None:
            return int(self._baro_hover_us)
        return int(settings.ardupilot_baro_hover_us)

    def hold_hover(self, seconds: float = 0.2) -> None:
        """Держать ALT_HOLD: стики центр, газ mid (без пульта override истекает)."""
        deadline = time.monotonic() + max(0.05, seconds)
        hover = self._active_hover_us()
        with self._lock:
            conn, t = self._require()
            while time.monotonic() < deadline:
                self._rc_send(conn, t, throttle=hover)
                self._drain_locked(conn, 0.08)

    def hold_position(self, seconds: float = 0.2) -> None:
        """С GPS — LOITER (точка XY+высота). Без GPS — баро-hover по высоте."""
        if self._gps_fix_ok():
            deadline = time.monotonic() + max(0.05, seconds)
            with self._lock:
                conn, t = self._require()
                try:
                    self._set_mode(conn, t, "LOITER")
                except Exception:
                    pass
                self._drain_locked(conn, 0.12)
                while time.monotonic() < deadline:
                    try:
                        conn.mav.rc_channels_override_send(
                            t.target_system, t.target_component, 0, 0, 0, 0, 0, 0, 0, 0
                        )
                    except Exception:
                        pass
                    self._drain_locked(conn, 0.08)
            return
        self.hold_hover(seconds)

    def nudge(self, direction: str = "forward", seconds: float = 1.5) -> None:
        """Без GPS: лёгкий pitch в ALT_HOLD, высоту держит баро."""
        direction = (direction or "forward").strip().lower()
        pitch = 1380 if direction in ("forward", "fwd", "вперёд", "вперед") else 1620
        hold_s = max(0.2, min(float(seconds), 8.0))
        deadline = time.monotonic() + hold_s
        with self._lock:
            conn, t = self._require()
            try:
                self._set_mode(conn, t, "ALT_HOLD")
            except Exception:
                pass
            while time.monotonic() < deadline:
                self._rc_send(conn, t, pitch=pitch, throttle=self._active_hover_us())
                self._drain_locked(conn, 0.08)
            hover = self._active_hover_us()
            for _ in range(6):
                self._rc_send(conn, t, throttle=hover)
                self._drain_locked(conn, 0.08)

    def takeoff(
        self,
        altitude_m: float,
        no_gps: bool = False,
        hover_us: int | None = None,
        climb_us: int | None = None,
    ) -> None:
        """
        GPS: GUIDED + NAV_TAKEOFF.
        Без GPS: ALT_HOLD + RC throttle, высота по барометру (AGL).
        """
        if no_gps:
            self._prefer_baro_land = True
            self._takeoff_althold(altitude_m, hover_us=hover_us, climb_us=climb_us)
            return
        self._prefer_baro_land = False
        self._baro_hover_us = None
        self._baro_climb_us = None
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
                0, 0, 0, 0,
                0, 0,
                alt,
            )

    def _takeoff_althold(
        self,
        altitude_m: float,
        hover_us: int | None = None,
        climb_us: int | None = None,
    ) -> None:
        target = float(max(0.4, min(altitude_m, 8.0)))
        hover, climb = self._apply_baro_power(hover_us, climb_us)
        p_gain = int(settings.ardupilot_baro_alt_p_gain)
        tolerance = float(settings.ardupilot_baro_alt_tolerance_m)
        stable_s = float(settings.ardupilot_baro_takeoff_stable_s)
        slew = int(settings.ardupilot_baro_alt_slew_us)
        with self._lock:
            conn, t = self._require()
            try:
                self._set_mode(conn, t, "ALT_HOLD")
            except Exception:
                pass
            self._drain_locked(conn, 0.4)
            if self._last_armed is not True:
                self._arm_cmd(conn, t, True, force=True)
                self._drain_locked(conn, 3.0)
            if self._last_baro is not None and self._baro_baseline is None:
                self._baro_baseline = self._last_baro
            throttle = hover
            stable_since: float | None = None
            deadline = time.monotonic() + 25.0
            while time.monotonic() < deadline:
                agl = self._agl_m()
                if agl is not None:
                    err = target - agl
                    if abs(err) <= tolerance:
                        if stable_since is None:
                            stable_since = time.monotonic()
                        elif time.monotonic() - stable_since >= stable_s:
                            break
                    else:
                        stable_since = None
                    desired = int(hover + p_gain * err)
                    desired = max(1200, min(climb, desired))
                else:
                    desired = min(climb, hover + 180)
                throttle = self._smooth_throttle_us(throttle, desired, max_step=slew)
                self._rc_send(conn, t, throttle=throttle)
                self._drain_locked(conn, 0.08)
            settle_deadline = time.monotonic() + max(0.5, stable_s)
            while time.monotonic() < settle_deadline:
                self._rc_send(conn, t, throttle=hover)
                self._drain_locked(conn, 0.08)

    def _land_althold(self) -> None:
        """Без GPS: плавно снизить газ по баро, 5 с на idle, затем DISARM."""
        hover = self._active_hover_us()
        min_throttle = int(settings.ardupilot_baro_land_throttle_min)
        land_s = max(3.0, float(settings.ardupilot_baro_land_seconds))
        idle_hold_s = max(2.0, float(settings.ardupilot_baro_land_idle_hold_s))
        ground_m = float(settings.ardupilot_baro_land_ground_m)
        slew = int(settings.ardupilot_baro_land_slew_us)
        p_gain = int(settings.ardupilot_baro_land_p_gain)
        with self._lock:
            conn, t = self._require()
            try:
                self._set_mode(conn, t, "ALT_HOLD")
            except Exception:
                pass
            self._drain_locked(conn, 0.3)
            start = time.monotonic()
            deadline = start + land_s
            throttle = hover
            while time.monotonic() < deadline:
                elapsed = time.monotonic() - start
                progress = min(1.0, elapsed / land_s)
                time_throttle = int(hover + (min_throttle - hover) * progress)
                agl = self._agl_m()
                if agl is not None:
                    if agl <= ground_m:
                        throttle = min_throttle
                        break
                    p_throttle = int(hover - p_gain * max(0.0, agl - ground_m))
                    p_throttle = max(min_throttle, min(hover, p_throttle))
                    time_throttle = min(time_throttle, p_throttle)
                throttle = self._smooth_throttle_us(throttle, time_throttle, max_step=slew)
                self._rc_send(conn, t, throttle=throttle)
                self._drain_locked(conn, 0.08)
            idle_deadline = time.monotonic() + idle_hold_s
            while time.monotonic() < idle_deadline:
                self._rc_send(conn, t, throttle=min_throttle)
                self._drain_locked(conn, 0.08)
            try:
                conn.mav.rc_channels_override_send(
                    t.target_system, t.target_component, 0, 0, 0, 0, 0, 0, 0, 0
                )
            except Exception:
                pass
            self._send_rc_idle(conn, t)
            self._drain_locked(conn, 0.2)
        self.disarm()

    def land(self, *, no_gps: bool | None = None) -> None:
        use_baro = self._prefer_baro_land if no_gps is None else bool(no_gps)
        if no_gps is None and not use_baro:
            use_baro = not self._gps_fix_ok()
        if use_baro:
            self._land_althold()
            self._prefer_baro_land = False
            return
        with self._lock:
            conn, t = self._require()
            try:
                self._set_mode(conn, t, "LAND")
            except Exception:
                pass
            time.sleep(0.15)
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
