from __future__ import annotations

import json
import math
import socket
import threading
import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.services.drone_types import DroneCapabilities, SafetyGate, TelemetrySnapshot


@dataclass
class _UnityState:
    last_snapshot: TelemetrySnapshot = field(default_factory=lambda: TelemetrySnapshot(source="unity_sim"))
    updated_at: float = 0.0


class UnitySimAdapter:
    """
    Виртуальный "полетник" для Unity.

    Связь по UDP (JSON строками):
    - Команды: backend -> Unity (udp://unity_cmd_host:unity_cmd_port)
    - Телеметрия: Unity -> backend (udp://0.0.0.0:unity_telem_port)

    Формат команд (пример):
      {"type":"manual","pitch":0,"roll":0,"yaw":0,"thrust":500}
      {"type":"arm"} / {"type":"disarm"}
      {"type":"takeoff","alt":2}
      {"type":"land"}
      {"type":"rtl"}
      {"type":"goto","lat":51.1,"lon":71.4,"alt":15}
      {"type":"set_mode","mode":"POSHOLD"}

    Формат телеметрии (пример):
      {
        "type":"telemetry",
        "lat":51.1694,"lon":71.4491,"alt":1.2,
        "roll":0.1,"pitch":-0.2,"yaw":180.0,
        "speed":3.2,
        "armed":true,
        "mode":"SIM"
      }
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cmd_addr = (settings.unity_cmd_host, int(settings.unity_cmd_port))
        self._cmd_sock: socket.socket | None = None
        self._telem_sock: socket.socket | None = None
        self._rx_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state = _UnityState()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        with self._lock:
            if self._cmd_sock is not None and self._telem_sock is not None:
                return

            self._cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._cmd_sock.setblocking(False)

            self._telem_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._telem_sock.bind(("0.0.0.0", int(settings.unity_telem_port)))
            self._telem_sock.settimeout(0.2)

            self._stop.clear()
            self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True, name="unity-sim-rx")
            self._rx_thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        with self._lock:
            for s in (self._cmd_sock, self._telem_sock):
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass
            self._cmd_sock = None
            self._telem_sock = None

    # ------------------------------------------------------------------
    # capabilities / telemetry
    # ------------------------------------------------------------------

    def get_capabilities(self) -> DroneCapabilities:
        return DroneCapabilities(
            profile="unity_sim",
            label="Unity Simulator (UDP)",
            supports_missions=True,
            supports_manual_control=True,
            supports_direct_commands=True,
            supports_video=False,
            warnings=(
                "Unity симулятор: команды идут по UDP (см. DRONE_UNITY_CMD_HOST/PORT и DRONE_UNITY_TELEM_PORT).",
            ),
            safety_gates=(SafetyGate("no_props", "Симулятор — пропеллеров нет", "info"),),
        )

    def poll_telemetry(self, wait_s: float = 1.0) -> TelemetrySnapshot | None:
        # Телеметрия приходит асинхронно в rx thread; здесь просто возвращаем последний снимок
        deadline = time.monotonic() + float(wait_s)
        while time.monotonic() < deadline:
            with self._lock:
                age = time.monotonic() - self._state.updated_at
                snap = TelemetrySnapshot(**self._state.last_snapshot.__dict__)
            if self._state.updated_at > 0 and age < 2.0:
                return snap
            time.sleep(0.05)
        # если ничего не пришло — считаем что нет данных
        return None

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------

    def _send(self, obj: dict) -> None:
        with self._lock:
            if self._cmd_sock is None:
                self.connect()
            assert self._cmd_sock is not None
            payload = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
            self._cmd_sock.sendto(payload, self._cmd_addr)

    def arm(self) -> None:
        self._send({"type": "arm"})

    def disarm(self) -> None:
        self._send({"type": "disarm"})

    def takeoff(self, altitude_m: float, no_gps: bool = False) -> None:
        _ = no_gps
        alt = float(max(0.5, min(float(altitude_m), 120.0)))
        self._send({"type": "takeoff", "alt": alt})

    def land(self) -> None:
        self._send({"type": "land"})

    def return_home(self) -> None:
        self._send({"type": "rtl"})

    def goto(self, lat: float, lon: float, alt_agl_m: float) -> None:
        self._send({"type": "goto", "lat": float(lat), "lon": float(lon), "alt": float(alt_agl_m)})

    def manual_control(self, pitch: int, roll: int, thrust: int, yaw: int) -> None:
        # диапазоны как в UI: pitch/roll/yaw -1000..1000, thrust 0..1000
        self._send({"type": "manual", "pitch": int(pitch), "roll": int(roll), "yaw": int(yaw), "thrust": int(thrust)})

    def set_home_global(self, lat: float, lon: float, alt_amsl_m: float) -> None:
        self._send({"type": "set_home", "lat": float(lat), "lon": float(lon), "alt": float(alt_amsl_m)})

    def set_flight_mode(self, mode_name: str) -> None:
        self._send({"type": "set_mode", "mode": str(mode_name)})

    # ------------------------------------------------------------------
    # rx telemetry
    # ------------------------------------------------------------------

    def _rx_loop(self) -> None:
        assert self._telem_sock is not None
        while not self._stop.is_set():
            try:
                data, _addr = self._telem_sock.recvfrom(64 * 1024)
            except socket.timeout:
                continue
            except Exception:
                time.sleep(0.05)
                continue
            try:
                msg = json.loads(data.decode("utf-8", errors="ignore").strip())
            except Exception:
                continue
            if not isinstance(msg, dict) or msg.get("type") != "telemetry":
                continue

            snap = TelemetrySnapshot(source="unity_sim")
            snap.lat = float(msg.get("lat")) if msg.get("lat") is not None else None
            snap.lon = float(msg.get("lon")) if msg.get("lon") is not None else None
            snap.alt = float(msg.get("alt")) if msg.get("alt") is not None else None
            snap.speed = float(msg.get("speed")) if msg.get("speed") is not None else None
            snap.heading = float(msg.get("yaw")) if msg.get("yaw") is not None else None
            snap.armed = bool(msg.get("armed")) if msg.get("armed") is not None else None
            snap.mode = str(msg.get("mode")) if msg.get("mode") is not None else None
            snap.status = "connected"
            snap.updated_at_monotonic = time.monotonic()

            with self._lock:
                self._state.last_snapshot = snap
                self._state.updated_at = time.monotonic()

