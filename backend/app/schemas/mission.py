from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class ActionBase(BaseModel):
    action: str


class ArmAction(ActionBase):
    action: Literal["arm"]
    # force=true → MAVLink param2=21196 (игнор prearm). Только стенд без пропеллеров.
    force: bool = Field(default=False)


class DisarmAction(ActionBase):
    action: Literal["disarm"]


class TakeoffAction(ActionBase):
    action: Literal["takeoff"]
    alt: float = Field(gt=0, le=500)
    no_gps: bool = Field(default=False, description="Баро-взлёт без GPS (ALT_HOLD + RC override)")
    # Только no_gps: mid-stick hover и потолок газа при наборе (µs, как в Betaflight).
    hover_us: int | None = Field(default=None, ge=1200, le=1800)
    climb_us: int | None = Field(default=None, ge=1300, le=1900)


class LandAction(ActionBase):
    action: Literal["land"]
    # Баро-посадка ALT_HOLD (газ ↓ → idle 5 с → DISARM). Без GPS NAV_LAND не работает.
    no_gps: bool = Field(default=False)


class ReturnHomeAction(ActionBase):
    action: Literal["return_home", "return-home", "rtl"]

class GotoAction(ActionBase):
    action: Literal["goto"]
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt: float = Field(ge=-1000, le=10000)


class WaitAction(ActionBase):
    action: Literal["wait"]
    seconds: float = Field(gt=0, le=3600)


class NudgeAction(ActionBase):
    action: Literal["nudge"]
    direction: Literal["forward", "back"] = "forward"
    seconds: float = Field(gt=0, le=15)


MissionAction = Annotated[
    Union[ArmAction, DisarmAction, TakeoffAction, LandAction, ReturnHomeAction, GotoAction, WaitAction, NudgeAction],
    Field(discriminator="action"),
]


class MissionRequest(BaseModel):
    mission: list[MissionAction] = Field(min_length=1)


class MissionStopRequest(BaseModel):
    action: Literal["land", "rtl", "disarm"] = "land"


class MissionStatusResponse(BaseModel):
    status: Literal["idle", "running", "completed", "stopped", "error"]
    current_step: int | None = None
    total_steps: int | None = None
    current_action: str | None = None
    error: str | None = None

