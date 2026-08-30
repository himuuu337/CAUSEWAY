"""The prediction engine: run every detector against a service's recent
telemetry, and apply ONE uniform hysteresis rule so persistence can never
quietly differ between detectors.

Hysteresis, stated exactly: a detector's raw HIGH must appear in
CONFIRM_AFTER consecutive evaluations before `confirmed` becomes true for
it, and a raw LOW must appear in RECOVER_AFTER consecutive evaluations
before a confirmed detector clears. A single spike therefore cannot
confirm anything, and a detector that flickers between HIGH and MEDIUM
never confirms either - only a sustained run of HIGH does. This is the one
piece of engine-owned state; everything else here is a pure function of
the samples passed in.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from typing import Dict, List, Sequence, Tuple

from causeway.prediction import trends
from causeway.prediction.base import Detector
from causeway.prediction.registry import DETECTORS
from causeway.prediction.schema import HIGH, LOW, NoAssessment, RiskAssessment
from causeway.telemetry.store import TelemetryStore

CONFIRM_AFTER = 3
RECOVER_AFTER = 3
_HISTORY_LEN = 6   # comfortably more than max(CONFIRM_AFTER, RECOVER_AFTER)
# Each evaluation looks at only its own trailing window of telemetry, never
# a service's entire lifetime history. A detector computing a trend over
# months of samples - a rise, a recovery, a fresh rise - would see a
# meaningless average of all three; a bounded recent window is what makes
# "is this rising right now" a question with a real answer.
RECENT_WINDOW = 20


@dataclass
class _DetectorState:
    history: List[str] = field(default_factory=list)
    confirmed: bool = False


class PredictionEngine:
    def __init__(self, telemetry_store: TelemetryStore, detectors: Sequence[Detector] = None,
                confirm_after: int = CONFIRM_AFTER, recover_after: int = RECOVER_AFTER,
                recent_window: int = RECENT_WINDOW):
        self._store = telemetry_store
        self._detectors = tuple(detectors) if detectors is not None else DETECTORS
        self._confirm_after = confirm_after
        self._recover_after = recover_after
        self._recent_window = recent_window
        self._lock = threading.Lock()
        self._state: Dict[Tuple[str, str], _DetectorState] = {}

    def evaluate(self, service: str) -> List[RiskAssessment]:
        """Every detector's current assessment for `service`, with
        `confirmed` set by this engine's own hysteresis - never by a
        detector, which has no memory of previous evaluations at all."""
        samples = self._store.recent(service, limit=self._recent_window)
        results: List[RiskAssessment] = []
        with self._lock:
            for detector in self._detectors:
                outcome = detector.evaluate(service, samples)
                if isinstance(outcome, NoAssessment):
                    continue

                key = (service, detector.id)
                state = self._state.setdefault(key, _DetectorState())
                state.history.append(outcome.level)
                state.history = state.history[-_HISTORY_LEN:]

                high_streak = trends.persistence([lvl == HIGH for lvl in state.history])
                low_streak = trends.persistence([lvl == LOW for lvl in state.history])
                if high_streak >= self._confirm_after:
                    state.confirmed = True
                if low_streak >= self._recover_after:
                    state.confirmed = False

                confirmed = state.confirmed and outcome.level == HIGH
                results.append(replace(outcome, confirmed=confirmed))
        return results

    def status(self, service: str) -> dict:
        assessments = self.evaluate(service)
        return {"service": service, "assessments": [a.as_dict() for a in assessments]}

    def reset(self, service: str = None) -> None:
        with self._lock:
            if service is None:
                self._state.clear()
            else:
                self._state = {k: v for k, v in self._state.items() if k[0] != service}


# The process-wide engine the API uses, reading from the process-wide
# telemetry store - the same pairing causeway.runs.manager /
# causeway.stream already establish for investigations.
def _default_engine() -> PredictionEngine:
    from causeway.telemetry.store import store as telemetry_store
    return PredictionEngine(telemetry_store)


engine = _default_engine()
