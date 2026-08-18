from pydantic import BaseModel, Field


class TakeoffRequest(BaseModel):
    altitude: float = Field(gt=0, le=500, description="Целевая высота взлёта (м)")
    no_gps: bool = Field(default=False, description="Баро-взлёт без GPS (ALT_HOLD + RC override)")


class LandRequest(BaseModel):
    no_gps: bool | None = Field(
        default=None,
        description="true=баро-посадка; false=NAV_LAND; null=авто по GPS fix",
    )


class GotoRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt: float = Field(ge=0, le=500, description="Высота над домом AGL (м)")


class CommandResponse(BaseModel):
    ok: bool
    detail: str | None = None


class ManualControlRequest(BaseModel):
    pitch: int = Field(ge=-1000, le=1000, description="тангаж")
    roll: int = Field(ge=-1000, le=1000)
    thrust: int = Field(ge=0, le=1000, description="газ 0…1000")
    yaw: int = Field(ge=-1000, le=1000)


class SetHomeRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt: float = Field(ge=-500, le=9000, description="AMSL, м")


class SetModeRequest(BaseModel):
    mode: str = Field(min_length=2, max_length=32)


class SafetyGateResponse(BaseModel):
    id: str
    title: str
    level: str


class DroneProfileResponse(BaseModel):
    profile: str
    label: str
    supports_missions: bool
    supports_manual_control: bool
    supports_direct_commands: bool
    supports_video: bool
    video_url: str | None = None
    warnings: list[str] = Field(default_factory=list)
    safety_gates: list[SafetyGateResponse] = Field(default_factory=list)


class BridgeTelemetryRequest(BaseModel):
    lat: float | None = None
    lon: float | None = None
    alt: float | None = Field(default=None, description="AGL, м")
    battery: float | None = Field(default=None, ge=0, le=100)
    status: str = Field(default="connected")
    speed: float | None = Field(default=None, ge=0)
    armed: bool | None = None
    mode: str | None = None
    heading: float | None = Field(default=None, ge=0, le=360)
    note: str | None = None
    source: str | None = None


class BridgeCommandResponse(BaseModel):
    id: str
    command: str
    params: dict[str, int | float | str | bool | None]
    created_at: float
    status: str


class BridgeAckRequest(BaseModel):
    id: str = Field(min_length=8)
    status: str = Field(default="acked", min_length=2, max_length=32)
    note: str | None = None

