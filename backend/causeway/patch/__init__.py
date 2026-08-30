"""Requested-change patching: a general, bounded CodePatch model.

This is deliberately a different shape from causeway.fixer. A fix repairs an
already-PROVEN cause at one named, whitelisted repair surface, and its `after`
is checked against a known-safe value the detector already derived. A
requested change has no proven cause and no known-safe answer to check
against - the user asked for new behaviour, and a planner (Gemini, or the
deterministic fallback) has to actually author it. What replaces "matches the
known-safe repair" here is a wider, still-deterministic envelope: bounded file
and hunk counts, paths that must be both analysable and declared patchable by
the repository's own manifest, before-text that must match the file exactly
as it exists right now, and the user's own enforceable constraints.

causeway.patch.validator is the only thing that decides whether a proposed
CodePatch may reach a disposable copy of the repository. Nothing here ever
writes to the real clone.
"""
from __future__ import annotations

import difflib
import io
import os
import time
from dataclasses import dataclass
from typing import Optional

from causeway.patch.deterministic import DeterministicPatchPlanner
from causeway.patch.gemini import GeminiPatchPlanner
from causeway.patch.schema import (EMPTY_PATCH, GEMINI_HTTP_ERROR, GEMINI_INVALID_JSON,
                                   GEMINI_INVALID_RESPONSE, GEMINI_RATE_LIMIT,
                                   GEMINI_SCHEMA_ERROR, GEMINI_TIMEOUT, PATCH_SCHEMA,
                                   PATCH_VALIDATION_REJECTED, NEEDS_CLARIFICATION,
                                   NO_ACTIONABLE_DEFECT_FOUND, SOURCE_CONTEXT_INSUFFICIENT,
                                   UNKNOWN_PLANNER_FAILURE, CodePatch, PatchRequest,
                                   ProviderTimeout, ProviderUnavailable)
from causeway.patch.validator import PatchValidationReport, validate
from causeway.sandbox.variant import SourceEdit

__all__ = ["PatchOutcome", "plan_patch", "default_patch_provider", "edits_for",
          "unified_diff_for", "display_rejection_reason", "reason_code_for",
          "check_actionable", "message_for_reason_code", "TIMEOUT_REASON",
          "NO_PATCH_REASON", "NEEDS_CLARIFICATION_REASON", "GEMINI_RATE_LIMIT_REASON",
          "GEMINI_UNAVAILABLE_REASON", "GEMINI_MALFORMED_RESPONSE_REASON",
          "PATCH_SCHEMA", "CodePatch",
          "PatchRequest", "ProviderUnavailable", "ProviderTimeout", "PatchValidationReport"]

# What a dashboard may say about a patch that was never applied. Never the
# raw text a provider or the deterministic fallback declined with when no
# candidate was even produced to validate - that text can name the exact
# narrow instruction shape an offline fallback recognises, which is an
# implementation detail, not something someone investigating their own
# repository needs to see. The raw detail stays available, verbatim, on
# PatchOutcome.fallback_reason and the patch_generation_failed event, for
# backend logs and the raw event stream.
TIMEOUT_REASON = (
    "AI patch generation timed out before a safe patch could be produced. "
    "No repository files were changed. Retry the analysis or provide a more "
    "specific problem description.")
NO_PATCH_REASON = (
    "AI patch generation could not produce a safe, validated patch for this "
    "repository and instruction. No repository files were changed. Retry the "
    "analysis or provide a more specific problem description.")
# Distinct from NO_PATCH_REASON on purpose: a rate limit, an unreachable
# provider, or a malformed response are operational problems with reaching
# Gemini, not a verdict on the instruction - collapsing them into "try being
# more specific" tells someone whose API key is wrong to rephrase a request
# that was never going to work no matter how it was phrased. Still no raw
# provider text, the same safety this module applies everywhere else.
GEMINI_RATE_LIMIT_REASON = (
    "Gemini is rate-limiting requests right now. No repository files were "
    "changed. Wait a moment and retry.")
GEMINI_UNAVAILABLE_REASON = (
    "Gemini could not be reached to propose a patch - a configuration or "
    "network problem, not your instruction. No repository files were "
    "changed. Check GEMINI_API_KEY and CAUSEWAY_GEMINI_MODEL, or run "
    "python -m causeway.cli gemini-check.")
GEMINI_MALFORMED_RESPONSE_REASON = (
    "Gemini's response could not be read as a patch. No repository files "
    "were changed. This is usually transient - retry the analysis.")
VALIDATION_REJECTED_TEMPLATE = (
    "AI proposed a change, but Causeway's deterministic safety validator "
    "rejected it (%s). No repository files were changed.")
NEEDS_CLARIFICATION_REASON = (
    "Causeway could not identify a concrete defect from the available source "
    "and evidence. Provide an error, failing behavior, or more specific goal.")

# Instructions this narrow, exact-phrase heuristic recognises as carrying no
# concrete defect to act on. Deliberately a short, explicit list rather than
# a fuzzy classifier: a false positive here blocks a legitimate request, and
# a false negative just means Gemini is asked and (per its own system
# instruction) may decline on its own - the safer failure direction.
_VAGUE_INSTRUCTIONS = frozenset((
    "fix the code", "fix it", "fix this", "fix bugs", "fix the bug",
    "fix issues", "improve the code", "improve this", "make it better",
    "clean up", "clean up the code", "refactor", "refactor this",
    "make it work", "do something",
))


@dataclass(frozen=True)
class PatchOutcome:
    patch: CodePatch
    report: PatchValidationReport
    source: str
    kind: str                    # "gemini" | "deterministic"
    proposed_by: str = ""
    fallback_reason: str = ""
    # Set only when the PRIMARY provider (never the deterministic fallback
    # itself) failed specifically because it did not answer in time. Kept
    # separate from fallback_reason's prose so a caller can choose the exact
    # clean message a timeout gets, without parsing text to find out.
    timed_out: bool = False
    # One of causeway.patch.schema.REASON_CODES, or "" on success. Internal
    # diagnostics - see patch_generation_failed's docstring at the call
    # sites in causeway.standard_investigation / causeway.requested_change.
    reason_code: str = ""
    elapsed_ms: float = 0.0
    selected_file_count: int = 0
    selected_char_count: int = 0

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallback_reason)

    def as_dict(self) -> dict:
        return {
            "patch": self.patch.as_dict(),
            "validation": self.report.as_dict(),
            "provenance": {
                "source": self.source,
                "kind": self.kind,
                "proposed_by": self.proposed_by or self.source,
                "used_fallback": self.used_fallback,
                "fallback_reason": self.fallback_reason,
            },
        }

    def diagnostics(self) -> dict:
        """Everything a patch_generation_failed event carries - technical
        detail for backend logs and the raw event stream, never the thing a
        dashboard leads with (see display_rejection_reason)."""
        return {
            "reason_code": self.reason_code, "message": self.fallback_reason,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "selected_file_count": self.selected_file_count,
            "selected_char_count": self.selected_char_count,
            "planner": self.proposed_by or self.source,
        }


def _classify_exception(exc: BaseException) -> str:
    if isinstance(exc, ProviderTimeout):
        return GEMINI_TIMEOUT
    text = str(exc).lower()
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return GEMINI_RATE_LIMIT
    if "returned http" in text or "unreachable" in text:
        return GEMINI_HTTP_ERROR
    if "no codepatch object" in text or "could not read the gemini response" in text:
        return GEMINI_INVALID_RESPONSE
    if isinstance(exc, ValueError) or "json" in text or "expecting value" in text:
        return GEMINI_INVALID_JSON
    return UNKNOWN_PLANNER_FAILURE


def check_actionable(request: PatchRequest) -> Optional[str]:
    """None when there is enough here to act on. Otherwise the reason code
    explaining why Causeway will not guess: an instruction this narrow
    heuristic recognises as carrying no concrete defect
    (NO_ACTIONABLE_DEFECT_FOUND), an empty one (NEEDS_CLARIFICATION), or
    bounded source that came back empty (SOURCE_CONTEXT_INSUFFICIENT - no
    file was readable, so there is nothing to show a planner regardless of
    how specific the instruction is).

    Deliberately checked BEFORE a provider is ever asked: a vague
    instruction is not something Gemini should be trusted to interpret
    charitably, and an empty file_contents means asking would just spend a
    request on a prompt with no source in it at all.
    """
    text = (request.instruction or "").strip().lower()
    if not text:
        return NEEDS_CLARIFICATION
    if text in _VAGUE_INSTRUCTIONS:
        return NO_ACTIONABLE_DEFECT_FOUND
    if not request.file_contents:
        return SOURCE_CONTEXT_INSUFFICIENT
    return None


def plan_patch(request: PatchRequest, provider, workspace: str, intent=None) -> PatchOutcome:
    """Ask a provider for a patch; fall back if anything at all goes wrong.

    Mirrors causeway.fixer.plan_fix: no key, no network, a timeout, malformed
    JSON and a rejected patch all land in the same place, and the fallback
    goes through the identical validator a model's proposal does. Unlike
    fixer's fallback, this one is not guaranteed to succeed - it recognises
    one narrow instruction shape - so a fallback that also fails is reported,
    not raised as a bug. Exactly one request is made to the primary provider
    here, ever - the deterministic fallback below makes no network call at
    all, so this is never a retry.
    """
    started = time.monotonic()
    file_count = len(request.file_contents)
    char_count = sum(len(text) for text in request.file_contents.values())

    def _finish(**overrides) -> PatchOutcome:
        overrides.setdefault("elapsed_ms", (time.monotonic() - started) * 1000.0)
        overrides.setdefault("selected_file_count", file_count)
        overrides.setdefault("selected_char_count", char_count)
        return PatchOutcome(**overrides)

    reason = ""
    reason_code = ""
    timed_out = False
    asked = getattr(provider, "name", "unknown")
    try:
        raw = provider.propose(request)
        report = validate(raw, request, workspace, intent=intent)
        if report.accepted:
            return _finish(patch=report.patch, report=report, source=provider.name,
                          kind=getattr(provider, "kind", "unknown"), proposed_by=asked)
        names = [c.name for c in report.rejections]
        reason = "the patch validator rejected the proposal: %s" % "; ".join(names)
        reason_code = GEMINI_SCHEMA_ERROR if "schema" in names else PATCH_VALIDATION_REJECTED
    except ProviderTimeout as exc:
        reason, reason_code, timed_out = str(exc), GEMINI_TIMEOUT, True
    except ProviderUnavailable as exc:
        reason, reason_code = str(exc), _classify_exception(exc)
    except Exception as exc:                       # noqa: BLE001 - never fatal
        reason = "%s: %s" % (type(exc).__name__, exc)
        reason_code = _classify_exception(exc)

    if isinstance(provider, DeterministicPatchPlanner):
        # Already were the fallback - nothing left to fall back to.
        return _finish(patch=None, report=PatchValidationReport(()),
                      source=provider.name, kind=provider.kind, proposed_by=asked,
                      fallback_reason=reason, timed_out=timed_out, reason_code=reason_code)

    fallback = DeterministicPatchPlanner()
    try:
        raw = fallback.propose(request)
        report = validate(raw, request, workspace, intent=intent)
    except ProviderUnavailable as exc:
        return _finish(patch=None, report=PatchValidationReport(()),
                      source=fallback.name, kind=fallback.kind, proposed_by=asked,
                      fallback_reason="%s; the deterministic fallback also "
                                     "declined: %s" % (reason, exc),
                      timed_out=timed_out, reason_code=reason_code or UNKNOWN_PLANNER_FAILURE)
    if not report.accepted:
        reason_code = reason_code or PATCH_VALIDATION_REJECTED
    return _finish(patch=report.patch, report=report, source=fallback.name,
                  kind=fallback.kind, proposed_by=asked, fallback_reason=reason,
                  timed_out=timed_out, reason_code="" if report.accepted else reason_code)


def reason_code_for(outcome: PatchOutcome) -> str:
    """The one reason code that best explains why `outcome` carries no
    patch. Prefers the outcome's own classification; timed_out always wins
    (a timeout's reason_code is already GEMINI_TIMEOUT, this is only a
    safety net for a caller that only checked the boolean)."""
    if outcome.timed_out:
        return GEMINI_TIMEOUT
    return outcome.reason_code or UNKNOWN_PLANNER_FAILURE


def message_for_reason_code(code: str, outcome: PatchOutcome = None) -> str:
    """The exact sentence a dashboard shows for one reason code. Never the
    raw provider/fallback text - that stays on outcome.fallback_reason and
    the patch_generation_failed event for backend logs."""
    if code in (NEEDS_CLARIFICATION, NO_ACTIONABLE_DEFECT_FOUND):
        return NEEDS_CLARIFICATION_REASON
    if code == GEMINI_TIMEOUT:
        return TIMEOUT_REASON
    if code == GEMINI_RATE_LIMIT:
        return GEMINI_RATE_LIMIT_REASON
    if code == GEMINI_HTTP_ERROR:
        return GEMINI_UNAVAILABLE_REASON
    if code in (GEMINI_INVALID_RESPONSE, GEMINI_INVALID_JSON):
        return GEMINI_MALFORMED_RESPONSE_REASON
    if code in (PATCH_VALIDATION_REJECTED, GEMINI_SCHEMA_ERROR) and outcome is not None \
            and outcome.report.checks:
        names = "; ".join(c.name for c in outcome.report.rejections) or "validation failed"
        return VALIDATION_REJECTED_TEMPLATE % names
    return NO_PATCH_REASON


def display_rejection_reason(outcome: PatchOutcome) -> str:
    """The message a dashboard may show for a patch that was never applied.

    A timeout always gets the same clean, actionable sentence - never
    `outcome.fallback_reason`'s raw text, which for a timeout is Gemini's
    own error string concatenated with whatever the deterministic fallback
    declined with (frequently the exact narrow instruction pattern it
    recognises, an implementation detail no one investigating a real
    repository needs to see).

    A validator rejection is shown with which check failed: useful, safe
    information - never the narrow-pattern text, because that text only
    ever appears when NO candidate reached the validator at all.
    """
    return message_for_reason_code(reason_code_for(outcome), outcome)


def default_patch_provider(offline: bool = False):
    if offline:
        return DeterministicPatchPlanner()
    gemini = GeminiPatchPlanner()
    return gemini if gemini.available else DeterministicPatchPlanner()


def edits_for(patch: CodePatch):
    return [SourceEdit(file=f.path, before=h.before, after=h.after, label=patch.summary)
           for f in patch.files for h in f.hunks]


def unified_diff_for(workspace: str, patch: CodePatch) -> str:
    """The patch, as a human would review it - computed from the real files,
    applying each file's hunks in order. Nothing here writes anything."""
    chunks = []
    for f in patch.files:
        path = os.path.join(workspace, f.path)
        with io.open(path, encoding="utf-8") as handle:
            original = handle.read()
        patched = original
        for hunk in f.hunks:
            if patched.count(hunk.before) != 1:
                raise ValueError("cannot render a diff for %s - a hunk's before-text "
                                 "is not unique in the file as it currently stands"
                                 % f.path)
            patched = patched.replace(hunk.before, hunk.after, 1)
        chunks.append("".join(difflib.unified_diff(
            original.splitlines(keepends=True), patched.splitlines(keepends=True),
            fromfile="a/%s" % f.path, tofile="b/%s" % f.path, n=3)))
    return "".join(chunks)
