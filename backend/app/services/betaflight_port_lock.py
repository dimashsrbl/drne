from __future__ import annotations

import threading

_port_lock = threading.RLock()


class PortBusyError(RuntimeError):
    pass


class betaflight_port_lock:
    def __init__(self, timeout: float | None = None) -> None:
        self._timeout = timeout

    def __enter__(self) -> betaflight_port_lock:
        if self._timeout is None:
            _port_lock.acquire()
            return self
        if not _port_lock.acquire(timeout=self._timeout):
            raise PortBusyError("Порт Betaflight занят (sequence или другой MSP-запрос)")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _port_lock.release()
