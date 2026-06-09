from pydantic import BaseModel, Field


class Waypoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt: float = Field(ge=-1000, le=10000)


class RouteRequest(BaseModel):
    waypoints: list[Waypoint] = Field(min_length=1)
    arm: bool = False
    takeoff_alt: float | None = Field(default=None, gt=0, le=500)
    land_at_end: bool = False

