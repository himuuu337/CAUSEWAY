"""Live production-monitoring events: telemetry received, risk updated, an
incident opened, an investigation handed off - one ever-running,
append-only, resumable stream, independent of any single investigation.

Reuses causeway.stream's frame format and resume semantics (the SSE
mechanics are the same problem either way) but never terminates on its own
the way an investigation's stream does at its `done`/`end` event: a
monitoring feed has no natural end, only a client that stops listening.
"""
from __future__ import annotations

import asyncio
import threading
import time as _time
from typing import AsyncIterator, Awaitable, Callable, List, Optional

from causeway.stream import HEARTBEAT_SECONDS, POLL_SECONDS, sse_comment, sse_frame

TELEMETRY_RECEIVED = "telemetry_received"
RISK_UPDATED = "risk_updated"
FAILURE_PREDICTED = "failure_predicted"
INCIDENT_CREATED = "incident_created"
INVESTIGATION_HANDOFF = "investigation_handoff"


class MonitorManager:
    """One process-wide, in-memory event log. Never trimmed: a hackathon
    demo process does not run long enough for that to matter, and trimming
    would invalidate the `id:`-as-resume-cursor guarantee every SSE client
    here relies on - the same tradeoff causeway.runs.Run's own event buffer
    already makes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events: List[dict] = []

    def publish(self, event: dict) -> dict:
        with self._lock:
            self._events.append(event)
        return event

    def events_from(self, index: int) -> List[dict]:
        with self._lock:
            if index < 0:
                index = 0
            return list(self._events[index:])

    def latest_index(self) -> int:
        with self._lock:
            return len(self._events)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


manager = MonitorManager()


async def monitor_stream(
    source: MonitorManager,
    start_index: int = 0,
    is_disconnected: Callable[[], Awaitable[bool]] = None,
    sleep: Callable[[float], Awaitable[None]] = None,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    clock: Callable[[], float] = None,
) -> AsyncIterator[str]:
    now = clock or _time.monotonic
    pause = sleep or asyncio.sleep
    disconnected = is_disconnected or (lambda: _never())

    yield "retry: 2000\n\n"
    index = max(0, start_index)
    last_frame = now()

    while True:
        if await disconnected():
            return

        batch = source.events_from(index)
        if batch:
            for event in batch:
                yield sse_frame(index, event)
                index += 1
            last_frame = now()
            continue

        if now() - last_frame >= heartbeat_seconds:
            yield sse_comment()
            last_frame = now()
        await pause(poll_seconds)


async def _never() -> bool:
    return False
