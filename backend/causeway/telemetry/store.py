"""A bounded rolling history of telemetry samples, per service.

No database: a hackathon MVP keeps this in memory, one deque per service,
capped so a service that never stops sending cannot grow this without
bound. One lock guards every mutation, which is enough for FastAPI's
threaded request handling without reaching for anything heavier.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Dict, List, Optional

from causeway.telemetry.schema import TelemetrySample

MAX_SAMPLES_PER_SERVICE = 240   # ~ a healthy window of history at 1 sample/2s


class TelemetryStore:
    def __init__(self, max_samples: int = MAX_SAMPLES_PER_SERVICE):
        self._max_samples = max_samples
        self._lock = threading.Lock()
        self._services: Dict[str, "deque[TelemetrySample]"] = {}

    def append(self, sample: TelemetrySample) -> None:
        with self._lock:
            bucket = self._services.setdefault(
                sample.service, deque(maxlen=self._max_samples))
            # Out-of-order or duplicate-timestamp samples are still kept -
            # a detector's own trend math is what has to tolerate that,
            # not the store silently reordering or dropping data an
            # exporter actually sent.
            bucket.append(sample)

    def latest(self, service: str) -> Optional[TelemetrySample]:
        with self._lock:
            bucket = self._services.get(service)
            return bucket[-1] if bucket else None

    def recent(self, service: str, limit: int = None) -> List[TelemetrySample]:
        with self._lock:
            bucket = self._services.get(service)
            if not bucket:
                return []
            items = list(bucket)
        return items[-limit:] if limit else items

    def services(self) -> List[str]:
        with self._lock:
            return sorted(self._services)

    def sample_count(self, service: str) -> int:
        with self._lock:
            bucket = self._services.get(service)
            return len(bucket) if bucket else 0

    def reset(self, service: str = None) -> None:
        """Clear one service's history, or every service's - for demos and
        tests, never called from a telemetry-ingestion code path itself."""
        with self._lock:
            if service is None:
                self._services.clear()
            else:
                self._services.pop(service, None)


# The process-wide store the API uses. Tests construct their own
# TelemetryStore() instead of touching this one, the same pattern
# causeway.runs.manager already uses for the investigation run state.
store = TelemetryStore()
