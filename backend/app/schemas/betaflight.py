from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


BetaflightStepAction = Literal[
    "arm",
    "neutral",
    "throttle",
    "takeoff_alt",
    "hold_alt",
    "forward",
    "back",
    "left",
    "right",
    "yaw_left",
    "yaw_right",
    "land",
    "disarm",
    "wait",
]
 

class BetaflightSequenceStep(BaseModel):
    action: BetaflightStepAction
    seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    throttle_us: int | None = Field(default=None, ge=1000, le=2000)
    stick_delta: int | None = Field(default=None, ge=0, le=500)
    # Для takeoff_alt / hold_alt: целевая высота над baseline (м). Для land — макс. время (с).
    target_alt_m: float | None = Field(default=None, ge=0.0, le=50.0)
    # После takeoff_alt: держать neutral + baro на высоте N сек (стабилизация перед манёврами).
    settle_s: float | None = Field(default=None, ge=0.0, le=30.0)


class BetaflightSequenceStartRequest(BaseModel):
    steps: list[BetaflightSequenceStep] =  Field(min_length=1, max_length=100)
    port: str | None = Field(default=None, min_length=1, max_length=128)
    baud: int | None = Field(default=None, ge=9600, le=1000000)
    hz: float | None = Field(default=None, ge=5.0, le=100.0)
    channels: int | None = Field(default=None, ge=4, le=18)
    arm_channel: int | None = Field(default=None, ge=1, le=18)
    angle_channel: int | None = Field(default=None, ge=1, le=18)
    # Лимит всей миссии (с). None → из settings (25). 0 → без лимита.
    max_mission_s: float | None = Field(default=None, ge=0.0, le=600.0)


class BetaflightEmergencyLandRequest(BaseModel):
    port: str | None = Field(default=None, min_length=1, max_length=128)
    baud: int | None = Field(default=None, ge=9600, le=1000000)
    seconds: float = Field(default=30.0, ge=5.0, le=120.0)
    throttle_us: int | None = Field(default=None, ge=1000, le=2000)


class BetaflightTrackStartRequest(BaseModel):
    port: str | None = Field(default=None, min_length=1, max_length=128)
    baud: int | None = Field(default=None, ge=9600, le=1000000)
    vision_url: str | None = Field(default=None, max_length=256)
    target_alt_m: float = Field(default=1.0, ge=0.3, le=5.0)
    wait_lock_s: float = Field(default=90.0, ge=5.0, le=300.0)
    takeoff_timeout_s: float = Field(default=25.0, ge=5.0, le=120.0)
    land_timeout_s: float = Field(default=30.0, ge=5.0, le=120.0)
    settle_s: float | None = Field(default=2.0, ge=0.0, le=30.0)
    throttle_us: int | None = Field(default=None, ge=1000, le=2000)
    auto_lock: bool = Field(default=False, description="POST /lock перед ожиданием цели")
    max_mission_s: float | None = Field(default=None, ge=0.0, le=600.0)


class BetaflightCheckResponse(BaseModel):
    ok: bool
    detail: str
    port: str
    variant: str | None = None
    version: str | None = None
    status: dict[str, int | str] | None = None


class BetaflightSequenceStatusResponse(BaseModel):
    status: Literal["idle", "running", "completed", "stopped", "error"]
    current_step: int | None = None
    total_steps: int | None = None
    current_action: str | None = None
    elapsed_s: float = 0.0
    error: str | None = None
    port: str | None = None
    current_channels: list[int] | None = None
    current_alt_m: float | None = Field(default=None, description="Текущая относительная высота по барометру, м")
    target_alt_m: float | None = Field(default=None, description="Целевая высота шага, м")
    mission_max_s: float | None = Field(default=None, description="Лимит миссии, с")
    mission_remaining_s: float | None = Field(default=None, description="Осталось до лимита, с")
