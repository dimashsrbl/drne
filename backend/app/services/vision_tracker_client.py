from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class VisionTargetSnapshot:
    cx: float = 0.0
    cy: float = 0.0
    lost: bool = True
    target_locked: bool = False
    camera_status: str = ""
    backend: str = "stub"
    confidence: float = 0.0


def _url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}{path}"


def fetch_target(base_url: str, *, timeout_s: float = 0.5) -> VisionTargetSnapshot | None:
    try:
        with urllib.request.urlopen(_url(base_url, "/target"), timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    return VisionTargetSnapshot(
        cx=float(data.get("cx") or 0.0),
        cy=float(data.get("cy") or 0.0),
        lost=bool(data.get("lost", True)),
        target_locked=bool(data.get("target_locked", False)),
        camera_status=str(data.get("camera_status") or ""),
        backend=str(data.get("backend") or "stub"),
        confidence=float(data.get("confidence") or 0.0),
    )


def vision_health_ok(base_url: str, *, timeout_s: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(_url(base_url, "/health"), timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("status", "")).lower() == "ok"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False


def vision_lock(base_url: str, *, timeout_s: float = 2.0) -> None:
    req = urllib.request.Request(_url(base_url, "/lock"), method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=timeout_s):
        return


def vision_unlock(base_url: str, *, timeout_s: float = 2.0) -> None:
    req = urllib.request.Request(_url(base_url, "/unlock"), method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s):
            return
    except (urllib.error.URLError, TimeoutError, OSError):
        return
