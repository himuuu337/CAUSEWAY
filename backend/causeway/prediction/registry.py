"""Every detector Causeway ships. Adding one is adding an entry here -
nothing else in causeway.prediction needs to change.

A rate-limit detector (429_rate / rate_limit_remaining) is deliberately not
implemented yet: causeway.telemetry.schema already reserves the fields for
it, but wiring one up without real telemetry to validate it against would
mean inventing the thresholds rather than measuring them. The architecture
is ready; the detector is not invented data.
"""
from __future__ import annotations

from typing import Tuple

from causeway.prediction.base import Detector
from causeway.prediction.connection_pool import ConnectionPoolDetector
from causeway.prediction.latency_degradation import LatencyDegradationDetector
from causeway.prediction.memory_pressure import MemoryPressureDetector

DETECTORS: Tuple[Detector, ...] = (
    ConnectionPoolDetector(),
    MemoryPressureDetector(),
    LatencyDegradationDetector(),
)
