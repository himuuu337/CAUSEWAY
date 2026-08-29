"""Fix planning: schema, validator, providers, and the fallback path.

The model proposes a fix. This package decides whether the proposal is
allowed to run. causeway/fix_verdict.py decides what the fix's own result
means. Those three responsibilities never merge - the same separation
causeway/planner/__init__.py documents for the experiment loop. This package
imports causeway.verdict (for the PROVEN constant a fix request is gated on)
and causeway.sandbox.repair; neither of those imports back.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from causeway import verdict
from causeway.fixer.deterministic import DeterministicFixPlanner
from causeway.fixer.gemini import GeminiFixPlanner
from causeway.fixer.schema import (FIX_SCHEMA, FixOperation, FixRequest,
                                   FixSpec, ProviderUnavailable)
from causeway.fixer.validator import FixValidationReport, validate
from causeway.sandbox import repair


@dataclass(frozen=True)
class FixOutcome:
    spec: FixSpec
    report: FixValidationReport
    source: str
    kind: str                    # "gemini" | "deterministic"
    proposed_by: str = ""
    fallback_reason: str = ""

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallback_reason)

    def as_dict(self) -> dict:
        return {
            "fix": self.spec.as_dict(),
            "validation": self.report.as_dict(),
            "provenance": {
                "source": self.source,
                "kind": self.kind,
                "proposed_by": self.proposed_by or self.source,
                "used_fallback": self.used_fallback,
                "fallback_reason": self.fallback_reason,
            },
        }


def build_fix_request(candidate: dict, hypothesis_id: str, causal_verdict: str,
                      causal_reason: str,
                      surfaces: Optional[Mapping[str, Mapping[str, dict]]] = None
                      ) -> FixRequest:
    """Assemble everything a fix planner is allowed to see.

    Only ever called for a hypothesis the deterministic verdict has already
    decided is PROVEN - `causal_verdict` is a fact read from that decision,
    never something a planner supplies. `current_code` is read fresh from the
    real (unpatched) sandbox source, so a planner is always shown the value
    that is actually there right now.

    `surfaces` is None for the bundled demo (causeway.sandbox.repair's own
    REPAIR_SURFACES). A repository loaded through causeway.repository passes
    its own manifest-declared surfaces mapping instead.
    """
    targets = repair.targets_for(hypothesis_id, surfaces=surfaces)
    current_code = {target: repair.current_value(hypothesis_id, target, surfaces=surfaces)
                    for target in targets}
    mechanisms = [repair.repair_surface(hypothesis_id, t, surfaces=surfaces)["description"]
                 for t in targets]
    return FixRequest(
        hypothesis_id=hypothesis_id,
        candidate=dict(candidate),
        causal_verdict=causal_verdict,
        causal_reason=causal_reason,
        repair_targets=targets,
        current_code=current_code,
        mechanism="; ".join(mechanisms),
        surfaces=surfaces,
    )


def plan_fix(request: FixRequest, provider) -> FixOutcome:
    """Ask a provider for a fix; fall back if anything at all goes wrong.

    Mirrors causeway.planner.plan_experiment exactly: no key, no network, a
    timeout, malformed JSON and a rejected fix all land in the same place, and
    the fallback goes through the identical validator a model's proposal does.
    """
    reason = ""
    asked = getattr(provider, "name", "unknown")
    try:
        raw = provider.propose(request, FIX_SCHEMA)
        report = validate(raw, request)
        if report.accepted:
            return FixOutcome(spec=report.spec, report=report,
                              source=provider.name,
                              kind=getattr(provider, "kind", "unknown"),
                              proposed_by=asked)
        reason = ("the fix validator rejected the proposal: %s"
                  % "; ".join(c.name for c in report.rejections))
    except ProviderUnavailable as exc:
        reason = str(exc)
    except Exception as exc:                       # noqa: BLE001 - never fatal
        reason = "%s: %s" % (type(exc).__name__, exc)

    fallback = DeterministicFixPlanner()
    report = validate(fallback.propose(request), request)
    if not report.accepted:                        # would be a bug in our own planner
        raise RuntimeError("the deterministic fix fallback produced an invalid "
                           "fix: %s" % [c.detail for c in report.rejections])
    return FixOutcome(spec=report.spec, report=report, source=fallback.name,
                      kind=fallback.kind, proposed_by=asked,
                      fallback_reason=reason)


def default_fix_provider(offline: bool = False):
    """Which fix planner to ask first. Identical policy to
    causeway.planner.default_provider: Gemini when a key is configured and
    offline was not requested, the deterministic planner otherwise."""
    if offline:
        return DeterministicFixPlanner()
    gemini = GeminiFixPlanner()
    return gemini if gemini.available else DeterministicFixPlanner()


def fixable(causal_verdict: str, hypothesis_id: str,
           surfaces: Optional[Mapping[str, Mapping[str, dict]]] = None) -> bool:
    """Whether a fix should even be attempted: the verdict must be PROVEN, and
    at least one repair surface must be registered for this hypothesis. A is
    never PROVEN in the demo incident and has no repair surface either way -
    both are checked here so the orchestrator's policy and the validator's
    gate can never quietly drift apart."""
    return (causal_verdict == verdict.PROVEN
            and bool(repair.targets_for(hypothesis_id, surfaces=surfaces)))
