"""Experiment planning: schema, validator, providers, and the fallback path.

The model proposes. This package decides whether the proposal is allowed to
run. causeway/verdict.py decides what the result means. Those three
responsibilities never merge, and the dependency only points one way: this
package imports causeway.verdict, and causeway.verdict imports nothing from
here. A test walks the import graph and fails the build if that ever inverts.
"""
from __future__ import annotations

from dataclasses import dataclass

from causeway import verdict
from causeway.planner.deterministic import DeterministicPlanner
from causeway.planner.gemini import GeminiPlanner
from causeway.planner.schema import (CODE_EVIDENCE, DEPLOY_EVIDENCE,
                                     ExperimentPlan, PlanRequest,
                                     PLAN_SCHEMA, ProviderUnavailable)
from causeway.planner.validator import ValidationReport, validate


@dataclass(frozen=True)
class PlanOutcome:
    plan: ExperimentPlan
    report: ValidationReport
    # Where the accepted plan actually came from. The UI shows this verbatim.
    source: str
    kind: str                    # "gemini" | "deterministic"
    proposed_by: str = ""        # who was asked first
    fallback_reason: str = ""    # why the first choice was not used

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallback_reason)

    def as_dict(self) -> dict:
        return {
            "plan": self.plan.as_dict(),
            "validation": self.report.as_dict(),
            "provenance": {
                "source": self.source,
                "kind": self.kind,
                "proposed_by": self.proposed_by or self.source,
                "used_fallback": self.used_fallback,
                "fallback_reason": self.fallback_reason,
            },
        }


def build_request(incident, candidates, incident_state, fixtures, target,
                  observational=None):
    """Assemble everything a planner is allowed to see.

    `observational` optionally supplies the correlation-only scores, which are
    legitimate pre-experiment evidence: they say how suspicious a change looks,
    not whether it is causal. Nothing measured during an experiment can enter
    here - PlanRequest has no field that could carry one, and a test asserts
    that the rendered prompt contains no result, ratio or verdict.
    """
    scores = {}
    for assessment in observational or ():
        scores[assessment.change_id] = assessment.score

    return PlanRequest(
        incident=dict(incident),
        candidates=tuple({
            "change_id": c.change_id, "branch": c.branch, "summary": c.summary,
            "lines_changed": c.lines_changed, "files_changed": c.files_changed,
            "observational_score": scores.get(c.change_id),
        } for c in candidates),
        intervention_surfaces=tuple(sorted(incident_state)),
        incident_state=dict(incident_state),
        fixtures=tuple(fixtures),
        failure_factor=verdict.FAILURE_FACTOR,
        recovery_factor=verdict.RECOVERY_FACTOR,
        target_hypothesis=target,
    )


def build_request_for_code(incident, hypotheses, state, workloads, target):
    """Assemble everything a planner may see about a REPOSITORY investigation.

    The candidates here are places in the repository's own source, found by
    causeway.analysis.detectors - not deploy records, and not A and B. Each
    one carries what the detector actually saw and the counterfactual it
    derived, because that is the evidence a planner is reasoning from.

    Note what is still absent, and absent for the same reason it is absent
    from build_request: PlanRequest has no field that could carry a
    measurement, and none of these fields is one. A test asserts that the
    rendered prompt contains no result, ratio or verdict.
    """
    return PlanRequest(
        incident=dict(incident),
        candidates=tuple({
            "id": h.id,
            "label": h.label,
            "file": h.file,
            "line": h.line,
            "symbol": h.symbol,
            "kind": h.kind,
            "observed": h.observed,
            "counterfactual": h.counterfactual,
            "evidence": h.evidence,
            "reason": h.reason,
            "detector": h.detector,
        } for h in hypotheses),
        intervention_surfaces=tuple(sorted(state)),
        incident_state=dict(state),
        fixtures=tuple(workloads),
        failure_factor=verdict.FAILURE_FACTOR,
        recovery_factor=verdict.RECOVERY_FACTOR,
        target_hypothesis=target,
        evidence_kind=CODE_EVIDENCE,
    )


def plan_experiment(request: PlanRequest, provider) -> PlanOutcome:
    """Ask a provider for an experiment; fall back if anything at all goes wrong.

    "Anything at all" is deliberate: no key, no network, a timeout, malformed
    JSON and a rejected plan all land in the same place.
    """
    reason = ""
    asked = getattr(provider, "name", "unknown")
    try:
        raw = provider.propose(request, PLAN_SCHEMA)
        report = validate(raw, request)
        if report.accepted:
            return PlanOutcome(plan=report.plan, report=report,
                               source=provider.name,
                               kind=getattr(provider, "kind", "unknown"),
                               proposed_by=asked)
        reason = ("the validator rejected the plan: %s"
                  % "; ".join(c.name for c in report.rejections))
    except ProviderUnavailable as exc:
        reason = str(exc)
    except Exception as exc:                       # noqa: BLE001 - never fatal
        reason = "%s: %s" % (type(exc).__name__, exc)

    fallback = DeterministicPlanner()
    report = validate(fallback.propose(request), request)
    if not report.accepted:                        # would be a bug in our own planner
        raise RuntimeError("the deterministic fallback produced an invalid plan: %s"
                           % [c.detail for c in report.rejections])
    return PlanOutcome(plan=report.plan, report=report, source=fallback.name,
                       kind=fallback.kind, proposed_by=asked,
                       fallback_reason=reason)


def default_provider(offline: bool = False):
    """Which planner to ask first.

    Gemini when a key is configured and offline was not requested; the
    deterministic planner otherwise. The distinction matters downstream: a run
    that never had a key is a deterministic RUN, not a fallback, and the
    interface must not call it one.
    """
    if offline:
        return DeterministicPlanner()
    gemini = GeminiPlanner()
    return gemini if gemini.available else DeterministicPlanner()


def phases_for(plan: ExperimentPlan, incident_state):
    """Turn an accepted plan into the phases the engine will run.

    Note what does and does not cross this boundary: the plan contributes which
    candidate to remove and which fixture to replay. The seven phases, the
    controls and every comparison are constructed by causeway.verdict, so a
    planner cannot move its own goalposts - and there are no goalposts to move,
    because the reference is measured during the run.
    """
    return verdict.plan_phases(plan.hypothesis_id, incident_state, plan.fixture_id)
