"""A bounded connection pool. `acquire()` blocks up to a short timeout and
raises PoolExhausted rather than hanging forever or returning a value a
caller has to remember to check; `release()` gives a slot back. Nothing
here talks to a real database - this is the resource whose bookkeeping
app.py's request handler gets wrong on one path, which is the whole
incident this repository exists to demonstrate.
"""
from __future__ import annotations

import threading


class PoolExhausted(RuntimeError):
    """No connection became available within the timeout."""


class ConnectionPool:
    def __init__(self, capacity: int):
        self._semaphore = threading.Semaphore(capacity)
        self._lock = threading.Lock()
        self._capacity = capacity
        self._used = 0
        self._waiting = 0

    def acquire(self, timeout: float = 2.0) -> None:
        with self._lock:
            self._waiting += 1
        try:
            got = self._semaphore.acquire(timeout=timeout)
        finally:
            with self._lock:
                self._waiting -= 1
        if not got:
            raise PoolExhausted("no connection became available within %.1fs" % timeout)
        with self._lock:
            self._used += 1

    def release(self) -> None:
        with self._lock:
            if self._used <= 0:
                return
            self._used -= 1
        self._semaphore.release()

    def snapshot(self) -> dict:
        with self._lock:
            return {"used": self._used, "capacity": self._capacity, "waiting": self._waiting}
