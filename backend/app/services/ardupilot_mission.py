"""
Загрузка миссий в ArduCopter (MAVLink MISSION_ITEM_INT) и запуск в режиме AUTO.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from pymavlink import mavutil

from app.schemas.mission import (
    GotoAction,
    LandAction,
    MissionAction,
    ReturnHomeAction,
    TakeoffAction,
    WaitAction,
)

FRAME = mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
MISSION_TYPE = mavutil.mavlink.MAV_MISSION_TYPE_MISSION


@dataclass(frozen=True)
class ArduMissionPlan:
    items: tuple[dict, ...]


def actions_support_auto_upload(actions: list[MissionAction]) -> bool:
    """AUTO на FC: только takeoff / goto / land / rtl (без wait и ручных пауз)."""
    if not actions:
        return False
    for a in actions:
        if isinstance(a, (TakeoffAction, GotoAction, LandAction, ReturnHomeAction)):
            continue
        if a.action in ("arm", "disarm"):
            continue
        if isinstance(a, WaitAction):
            return False
        return False
    return any(isinstance(a, (TakeoffAction, GotoAction)) for a in actions)


def build_mission_plan(actions: list[MissionAction]) -> ArduMissionPlan:
    """Собрать план для ArduPilot AUTO из шагов конструктора / маршрута."""
    items: list[dict] = []
    seq = 0

    def add_item(
        command: int,
        *,
        param1: float = 0.0,
        param2: float = 0.0,
        param3: float = 0.0,
        param4: float = 0.0,
        lat: float = 0.0,
        lon: float = 0.0,
        alt: float = 0.0,
    ) -> None:
        nonlocal seq
        items.append(
            {
                "seq": seq,
                "frame": FRAME,
                "command": command,
                "param1": param1,
                "param2": param2,
                "param3": param3,
                "param4": param4,
                "x": int(lat * 1e7),
                "y": int(lon * 1e7),
                "z": float(alt),
            }
        )
        seq += 1

    for a in actions:
        if isinstance(a, TakeoffAction):
            add_item(
                mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                alt=float(a.alt),
            )
        elif isinstance(a, GotoAction):
            add_item(
                mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                lat=float(a.lat),
                lon=float(a.lon),
                alt=float(a.alt),
            )
        elif isinstance(a, LandAction):
            add_item(mavutil.mavlink.MAV_CMD_NAV_LAND)
        elif isinstance(a, ReturnHomeAction):
            add_item(mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH)

    if not items:
        raise ValueError("Пустой план миссии для ArduPilot AUTO")
    return ArduMissionPlan(items=tuple(items))


def upload_mission_plan(
    conn: mavutil.mavfile,
    target_system: int,
    target_component: int,
    plan: ArduMissionPlan,
    *,
    timeout_s: float = 30.0,
) -> None:
    """Загрузить миссию на FC и дождаться MISSION_ACK."""
    conn.mav.mission_clear_all_send(
        target_system,
        target_component,
        mission_type=MISSION_TYPE,
    )
    _wait_mission_ack(conn, timeout_s=5.0)

    count = len(plan.items)
    conn.mav.mission_count_send(
        target_system,
        target_component,
        count,
        mission_type=MISSION_TYPE,
    )

    sent: set[int] = set()
    deadline = time.monotonic() + timeout_s
    while len(sent) < count and time.monotonic() < deadline:
        msg = conn.recv_match(
            type=["MISSION_REQUEST", "MISSION_REQUEST_INT"],
            blocking=True,
            timeout=1.0,
        )
        if msg is None:
            continue
        req_seq = int(getattr(msg, "seq", getattr(msg, "mission_seq", 0)))
        if req_seq < 0 or req_seq >= count:
            continue
        item = plan.items[req_seq]
        conn.mav.mission_item_int_send(
            target_system,
            target_component,
            req_seq,
            item["frame"],
            item["command"],
            0,
            1,
            float(item["param1"]),
            float(item["param2"]),
            float(item["param3"]),
            float(item["param4"]),
            int(item["x"]),
            int(item["y"]),
            float(item["z"]),
            mission_type=MISSION_TYPE,
        )
        sent.add(req_seq)

    if len(sent) < count:
        raise TimeoutError(f"Не удалось загрузить миссию на ArduPilot ({len(sent)}/{count} пунктов)")

    _wait_mission_ack(conn, timeout_s=5.0)


def _wait_mission_ack(conn: mavutil.mavfile, *, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        msg = conn.recv_match(type="MISSION_ACK", blocking=True, timeout=0.5)
        if msg is None:
            continue
        if int(getattr(msg, "type", -1)) == mavutil.mavlink.MAV_MISSION_ACCEPTED:
            return
        raise RuntimeError(f"ArduPilot отклонил миссию: MISSION_ACK type={getattr(msg, 'type', None)}")
    raise TimeoutError("Timeout: нет MISSION_ACK от ArduPilot")


def start_auto_mission(
    conn: mavutil.mavfile,
    target_system: int,
    target_component: int,
    *,
    arm_first: bool = True,
) -> None:
    """ARM (опц.) и режим AUTO — FC выполняет загруженную миссию."""
    if arm_first:
        conn.mav.command_long_send(
            target_system,
            target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            1.0,
            0.0,
            0,
            0,
            0,
            0,
            0,
        )
        time.sleep(0.5)

    conn.mav.command_long_send(
        target_system,
        target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_MODE,
        0,
        float(mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED),
        float(3),  # AUTO
        0,
        0,
        0,
        0,
        0,
    )
