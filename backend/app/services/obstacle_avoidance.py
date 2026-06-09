from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from enum import Enum

from app.services.drone_types import DroneAdapter
from app.services.telemetry import TelemetryService
from app.services.vision import VisionService


class Phase(str, Enum):
    IDLE     = "idle"
    FLYING   = "flying"    # летим к цели
    AVOIDING = "avoiding"  # летим к боковой точке
    RESUMED  = "resumed"   # обошли, возвращаемся на курс


@dataclass
class AvoidanceState:
    active: bool = False
    target_lat:  float | None = None
    target_lon:  float | None = None
    target_alt:  float | None = None
    avoid_lat:   float | None = None
    avoid_lon:   float | None = None
    phase: str = Phase.IDLE
    log: list[str] | None = None

    def __post_init__(self) -> None:
        if self.log is None:
            self.log = []


# ── Параметры ────────────────────────────────────────────────────────────────
CHECK_INTERVAL_S   = 0.3   # частота проверки камеры и телеметрии
CLEAR_FRAMES_NEEDED = 3    # сколько подряд "чистых" кадров для сброса AVOIDING
AVOID_DIST_M       = 5.0   # боковое смещение в метрах (минимум 3-5 м для GPS)
ARRIVE_DIST_M      = 2.5   # считаем "прибыли" к обходной точке, если ближе этого
EARTH_R_M          = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками в метрах (формула гаверсинусов)."""
    r = EARTH_R_M
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def offset_meters_to_deg(meters: float, lat: float) -> tuple[float, float]:
    """Перевод смещения в метрах в градусы lat/lon (приближение для малых расстояний)."""
    d_lat = meters / EARTH_R_M * (180.0 / math.pi)
    d_lon = meters / (EARTH_R_M * math.cos(math.radians(lat))) * (180.0 / math.pi)
    return d_lat, d_lon


class ObstacleAvoidanceService:
    """
    Облёт препятствий на основе данных с камеры (VisionService).

    State machine:
      IDLE     → ничего не делаем
      FLYING   → летим к target; если obstacle → вычислить avoid_point → AVOIDING
      AVOIDING → летим к avoid_point; ждём haversine < ARRIVE_DIST_M  ИЛИ  3 чистых кадра
                 → goto(target) → RESUMED
      RESUMED  → летим к target; при новом obstacle → снова AVOIDING
    """

    def __init__(
        self,
        drone: DroneAdapter,
        telemetry: TelemetryService,
        vision: VisionService,
    ) -> None:
        self._drone    = drone
        self._telemetry = telemetry
        self._vision   = vision
        self._lock     = threading.RLock()
        self._state    = AvoidanceState()
        self._thread: threading.Thread | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def get_state(self) -> AvoidanceState:
        with self._lock:
            s = self._state
            return AvoidanceState(
                active=s.active,
                target_lat=s.target_lat,
                target_lon=s.target_lon,
                target_alt=s.target_alt,
                avoid_lat=s.avoid_lat,
                avoid_lon=s.avoid_lon,
                phase=s.phase,
                log=list(s.log[-20:]),
            )

    def start(self, lat: float, lon: float, alt: float) -> None:
        with self._lock:
            if self._state.active:
                raise RuntimeError("Облёт уже активен")
            self._state = AvoidanceState(
                active=True,
                target_lat=lat,
                target_lon=lon,
                target_alt=alt,
                phase=Phase.FLYING,
            )
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="avoidance"
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._state.active = False
            self._state.phase  = Phase.IDLE

    # ── Internal ──────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        with self._lock:
            self._state.log.append(f"{time.strftime('%H:%M:%S')} {msg}")

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self._state.phase = phase

    def _run(self) -> None:
        try:
            with self._lock:
                t_lat = self._state.target_lat
                t_lon = self._state.target_lon
                t_alt = self._state.target_alt

            self._log(f"Старт → цель ({t_lat:.5f}, {t_lon:.5f}, {t_alt}м)")
            self._drone.goto(t_lat, t_lon, t_alt)
            self._set_phase(Phase.FLYING)

            clear_streak = 0
            phase = Phase.FLYING

            while True:
                with self._lock:
                    if not self._state.active:
                        break
                    t_lat = self._state.target_lat
                    t_lon = self._state.target_lon
                    t_alt = self._state.target_alt

                time.sleep(CHECK_INTERVAL_S)

                vis  = self._vision.get_snapshot()
                tele = self._telemetry.get_snapshot()
                obs  = vis.obstacle

                # Камера не работает — летим без проверок
                if not vis.running:
                    continue

                # ── State: FLYING / RESUMED ────────────────────────────────
                if phase in (Phase.FLYING, Phase.RESUMED):
                    if obs.detected:
                        clear_streak = 0
                        cur_lat = tele.lat
                        cur_lon = tele.lon
                        if cur_lat is None or cur_lon is None:
                            continue

                        avoid_lat, avoid_lon = self._calc_avoid_point(
                            cur_lat, cur_lon, t_lat, t_lon,
                            obs.offset_x, t_alt,
                        )
                        direction = "влево" if obs.offset_x > 0 else "вправо"
                        self._log(
                            f"ПРЕПЯТСТВИЕ area={obs.area_ratio:.1%} dx={obs.offset_x:+.2f} "
                            f"→ облёт {direction} ({avoid_lat:.5f}, {avoid_lon:.5f})"
                        )
                        with self._lock:
                            self._state.avoid_lat = avoid_lat
                            self._state.avoid_lon = avoid_lon
                        self._drone.goto(avoid_lat, avoid_lon, t_alt)
                        phase = Phase.AVOIDING
                        self._set_phase(phase)

                # ── State: AVOIDING ────────────────────────────────────────
                elif phase == Phase.AVOIDING:
                    with self._lock:
                        avoid_lat = self._state.avoid_lat
                        avoid_lon = self._state.avoid_lon

                    if obs.detected:
                        clear_streak = 0
                    else:
                        clear_streak += 1

                    # Проверяем дистанцию до обходной точки
                    arrived = False
                    if (
                        avoid_lat is not None and avoid_lon is not None
                        and tele.lat is not None and tele.lon is not None
                    ):
                        dist = haversine_m(tele.lat, tele.lon, avoid_lat, avoid_lon)
                        arrived = dist < ARRIVE_DIST_M

                    # Возобновляем курс если: прибыли к обходной точке ИЛИ 3 чистых кадра
                    if arrived or clear_streak >= CLEAR_FRAMES_NEEDED:
                        reason = "прибыли к обходной точке" if arrived else "путь свободен (3 кадра)"
                        self._log(f"Облёт завершён ({reason}) → курс к цели")
                        self._drone.goto(t_lat, t_lon, t_alt)
                        clear_streak = 0
                        phase = Phase.RESUMED
                        self._set_phase(phase)

        except Exception as e:
            self._log(f"Ошибка: {e}")
        finally:
            with self._lock:
                self._state.active = False
                self._state.phase  = Phase.IDLE

    @staticmethod
    def _calc_avoid_point(
        cur_lat: float,
        cur_lon: float,
        tgt_lat: float,
        tgt_lon: float,
        offset_x: float,
        ref_lat: float,
    ) -> tuple[float, float]:
        """
        Боковая точка перпендикулярно вектору cur→tgt, смещённая на AVOID_DIST_M метров.
        offset_x > 0 → препятствие правее → уходим влево.
        """
        d_lat = tgt_lat - cur_lat
        d_lon = tgt_lon - cur_lon
        norm = math.sqrt(d_lat ** 2 + d_lon ** 2) or 1e-9

        # Единичный перпендикуляр
        perp_lat = -d_lon / norm
        perp_lon =  d_lat / norm

        # Конвертируем AVOID_DIST_M → градусы
        ddlat, ddlon = offset_meters_to_deg(AVOID_DIST_M, ref_lat)

        sign = 1.0 if offset_x > 0 else -1.0
        avoid_lat = cur_lat + sign * perp_lat * ddlat
        avoid_lon = cur_lon + sign * perp_lon * ddlon

        return avoid_lat, avoid_lon
