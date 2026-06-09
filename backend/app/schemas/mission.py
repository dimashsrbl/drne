from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class ActionBase(BaseModel):
    action: str


class ArmAction(ActionBase):
    action: Literal["arm"]


class DisarmAction(ActionBase):
    action: Literal["disarm"]


class TakeoffAction(ActionBase):
    action: Literal["takeoff"]
    alt: float = Field(gt=0, le=500)
    # INAV: без GPS POSHOLD недоступен — взлёт через ALT_HOLD + NAV_TAKEOFF (нужен барометр).
    no_gps: bool = Field(default=False, description="Тестовый взлёт без GPS (INAV: ALT_HOLD + баро)")


class LandAction(ActionBase):
    action: Literal["land"]


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


MissionAction = Annotated[
    Union[ArmAction, DisarmAction, TakeoffAction, LandAction, ReturnHomeAction, GotoAction, WaitAction],
    Field(discriminator="action"),
]


class MissionRequest(BaseModel):
    mission: list[MissionAction] = Field(min_length=1)


class MissionStatusResponse(BaseModel):
    status: Literal["idle", "running", "completed", "error"]
    current_step: int | None = None
    total_steps: int | None = None
    current_action: str | None = None
    error: str | None = None

