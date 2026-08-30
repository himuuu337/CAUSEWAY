"""The Detector contract every failure-risk detector implements.

A detector reads a service's recent telemetry history and nothing else - no
network call, no model, no file I/O. It returns either a RiskAssessment (it
had an opinion) or a NoAssessment (not enough signal yet, and it says so
rather than guessing). `confirmed` is never set here: that is
causeway.prediction.engine's hysteresis, applied uniformly across every
detector so persistence rules cannot quietly drift apart between them.
"""
from __future__ import annotations

from typing import Sequence, Union

from causeway.prediction.schema import NoAssessment, RiskAssessment
from causeway.telemetry.schema import TelemetrySample

MIN_SAMPLES = 3   # below this, a detector has no business claiming a trend


class Detector:
    id: str = ""
    display_name: str = ""

    def evaluate(self, service: str, samples: Sequence[TelemetrySample]
                ) -> Union[RiskAssessment, NoAssessment]:
        raise NotImplementedError
