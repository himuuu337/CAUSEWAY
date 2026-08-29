"""The ExperimentSpec a planner returns, and the JSON schema it must satisfy.

An experiment designer - Gemini, or the deterministic fallback - returns one
of these and nothing else. It is given the incident, the candidates, the
available interventions and the available fixtures. It is NOT given any
measurement, and it is called before anything runs.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Mapping, Tuple

# The metric, comparison and reference a plan is allowed to name. A plan may
# describe what it expects; it may not invent a new axis to be judged on.
ALLOWED_METRICS = ("p95_ms",)
ALLOWED_OPS = ("<", "<=")
# Relative to the control measured during the experiment - never an absolute
# millisecond figure, which goes stale the moment the machine changes.
ALLOWED_REFERENCES = ("control",)

# What kind of evidence the candidates in a PlanRequest are. The bundled demo
# hands a planner deploy records; a repository hands it locations in its own
# source. The validator does not care which - it checks identifiers, not
# provenance - but the prompt has to describe the right thing, and a candidate
# that is a place in code has no branch and no line count.
DEPLOY_EVIDENCE = "deploy_records"
CODE_EVIDENCE = "code_locations"

# Words a plan may not use to smuggle a conclusion into the pipeline.
VERDICT_TOKENS = ("proven", "refuted", "unresolved", "supported", "confirmed",
                  "disproved", "root cause is", "is the root cause")

# Keys that would mean the planner is trying to decide rather than propose.
VERDICT_KEYS = ("verdict", "conclusion", "result", "decision", "root_cause",
                "is_cause", "confidence", "probability")

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "hypothesis_id": {"type": "string"},
        "intervention": {
            "type": "object",
            "properties": {"flag": {"type": "string"},
                           "value": {"type": "boolean"}},
            "required": ["flag", "value"],
        },
        "fixture_id": {"type": "string"},
        "expected_signature": {
            "type": "object",
            "properties": {
                "metric": {"type": "string"},
                "op": {"type": "string"},
                "relative_to": {"type": "string"},
                "factor": {"type": "number"},
            },
            "required": ["metric", "op", "relative_to", "factor"],
        },
        "discriminates_between": {"type": "array", "items": {"type": "string"}},
        "reasoning_summary": {"type": "string"},
    },
    "required": ["hypothesis_id", "intervention", "fixture_id",
                 "expected_signature", "discriminates_between",
                 "reasoning_summary"],
}


@dataclass(frozen=True)
class PlanRequest:
    """Everything a planner is allowed to see. Note what is absent: results."""

    incident: Mapping[str, Any]
    candidates: Tuple[Mapping[str, Any], ...]
    intervention_surfaces: Tuple[str, ...]
    incident_state: Mapping[str, bool]
    fixtures: Tuple[str, ...]
    failure_factor: float
    recovery_factor: float
    target_hypothesis: str
    evidence_kind: str = DEPLOY_EVIDENCE

    @property
    def candidate_ids(self) -> Tuple[str, ...]:
        """The identifiers the validator checks a proposal against.

        A deploy record calls it change_id; a code location calls it id. Both
        are opaque strings to everything downstream, which is exactly why the
        validator needed no change when repositories arrived.
        """
        return tuple(str(c.get("id") or c.get("change_id")) for c in self.candidates)

    @property
    def is_code(self) -> bool:
        return self.evidence_kind == CODE_EVIDENCE


@dataclass(frozen=True)
class ExperimentPlan:
    hypothesis_id: str
    intervention: Dict[str, Any]
    fixture_id: str
    expected_signature: Dict[str, Any]
    discriminates_between: Tuple[str, ...]
    # Presentation only. Quoted on screen, never read by the engine.
    reasoning_summary: str

    def as_dict(self) -> dict:
        data = asdict(self)
        data["discriminates_between"] = list(self.discriminates_between)
        return data


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


class ProviderUnavailable(RuntimeError):
    """A planner cannot be used at all: no key, no network, a bad response."""
