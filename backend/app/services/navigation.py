from __future__ import annotations

from app.schemas.mission import ArmAction, GotoAction, LandAction, MissionAction, TakeoffAction
from app.schemas.navigation import RouteRequest


class NavigationService:
    def build_route_mission(self, route: RouteRequest) -> list[MissionAction]:
        actions: list[MissionAction] = []

        if route.arm:
            actions.append(ArmAction(action="arm"))

        if route.takeoff_alt is not None:
            actions.append(TakeoffAction(action="takeoff", alt=float(route.takeoff_alt)))

        for wp in route.waypoints:
            actions.append(GotoAction(action="goto", lat=wp.lat, lon=wp.lon, alt=wp.alt))

        if route.land_at_end:
            actions.append(LandAction(action="land"))

        return actions

