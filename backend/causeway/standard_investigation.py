"""The standard repository investigation: a normal public GitHub repository
with no causeway.json, in whatever language it turns out to be written in.

    GitHub URL + user instruction, a repository with no manifest
      -> languages detected (causeway.languages), a bounded scored
         selection of its own source read, across every language found
      -> Gemini authors a CodePatch from real source (or the requested-change
         deterministic fallback does, for the one instruction shape it knows)
      -> deterministic validator (causeway.patch.validator - unchanged: the
         same path safety, denylist and constraint enforcement a manifest
         repository's requested change goes through)
      -> the patch applied to a disposable copy - never the clone
      -> whatever objective, non-executing verification each patched file's
         own language adapter can safely run (causeway.languages) - never
         upgraded to VERIFIED on Gemini's own say-so, because a compile or
         syntax check is not runtime evidence and this module never
         pretends it is

This is deliberately a different verdict vocabulary from
causeway.requested_change's HTTP-probed VERIFIED/FAILED/UNRESOLVED: a
manifest repository declares real acceptance criteria and Causeway can prove
against them. A standard repository has not, and pretending otherwise would
be exactly the dishonesty this project's verdict-language rules elsewhere
exist to prevent.
"""
from __future__ import annotations

import io
import os
import time
from collections import defaultdict
from typing import Iterator

from causeway import intent as intent_module
from causeway import patch as patcher
from causeway.languages.registry import adapter_for
from causeway.sandbox.variant import materialise

MAX_FILE_CHARS = 6000


def _stage(name: str, status: str, **extra) -> dict:
    return dict({"type": "stage", "stage": name, "status": status,
                 "t": round(time.time(), 3)}, **extra)


def _bounded_file_contents(workspace: str, patchable) -> dict:
    contents = {}
    for relative in patchable:
        path = os.path.join(workspace, relative)
        if not os.path.isfile(path):
            continue
        with io.open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n# ...[truncated]\n"
        contents[relative] = text
    return contents


def _group_by_language(files, detected_languages) -> dict:
    """Every patched file, bucketed by the first detected language whose
    adapter recognises its extension. A file no detected adapter recognises
    (a config file, say) is bucketed under "" and simply has no check run
    for it - that is reported, not silently dropped."""
    groups: dict = defaultdict(list)
    for relative in files:
        matched = ""
        for language in detected_languages:
            adapter = adapter_for(language)
            if adapter and adapter.matches_file(relative):
                matched = language
                break
        groups[matched].append(relative)
    return groups


def _verify_patch(root: str, files, detected_languages) -> Iterator[dict]:
    """Run each represented language's own adapter against the files its
    language claimed, on a disposable, already-patched copy. Yields
    `verification_check` events; the last item yielded is
    (any_failed, notes, unchecked_files)."""
    groups = _group_by_language(files, detected_languages)
    any_failed = False
    notes = []
    unchecked = list(groups.get("", ()))
    for language, language_files in groups.items():
        if not language:
            continue
        adapter = adapter_for(language)
        result = adapter.verify(root, language_files)
        if not result.available:
            notes.append("%s: %s" % (adapter.display_name, result.note))
            unchecked.extend(language_files)
            continue
        notes.append("%s: %s" % (adapter.display_name, result.note))
        for check in result.checks:
            yield {"type": "verification_check", "language": language,
                  **check.as_dict()}
        if result.any_failed:
            any_failed = True
    yield (any_failed, notes, unchecked)


def run(context, intent, offline: bool = None) -> Iterator[dict]:
    """Run a standard (manifest-less) repository investigation."""
    started = time.time()

    yield {"type": "requested_change_start", "instruction": intent.raw_instruction,
           "goal": intent.goal, "files_considered": list(context.sources)}
    yield {"type": "language_detected", "primary": context.primary_language,
           "detected": list(context.detected_languages),
           "counts": dict(context.language_counts)}
    yield {"type": "source_selection", "files": list(context.sources),
           "all_source_files": len(context.all_source_files),
           "entrypoint": context.entrypoint or None,
           "tests_detected": context.tests_detected, "tests_note": context.tests_note}

    if intent.mode == intent_module.DIAGNOSE_ONLY:
        yield {"type": "patch_rejected",
               "reason": ("the instruction asked for diagnosis only, so no patch was "
                          "proposed or applied - select \"diagnose and fix\" or "
                          "\"requested change\" to have Causeway propose one")}
        yield {"type": "done", "elapsed_s": round(time.time() - started, 1)}
        return

    # ---- 1. planning + validation (AI proposes, code validates) -----------
    file_contents = _bounded_file_contents(context.workspace, context.patchable)
    request = patcher.PatchRequest(
        instruction=intent.raw_instruction, goal=intent.goal, intent=intent.as_dict(),
        service=context.name, entrypoint=context.entrypoint or "",
        sources=context.sources, patchable=context.patchable,
        file_contents=file_contents, acceptance={},
    )
    provider = patcher.default_patch_provider(offline=offline)
    yield _stage("patch_planning", "running", planner=provider.name)
    outcome = patcher.plan_patch(request, provider, context.workspace, intent=intent)
    if outcome.patch is not None:
        yield dict({"type": "patch_plan"}, **outcome.as_dict())
    yield _stage("patch_planning", "done")

    yield _stage("patch_validation", "running")
    yield {"type": "patch_validation", **outcome.report.as_dict()}
    yield _stage("patch_validation", "done")

    if not outcome.report.accepted:
        yield {"type": "patch_rejected",
               "reason": patcher.display_rejection_reason(outcome),
               "detail": outcome.fallback_reason}
        yield {"type": "done", "elapsed_s": round(time.time() - started, 1)}
        return

    patch = outcome.patch
    try:
        diff = patcher.unified_diff_for(context.workspace, patch)
    except ValueError as exc:
        yield {"type": "patch_rejected", "reason": str(exc)}
        yield {"type": "done", "elapsed_s": round(time.time() - started, 1)}
        return

    yield {"type": "patch_apply", "summary": patch.summary,
           "files": [f.path for f in patch.files], "diff": diff,
           "reasoning_summary": patch.reasoning_summary,
           "applied_to": "a disposable copy of the repository - the clone and "
                         "the original repository are never written to"}

    # ---- 2. whatever objective verification is actually available --------
    yield _stage("verification", "running")
    variant = None
    any_failed, notes, unchecked = False, [], []
    try:
        variant = materialise(context.workspace, None, edits=patcher.edits_for(patch))
        touched = [f.path for f in patch.files]
        for item in _verify_patch(variant.root, touched, context.detected_languages):
            if isinstance(item, tuple):
                any_failed, notes, unchecked = item
            else:
                yield item
    finally:
        if variant is not None:
            variant.cleanup()
    yield _stage("verification", "done")

    if any_failed:
        verdict = "FAILED"
        reason = "a language-specific check failed against the patched copy: %s" \
            % "; ".join(notes)
    else:
        verdict = "IMPLEMENTED_VERIFICATION_INCOMPLETE"
        pieces = [
            "the patch was applied to a disposable copy and every available "
            "language-specific check passed" if notes else
            "the patch was applied to a disposable copy",
        ]
        if notes:
            pieces.append("; ".join(notes))
        if unchecked:
            pieces.append("no check was available for: %s" % ", ".join(sorted(unchecked)))
        pieces.append(
            "this repository has no causeway.json, so there is no controlled workload "
            "and no reliable way to start or run it - Causeway does not install a "
            "repository's dependencies or execute untrusted code automatically, so "
            "runtime behaviour was not verified. %s" % context.tests_note)
        reason = ". ".join(p for p in pieces if p)

    yield {"type": "requested_change_verdict", "verdict": verdict, "reason": reason,
           "before": [], "after": []}
    yield {"type": "done", "elapsed_s": round(time.time() - started, 1)}
