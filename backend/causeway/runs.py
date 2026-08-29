"""One investigation at a time, and everything a reader needs to follow it.

The orchestrator is a synchronous generator that spends most of its time
waiting on a replayed HTTP workload. It runs on its own thread here, appending
every event it yields to an append-only buffer. Readers - the SSE endpoint, a
reconnecting browser, a test - take snapshots of that buffer by index.

The buffer is the reason a dropped connection is not a lost investigation: a
client that reconnects says which event it saw last and gets the rest. Nothing
is regenerated and nothing is replayed through the sandbox a second time.

No judgement is made in this module. It stores what the engine emitted, in the
order it was emitted, and hands it out unchanged.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from causeway.orchestrator import investigate

# Appended after the final orchestrator event so a reader always has an
# unambiguous "you have everything" marker, whether the run finished, failed,
# or raised something nobody expected.
END = "end"

STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"


class AlreadyRunning(RuntimeError):
    """A second investigation was requested while one was still in progress."""

    def __init__(self, run_id: str):
        super().__init__("investigation %s is already running" % run_id)
        self.run_id = run_id


@dataclass
class Run:
    id: str
    started_at: float
    state: str = STATE_RUNNING
    error: str = ""
    finished_at: float = 0.0
    events: List[dict] = field(default_factory=list)
    # Set once the terminal event has been appended. A reader that is caught up
    # on a closed run has everything there will ever be, and can stop waiting.
    closed: bool = False

    @property
    def elapsed_s(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 2)

    def summary(self) -> dict:
        return {
            "run_id": self.id,
            "state": self.state,
            "error": self.error,
            "event_count": len(self.events),
            "started_at": round(self.started_at, 3),
            "finished_at": round(self.finished_at, 3) if self.finished_at else None,
            "elapsed_s": self.elapsed_s,
        }


class RunManager:
    """Holds the current investigation. Deliberately single-slot: the sandbox
    is a real process measuring real latency, and two investigations sharing a
    machine would measure each other."""

    def __init__(self, source: Callable = investigate):
        self._source = source
        self._lock = threading.RLock()
        self._run: Optional[Run] = None
        self._thread: Optional[threading.Thread] = None

    # -- state -------------------------------------------------------------
    @property
    def run(self) -> Optional[Run]:
        with self._lock:
            return self._run

    def is_running(self) -> bool:
        with self._lock:
            return self._run is not None and self._run.state == STATE_RUNNING

    def is_closed(self, run_id: str) -> bool:
        with self._lock:
            return (self._run is not None and self._run.id == run_id
                    and self._run.closed)

    def get(self, run_id: str) -> Optional[Run]:
        with self._lock:
            if self._run is not None and self._run.id == run_id:
                return self._run
        return None

    def status(self) -> dict:
        with self._lock:
            if self._run is None:
                return {"state": "idle", "run_id": None, "event_count": 0}
            return self._run.summary()

    def events_from(self, run_id: str, index: int) -> List[dict]:
        """A snapshot of everything after `index`, taken under the lock so a
        reader never sees a half-appended buffer."""
        with self._lock:
            if self._run is None or self._run.id != run_id:
                return []
            if index < 0:
                index = 0
            return list(self._run.events[index:])

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> Run:
        with self._lock:
            if self.is_running():
                raise AlreadyRunning(self._run.id)
            run = Run(id=uuid.uuid4().hex[:12], started_at=time.time())
            self._run = run
            self._thread = threading.Thread(
                target=self._drive, args=(run,), daemon=True,
                name="causeway-investigation-%s" % run.id)
            self._thread.start()
            return run

    def _append(self, run: Run, event: dict) -> None:
        with self._lock:
            run.events.append(event)

    def _drive(self, run: Run) -> None:
        """Consume the orchestrator on this thread. Nothing here inspects an
        event beyond noticing that the engine reported an error."""
        failure = ""
        try:
            for event in self._source():
                self._append(run, event)
                if event.get("type") == "error":
                    failure = event.get("message", "the engine reported an error")
        except BaseException as exc:                  # noqa: BLE001 - never silent
            failure = "%s: %s" % (type(exc).__name__, exc)
            self._append(run, {"type": "error", "message": failure})

        with self._lock:
            run.state = STATE_FAILED if failure else STATE_COMPLETED
            run.error = failure
            run.finished_at = time.time()
            summary = run.summary()
        # appended last, and after the state is settled, so a client that acts
        # on `end` is acting on the final state rather than a stale one
        self._append(run, dict({"type": END}, **summary))
        with self._lock:
            run.closed = True

    def join(self, timeout: float = None) -> bool:
        """Wait for the current investigation. For tests and shutdown."""
        thread = None
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()


# The process-wide manager the API uses.
manager = RunManager()
