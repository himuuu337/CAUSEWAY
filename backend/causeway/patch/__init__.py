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
from dataclasses import dataclass

from causeway.patch.deterministic import DeterministicPatchPlanner
from causeway.patch.gemini import GeminiPatchPlanner
from causeway.patch.schema import (PATCH_SCHEMA, CodePatch, PatchRequest,
                                   ProviderTimeout, ProviderUnavailable)
from causeway.patch.validator import PatchValidationReport, validate
from causeway.sandbox.variant import SourceEdit

__all__ = ["PatchOutcome", "plan_patch", "default_patch_provider", "edits_for",
          "unified_diff_for", "display_rejection_reason", "TIMEOUT_REASON",
          "NO_PATCH_REASON", "PATCH_SCHEMA", "CodePatch", "PatchRequest",
          "ProviderUnavailable", "ProviderTimeout", "PatchValidationReport"]

# What a dashboard may say about a patch that was never applied. Never the
# raw text a provider or the deterministic fallback declined with when no
# candidate was even produced to validate - that text can name the exact
# narrow instruction shape an offline fallback recognises, which is an
# implementation detail, not something someone investigating their own
# repository needs to see. The raw detail stays available, verbatim, on
# PatchOutcome.fallback_reason for backend logs and the raw event stream.
TIMEOUT_REASON = (
    "AI patch generation timed out before a safe patch could be produced. "
    "No repository files were changed. Retry the analysis or provide a more "
    "specific problem description.")
NO_PATCH_REASON = (
    "AI patch generation could not produce a safe, validated patch for this "
    "repository and instruction. No repository files were changed. Retry the "
    "analysis or provide a more specific problem description.")


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


def plan_patch(request: PatchRequest, provider, workspace: str, intent=None) -> PatchOutcome:
    """Ask a provider for a patch; fall back if anything at all goes wrong.

    Mirrors causeway.fixer.plan_fix: no key, no network, a timeout, malformed
    JSON and a rejected patch all land in the same place, and the fallback
    goes through the identical validator a model's proposal does. Unlike
    fixer's fallback, this one is not guaranteed to succeed - it recognises
    one narrow instruction shape - so a fallback that also fails is reported,
    not raised as a bug.
    """
    reason = ""
    timed_out = False
    asked = getattr(provider, "name", "unknown")
    try:
        raw = provider.propose(request)
        report = validate(raw, request, workspace, intent=intent)
        if report.accepted:
            return PatchOutcome(patch=report.patch, report=report,
                                source=provider.name,
                                kind=getattr(provider, "kind", "unknown"),
                                proposed_by=asked)
        reason = ("the patch validator rejected the proposal: %s"
                  % "; ".join(c.name for c in report.rejections))
    except ProviderTimeout as exc:
        reason = str(exc)
        timed_out = True
    except ProviderUnavailable as exc:
        reason = str(exc)
    except Exception as exc:                       # noqa: BLE001 - never fatal
        reason = "%s: %s" % (type(exc).__name__, exc)

    if isinstance(provider, DeterministicPatchPlanner):
        # Already were the fallback - nothing left to fall back to.
        return PatchOutcome(patch=None, report=PatchValidationReport(()),
                            source=provider.name, kind=provider.kind,
                            proposed_by=asked, fallback_reason=reason,
                            timed_out=timed_out)

    fallback = DeterministicPatchPlanner()
    try:
        raw = fallback.propose(request)
        report = validate(raw, request, workspace, intent=intent)
    except ProviderUnavailable as exc:
        return PatchOutcome(patch=None, report=PatchValidationReport(()),
                            source=fallback.name, kind=fallback.kind,
                            proposed_by=asked,
                            fallback_reason="%s; the deterministic fallback also "
                                           "declined: %s" % (reason, exc),
                            timed_out=timed_out)
    return PatchOutcome(patch=report.patch, report=report, source=fallback.name,
                        kind=fallback.kind, proposed_by=asked, fallback_reason=reason,
                        timed_out=timed_out)


def display_rejection_reason(outcome: PatchOutcome) -> str:
    """The message a dashboard may show for a patch that was never applied.

    A timeout always gets the same clean, actionable sentence - never
    `outcome.fallback_reason`'s raw text, which for a timeout is Gemini's
    own error string concatenated with whatever the deterministic fallback
    declined with (frequently the exact narrow instruction pattern it
    recognises, an implementation detail no one investigating a real
    repository needs to see).

    A validator rejection is shown as-is: `outcome.report.checks` is
    non-empty only when a candidate was actually produced and checked, and
    naming which deterministic check failed is useful, safe information -
    never the narrow-pattern text, because that text only ever appears when
    NO candidate reached the validator at all.
    """
    if outcome.timed_out:
        return TIMEOUT_REASON
    if outcome.report.checks:
        return outcome.fallback_reason or NO_PATCH_REASON
    return NO_PATCH_REASON


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
