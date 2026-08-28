"""The deterministic gate between a proposed experiment and the sandbox.

This is the CODE VALIDATES clause. Every plan - from Gemini or from the
fallback - passes through the same eight checks before anything is allowed to
run. The checks are deterministic, they are named, and the UI shows them,
because "we validate the AI's output" is worth nothing if nobody can see what
was validated.

reasoning_summary is the one field exempt from the verdict-language check. It
is prose for a human reader and the engine never reads it, so a plan that says
"B is clearly the root cause" is accepted and FLAGGED rather than rejected -
and the flag is shown. A test proves prose claiming a result leaves the
computed verdict untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from causeway.planner.schema import (ALLOWED_METRICS, ALLOWED_OPS,
                                     ALLOWED_REFERENCES, PLAN_SCHEMA,
                                     VERDICT_KEYS, VERDICT_TOKENS, Check,
                                     ExperimentPlan, PlanRequest)

FACTOR_TOLERANCE = 1e-9

CHECK_NAMES = (
    "schema",
    "hypothesis_in_candidates",
    "intervention_surface_exists",
    "single_independent_variable",
    "fixture_exists",
    "discriminates_between_two",
    "expected_signature_wellformed",
    "no_encoded_verdict",
)


@dataclass(frozen=True)
class ValidationReport:
    checks: Tuple[Check, ...]
    plan: Any = None
    reasoning_flagged: bool = False

    @property
    def accepted(self) -> bool:
        return all(c.passed for c in self.checks) and self.plan is not None

    @property
    def rejections(self) -> Tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.passed)

    def as_dict(self) -> dict:
        return {
            "checks": [c.as_dict() for c in self.checks],
            "passed": sum(1 for c in self.checks if c.passed),
            "total": len(self.checks),
            "accepted": self.accepted,
            "reasoning_flagged": self.reasoning_flagged,
        }


def _contains_verdict(text) -> bool:
    lowered = str(text).lower()
    return any(token in lowered for token in VERDICT_TOKENS)


def _schema_ok(raw: Mapping) -> Tuple[bool, str]:
    if not isinstance(raw, dict):
        return False, "plan is not an object"
    missing = [k for k in PLAN_SCHEMA["required"] if k not in raw]
    if missing:
        return False, "missing %s" % ", ".join(missing)
    extra = [k for k in raw if k not in PLAN_SCHEMA["properties"]]
    if extra:
        return False, "unexpected field(s): %s" % ", ".join(sorted(extra))
    if not isinstance(raw["intervention"], dict):
        return False, "intervention is not an object"
    intervention_extra = [k for k in raw["intervention"] if k not in ("flag", "value")]
    if intervention_extra:
        return False, ("intervention carries extra key(s): %s"
                       % ", ".join(sorted(intervention_extra)))
    if not isinstance(raw["intervention"].get("value"), bool):
        return False, "intervention.value is not a boolean"
    expected = raw["expected_signature"]
    if not isinstance(expected, dict):
        return False, "expected_signature is not an object"
    allowed = tuple(PLAN_SCHEMA["properties"]["expected_signature"]["properties"])
    signature_extra = [k for k in expected if k not in allowed]
    if signature_extra:
        return False, ("expected_signature carries extra key(s): %s - a plan may "
                       "not pin an absolute value" % ", ".join(sorted(signature_extra)))
    signature_missing = [k for k in allowed if k not in expected]
    if signature_missing:
        return False, "expected_signature is missing %s" % ", ".join(sorted(signature_missing))
    if not isinstance(raw["discriminates_between"], list):
        return False, "discriminates_between is not a list"
    if not isinstance(raw["reasoning_summary"], str):
        return False, "reasoning_summary is not a string"
    return True, "all required fields present, no extras"


def validate(raw: Mapping, request: PlanRequest) -> ValidationReport:
    """Every rule that stands between a proposal and the sandbox."""
    checks = []

    ok, detail = _schema_ok(raw)
    checks.append(Check("schema", ok, detail))
    if not ok:
        return ValidationReport(tuple(checks))

    hypothesis = raw["hypothesis_id"]
    intervention = raw["intervention"]
    flag = intervention.get("flag")

    in_candidates = hypothesis in request.candidate_ids
    checks.append(Check(
        "hypothesis_in_candidates", in_candidates,
        "%s is %sone of the localised candidates (%s)"
        % (hypothesis, "" if in_candidates else "NOT ",
           ", ".join(request.candidate_ids))))

    surface_exists = flag in request.intervention_surfaces
    checks.append(Check(
        "intervention_surface_exists", surface_exists,
        "flag %r is %san available intervention"
        % (flag, "" if surface_exists else "NOT ")))

    # Exactly one independent variable: the plan may only move the flag it is
    # testing, and moving it must change exactly one position of the state.
    proposed = dict(request.incident_state)
    if flag in proposed:
        proposed[flag] = bool(intervention.get("value"))
    changed = [k for k in request.incident_state
               if request.incident_state[k] != proposed.get(k)]
    single = (flag == hypothesis) and len(changed) == 1
    checks.append(Check(
        "single_independent_variable", single,
        ("moves %d flag(s): %s" % (len(changed), ", ".join(changed) or "none"))
        if flag == hypothesis
        else "intervenes on %r while testing %r" % (flag, hypothesis)))

    fixture_ok = raw["fixture_id"] in request.fixtures
    checks.append(Check(
        "fixture_exists", fixture_ok,
        "%r is %sa recorded replay fixture"
        % (raw["fixture_id"], "" if fixture_ok else "NOT ")))

    discriminates = list(raw["discriminates_between"])
    unknown = [d for d in discriminates if d not in request.candidate_ids]
    discriminates_ok = len(set(discriminates)) >= 2 and not unknown
    checks.append(Check(
        "discriminates_between_two", discriminates_ok,
        "names %d candidate(s)%s" % (len(set(discriminates)),
                                     "" if not unknown else
                                     ", unknown: %s" % ", ".join(unknown))))

    signature = raw["expected_signature"]
    factor = signature.get("factor")
    signature_ok = (
        signature.get("metric") in ALLOWED_METRICS
        and signature.get("op") in ALLOWED_OPS
        and signature.get("relative_to") in ALLOWED_REFERENCES
        and isinstance(factor, (int, float)) and not isinstance(factor, bool)
        and abs(float(factor) - request.recovery_factor) <= FACTOR_TOLERANCE
    )
    checks.append(Check(
        "expected_signature_wellformed", signature_ok,
        "metric=%r op=%r relative_to=%r factor=%r (the engine uses %.2fx the "
        "control it measures)"
        % (signature.get("metric"), signature.get("op"),
           signature.get("relative_to"), factor, request.recovery_factor)))

    # A plan may describe an experiment. It may not carry a conclusion in any
    # field the engine reads. reasoning_summary is excluded here because the
    # engine never reads it; it is flagged instead and rendered as a quote.
    structural = [hypothesis, flag, raw["fixture_id"], signature.get("metric"),
                  signature.get("op"), signature.get("relative_to")] + discriminates
    offending_keys = [k for k in raw if k.lower() in VERDICT_KEYS]
    no_verdict = not offending_keys and not any(
        _contains_verdict(v) for v in structural if isinstance(v, str))
    checks.append(Check(
        "no_encoded_verdict", no_verdict,
        "no field the engine reads carries a conclusion" if no_verdict
        else "verdict encoded in %s" % (", ".join(offending_keys) or "a structural field")))

    reasoning_flagged = _contains_verdict(raw["reasoning_summary"])

    plan = None
    if all(c.passed for c in checks):
        plan = ExperimentPlan(
            hypothesis_id=hypothesis,
            intervention=dict(intervention),
            fixture_id=raw["fixture_id"],
            expected_signature=dict(signature),
            discriminates_between=tuple(discriminates),
            reasoning_summary=raw["reasoning_summary"],
        )
    return ValidationReport(tuple(checks), plan, reasoning_flagged)
