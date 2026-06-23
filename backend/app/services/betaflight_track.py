from __future__ import annotations

import time
from typing import TYPE_CHECKING

import serial

from app.core.config import settings
from app.schemas.betaflight import BetaflightSequenceStep, BetaflightTrackStartRequest
from app.services.vision_tracker_client import fetch_target, vision_lock, vision_unlock

if TYPE_CHECKING:
    from app.services.betaflight_control import BetaflightRcRunner, BetaflightRunConfig


class BetaflightTrackMission:
    """Захват цели (vision-tracker) → baro takeoff → follow по cx/cy → land + unlock."""

    def __init__(self, runner: BetaflightRcRunner) -> None:
        self._runner = runner

    def run(self, req: BetaflightTrackStartRequest, cfg: BetaflightRunConfig, start_t: float) -> None:
        vt_url = (req.vision_url or settings.vision_tracker_url).rstrip("/")
        target_alt = float(req.target_alt_m or settings.betaflight_track_target_alt_m)
        hover_us = req.throttle_us or settings.betaflight_alt_hover_us
        hover_step = BetaflightSequenceStep(action="hold_alt", seconds=60.0, target_alt_m=target_alt, throttle_us=hover_us)
        land_step = BetaflightSequenceStep(
            action="land",
            seconds=req.land_timeout_s or settings.betaflight_emergency_land_s,
            throttle_us=hover_us,
        )

        ser = self._runner._ser
        if ser is None:
            raise RuntimeError("Serial не открыт")

        try:
            self._wait_target_lock(vt_url, req, start_t, ser, cfg)
            if self._runner._stop_event.is_set() or self._runner._state.status != "running":
                return

            self._runner._set_action("arm")
            self._runner._ensure_armed(ser, cfg, start_t)
            time.sleep(0.35)
            self._runner._capture_alt_baseline(ser, cfg)

            self._runner._set_action("takeoff_alt")
            takeoff_step = BetaflightSequenceStep(
                action="takeoff_alt",
                seconds=req.takeoff_timeout_s,
                target_alt_m=target_alt,
                throttle_us=hover_us,
                settle_s=req.settle_s,
            )
            self._runner._ramp_until_liftoff(ser, cfg, takeoff_step, start_t)
            if self._runner._try_abort(ser, cfg, start_t):
                if not self._runner._soft_land_on_stop:
                    self._emergency_disarm_only(ser, cfg, start_t, vt_url)
                return

            with self._runner._lock:
                self._runner._state.target_alt_m = target_alt
            self._runner._stream_alt_loop(
                ser, cfg, takeoff_step, start_t, target_rel_m=target_alt, done_when_reached=True
            )
            settle_s = takeoff_step.settle_s if takeoff_step.settle_s is not None else settings.betaflight_alt_takeoff_settle_s
            if settle_s > 0 and self._runner._state.status == "running":
                settle_step = BetaflightSequenceStep(
                    action="hold_alt",
                    seconds=settle_s,
                    target_alt_m=target_alt,
                    throttle_us=hover_us,
                )
                self._runner._stream_alt_loop(
                    ser, cfg, settle_step, start_t, target_rel_m=target_alt, done_when_reached=False, hold=True
                )

            if self._runner._try_abort(ser, cfg, start_t):
                if not self._runner._soft_land_on_stop:
                    self._emergency_disarm_only(ser, cfg, start_t, vt_url)
                return

            self._follow_loop(vt_url, ser, cfg, start_t, target_alt, hover_step)
            if self._runner._stop_event.is_set() or self._runner._state.status != "running":
                if not self._runner._soft_land_on_stop:
                    self._emergency_disarm_only(ser, cfg, start_t, vt_url)
            else:
                self._land_disarm_unlock(ser, cfg, start_t, vt_url, land_step)
        finally:
            vision_unlock(vt_url)

    def _wait_target_lock(
        self,
        vt_url: str,
        req: BetaflightTrackStartRequest,
        start_t: float,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
    ) -> None:
        self._runner._set_action("wait_lock")
        if req.auto_lock:
            vision_lock(vt_url)
            time.sleep(0.35)

        deadline = time.monotonic() + max(5.0, req.wait_lock_s)
        disarm = self._runner._channels(cfg, throttle_us=1000, arm_us=1000)
        while time.monotonic() < deadline:
            if self._runner._try_abort(ser, cfg, start_t):
                return
            self._runner._send_channels(ser, disarm)
            snap = fetch_target(vt_url)
            with self._runner._lock:
                self._runner._state.elapsed_s = time.monotonic() - start_t
            if snap and snap.target_locked and not snap.lost:
                return
            if snap and snap.camera_status not in ("", "ok", "init"):
                raise RuntimeError(
                    f"Камера vision-tracker: {snap.camera_status}. "
                    f"Проверь VISION_VIDEO_SOURCE и сервис на :8001."
                )
            time.sleep(0.25)

        raise RuntimeError(
            "Цель не зафиксирована. Открой /tracker/ui, наведи объект в центр и нажми Lock, "
            "или включи auto_lock в запросе."
        )

    def _follow_loop(
        self,
        vt_url: str,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        start_t: float,
        target_alt: float,
        hover_step: BetaflightSequenceStep,
    ) -> None:
        self._runner._set_action("follow")
        interval = 1.0 / max(1.0, cfg.hz)
        throttle = hover_step.throttle_us or settings.betaflight_alt_hover_us
        max_delta = settings.betaflight_max_stick_delta
        kp = settings.betaflight_track_kp_stick
        cx_db = settings.betaflight_track_cx_deadband
        cy_db = settings.betaflight_track_cy_deadband
        lost_since: float | None = None

        while self._runner._state.status == "running":
            if self._runner._try_abort(ser, cfg, start_t):
                break
            snap = fetch_target(vt_url)
            roll = settings.betaflight_stick_center_roll_us
            pitch = settings.betaflight_stick_center_pitch_us
            yaw = settings.betaflight_stick_center_yaw_us

            if snap and snap.target_locked and not snap.lost:
                lost_since = None
                if abs(snap.cx) > cx_db:
                    roll += max(-max_delta, min(max_delta, int(kp * snap.cx)))
                if settings.betaflight_track_use_pitch and abs(snap.cy) > cy_db:
                    pitch += max(-max_delta, min(max_delta, int(-kp * snap.cy)))
            else:
                if lost_since is None:
                    lost_since = time.monotonic()
                elif time.monotonic() - lost_since > settings.betaflight_track_lost_neutral_s:
                    roll = settings.betaflight_stick_center_roll_us
                    pitch = settings.betaflight_stick_center_pitch_us

            channels_hold = self._runner._channels(
                cfg, roll_us=roll, pitch_us=pitch, yaw_us=yaw, throttle_us=throttle, arm_us=self._runner._arm_switch_us()
            )
            rel = self._runner._relative_alt_m(ser, cfg, channels_hold)
            if rel is not None:
                error = target_alt - rel
                target_throttle = self._runner._alt_throttle_us(error, hover_step, hold=True)
                throttle = self._runner._smooth_throttle_us(throttle, target_throttle)

            channels = self._runner._channels(
                cfg, roll_us=roll, pitch_us=pitch, yaw_us=yaw, throttle_us=throttle, arm_us=self._runner._arm_switch_us()
            )
            self._runner._send_channels(ser, channels)
            with self._runner._lock:
                self._runner._state.elapsed_s = time.monotonic() - start_t
                self._runner._state.target_alt_m = target_alt
                if rel is not None:
                    self._runner._state.current_alt_m = round(rel, 2)
            time.sleep(interval)

    def _emergency_disarm_only(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        start_t: float,
        vt_url: str,
    ) -> None:
        self._runner._set_action("disarm")
        self._runner._stream_disarm_hold(ser, cfg, start_t=start_t)
        vision_unlock(vt_url)

    def _land_disarm_unlock(
        self,
        ser: serial.Serial,
        cfg: BetaflightRunConfig,
        start_t: float,
        vt_url: str,
        land_step: BetaflightSequenceStep,
    ) -> None:
        self._runner._set_action("land")
        self._runner._stream_land_alt(ser, cfg, land_step, start_t)
        self._runner._set_action("disarm")
        self._runner._stream_disarm_hold(ser, cfg, seconds=0.8, start_t=start_t)
        vision_unlock(vt_url)
