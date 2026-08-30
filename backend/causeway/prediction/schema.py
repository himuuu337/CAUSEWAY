"""RiskAssessment: what a detector reports, and nothing more than it
actually knows.

A detector's job is to say "here is what the numbers are doing, and here is
what that pattern is called" - never "this will definitely happen". `score`
is a deterministic function of the signals a detector actually looked at,
not a calibrated probability, which is why it is called a risk score and
never printed as a percentage chance of failure.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Tuple

LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
LEVELS = (LOW, MEDIUM, HIGH)
_RANK = {LOW: 0, MEDIUM: 1, HIGH: 2}


def more_severe(a: str, b: str) -> str:
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b


@dataclass(frozen=True)
class RiskAssessment:
    service: str
    detector: str                          # e.g. "connection_pool_exhaustion"
    level: str                             # LOW | MEDIUM | HIGH - this evaluation, raw
    score: float                           # 0.0-1.0, deterministic, not a probability
    predicted_failure: str                 # human label, e.g. "connection pool exhaustion"
    evidence: Tuple[str, ...]              # short, human-readable observations
    current_values: Mapping[str, float]
    trends: Mapping[str, float]            # field -> slope (units/second)
    eta_seconds: Optional[float] = None
    sample_count: int = 0
    confirmed: bool = False                # set by the engine's hysteresis, never a detector

    def as_dict(self) -> dict:
        data = asdict(self)
        return data


@dataclass(frozen=True)
class NoAssessment:
    """A detector's honest "not enough signal yet" - distinct from LOW,
    which means "signal present and it looks fine". Never fabricated into
    a RiskAssessment just to have something to show."""
    service: str
    detector: str
    reason: str
