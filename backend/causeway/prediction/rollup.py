"""A system-wide risk rollup: the worst state across every service the
telemetry store has samples for, and how many are degrading.

Pure aggregation over RiskAssessments a detector and the hysteresis engine
already produced - nothing here detects anything, scores anything, or
decides confirmation. It exists only to answer "is the system at risk, and
how many services" without asking a browser to compare five numbers itself.

    STABLE            no meaningful degradation detected
    WATCH              a detector's own MEDIUM
    ELEVATED           a detector's own HIGH, not yet confirmed
    HIGH_RISK          a detector's own HIGH, confirmed by sustained evidence
    INSUFFICIENT_DATA  no detector produced an assessment at all

A service in INSUFFICIENT_DATA is not "probably fine" - every detector said
it does not yet have enough signal to say anything, and that is reported as
its own state rather than folded into STABLE.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

from causeway.prediction.schema import HIGH, LOW, MEDIUM, RiskAssessment

STABLE = "STABLE"
WATCH = "WATCH"
ELEVATED = "ELEVATED"
HIGH_RISK = "HIGH_RISK"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

STATES = (STABLE, WATCH, ELEVATED, HIGH_RISK, INSUFFICIENT_DATA)
_RANK = {INSUFFICIENT_DATA: -1, STABLE: 0, WATCH: 1, ELEVATED: 2, HIGH_RISK: 3}

_STATE_FOR_LEVEL = {LOW: STABLE, MEDIUM: WATCH}


def state_for(assessment: RiskAssessment) -> str:
    """One of the five states above, derived only from a level a detector
    already computed and a confirmation the engine's own hysteresis already
    decided - never a new judgement made about this one assessment."""
    if assessment.level in _STATE_FOR_LEVEL:
        return _STATE_FOR_LEVEL[assessment.level]
    return HIGH_RISK if assessment.confirmed else ELEVATED


def worst_state(assessments: Sequence[RiskAssessment]) -> str:
    """The most severe state among `assessments`, or INSUFFICIENT_DATA for
    an empty sequence - a service (or a system) no detector said anything
    about is not the same thing as one every detector called stable."""
    if not assessments:
        return INSUFFICIENT_DATA
    return max((state_for(a) for a in assessments), key=lambda s: _RANK[s])


@dataclass(frozen=True)
class ServiceRisk:
    service: str
    state: str
    score: float                         # 0-100: the worst assessment's own score, scaled
    assessments: Tuple[RiskAssessment, ...]

    def as_dict(self) -> dict:
        return {
            "service": self.service, "state": self.state, "score": self.score,
            "assessments": [a.as_dict() for a in self.assessments],
        }


def service_risk(service: str, assessments: Sequence[RiskAssessment]) -> ServiceRisk:
    score = round(max((a.score for a in assessments), default=0.0) * 100.0, 1)
    return ServiceRisk(service=service, state=worst_state(assessments), score=score,
                       assessments=tuple(assessments))


@dataclass(frozen=True)
class SystemRisk:
    state: str
    score: float                          # 0-100: the worst service's own score
    services: Tuple[ServiceRisk, ...]
    services_degraded: int                # WATCH, ELEVATED or HIGH_RISK - not INSUFFICIENT_DATA

    def as_dict(self) -> dict:
        return {
            "state": self.state, "score": self.score,
            "services_degraded": self.services_degraded,
            "services": [s.as_dict() for s in self.services],
        }


def system_risk(per_service: Dict[str, Sequence[RiskAssessment]]) -> SystemRisk:
    """`per_service` maps every service the telemetry store has samples for
    to whatever its detectors currently report - exactly what
    `causeway.prediction.engine.PredictionEngine.evaluate` returns, called
    once per known service. Deterministic and total: no services at all is
    INSUFFICIENT_DATA at a score of 0, not an error."""
    services = tuple(service_risk(name, assessments)
                     for name, assessments in sorted(per_service.items()))
    degraded = sum(1 for s in services if s.state in (WATCH, ELEVATED, HIGH_RISK))
    overall_state = worst_state([a for s in services for a in s.assessments])
    overall_score = round(max((s.score for s in services), default=0.0), 1)
    return SystemRisk(state=overall_state, score=overall_score,
                      services=services, services_degraded=degraded)
