"""Fix planning: schema, validator, providers, and the fallback path.

The model proposes a fix. This package decides whether the proposal is
allowed to run. causeway/fix_verdict.py decides what the fix's own result
means. Those three responsibilities never merge - the same separation
causeway/planner/__init__.py documents for the experiment loop. This package
imports causeway.verdict (for the PROVEN constant a fix request is gated on)
and causeway.sandbox.repair; neither of those imports back.
"""
from __future__ import annotations

import difflib
import io
import os
from dataclasses import dataclass
from typing import Mapping, Optional

from causeway import verdict
from causeway.fixer.deterministic import DeterministicFixPlanner
from causeway.fixer.gemini import GeminiFixPlanner
from causeway.fixer.schema import (FIX_SCHEMA, FixOperation, FixRequest,
                                   FixSpec, ProviderUnavailable)
from causeway.fixer.validator import FixValidationReport, validate
from causeway.sandbox import repair
from causeway.sandbox.variant import SourceEdit

# The one operation a repository fix is representable as, and the only one
# causeway.fixer.schema.ALLOWED_OPERATION_TYPES admits. Widening this is
# future work, not something a proposal can talk its way into.
CODE_OPERATION = "replace_predicate"


class FixSurfaceUnavailable(RuntimeError):
    """The proven hypothesis no longer describes what is in the file, so
    there is nothing safe to offer a fix planner. Nothing is patched."""


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


# --------------------------------------------------------------- repository

def code_target(hypothesis) -> str:
    """The symbolic name a repository fix's repair surface is addressed by.

    A bare identifier, assembled from the hypothesis's own symbol and kind -
    never a path, and never anything a proposal supplied. The fix validator
    rejects a target containing a separator, and this cannot produce one.
    """
    return "%s_%s" % (hypothesis.symbol, hypothesis.kind)


def _current_from_workspace(workspace: str, hypothesis) -> str:
    """Read the broken text back out of the cloned file, right now.

    The hypothesis was derived from this file when the repository was loaded;
    this proves it still says the same thing, and that it says it exactly
    once, before anything is offered to a planner as the value to replace.
    """
    path = os.path.join(workspace, hypothesis.file)
    with io.open(path, encoding="utf-8") as handle:
        source = handle.read()
    occurrences = source.count(hypothesis.observed)
    if occurrences != 1:
        raise FixSurfaceUnavailable(
            "%s appears %d times in %s - a repair surface must be unambiguous"
            % (hypothesis.observed, occurrences, hypothesis.file))
    return hypothesis.observed


def code_surfaces(hypothesis, workspace: str) -> dict:
    """The repair surface for one proven CodeHypothesis, in the shape
    causeway.sandbox.repair already validates against.

    `current` is read live from the clone. `safe_after` is the counterfactual
    the detector derived from the repository's own schema - which is why the
    fix prompt must never quote it: it is the answer the validator exists to
    check a proposal against.
    """
    if not hypothesis.testable:
        raise FixSurfaceUnavailable(
            "%s has no counterfactual, so there is nothing to repair"
            % hypothesis.label)
    return {
        hypothesis.id: {
            code_target(hypothesis): {
                "operation_type": CODE_OPERATION,
                "current": (lambda value=_current_from_workspace(workspace, hypothesis): value),
                "safe_after": hypothesis.counterfactual,
                "description": hypothesis.reason,
            },
        },
    }


def build_code_fix_request(hypothesis, causal_verdict: str, causal_reason: str,
                           workspace: str, intent=None) -> FixRequest:
    """Assemble everything a fix planner may see about a REPOSITORY repair.

    Only ever called for a hypothesis causeway.verdict has already decided is
    PROVEN. The caller is responsible for having checked the intent's own
    constraints first - this function builds a request, it does not grant
    permission.
    """
    surfaces = code_surfaces(hypothesis, workspace)
    target = code_target(hypothesis)
    return FixRequest(
        hypothesis_id=hypothesis.id,
        candidate={"label": hypothesis.label, "detector": hypothesis.detector,
                   "kind": hypothesis.kind},
        causal_verdict=causal_verdict,
        causal_reason=causal_reason,
        repair_targets=(target,),
        current_code={target: repair.current_value(hypothesis.id, target,
                                                   surfaces=surfaces)},
        mechanism=hypothesis.reason,
        surfaces=surfaces,
        location={"file": hypothesis.file, "line": hypothesis.line,
                  "symbol": hypothesis.symbol, "observed": hypothesis.observed},
        intent=intent.as_dict() if intent is not None else None,
    )


def edit_for(operation, hypothesis) -> SourceEdit:
    """The source edit a validated FixOperation authorises.

    The operation SELECTS a repair surface; this supplies the bytes. The text
    written is the repository's own - hypothesis.observed replaced by the
    counterfactual the detector derived - never the strings that came back
    from a planner, which the validator has only proven equal ignoring
    whitespace. Equal-ignoring-whitespace is the right test for "did you
    understand the surface"; it is the wrong text to write into a file, where
    an exact single match is what makes the edit unambiguous.
    """
    if operation.target != code_target(hypothesis):
        raise FixSurfaceUnavailable(
            "%r is not the repair surface for %s" % (operation.target, hypothesis.label))
    if operation.type != CODE_OPERATION:
        raise FixSurfaceUnavailable("unsupported operation %r" % operation.type)
    return SourceEdit(file=hypothesis.file, before=hypothesis.observed,
                      after=hypothesis.counterfactual,
                      label="fix:%s" % hypothesis.id)


def unified_diff(workspace: str, hypothesis, edit: SourceEdit) -> str:
    """The patch, as a human would review it - computed from the real file.

    Nothing here writes anything. The variant machinery applies the same edit
    to a disposable copy when the fix is measured; this only renders it.
    """
    path = os.path.join(workspace, edit.file)
    with io.open(path, encoding="utf-8") as handle:
        original = handle.read()
    if original.count(edit.before) != 1:
        raise FixSurfaceUnavailable(
            "cannot render a diff for %s - the text to replace is not unique"
            % edit.file)
    patched = original.replace(edit.before, edit.after, 1)
    return "".join(difflib.unified_diff(
        original.splitlines(keepends=True), patched.splitlines(keepends=True),
        fromfile="a/%s" % edit.file, tofile="b/%s" % edit.file, n=3))


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
