"""The standard repository investigation: a normal public GitHub repository
with no causeway.json.

    GitHub URL + user instruction, a repository with no manifest
      -> Python detected, a bounded scored selection of its own source read
      -> Gemini authors a CodePatch from real source (or the requested-change
         deterministic fallback does, for the one instruction shape it knows)
      -> deterministic validator (causeway.patch.validator - unchanged: the
         same path safety, denylist and constraint enforcement a manifest
         repository's requested change goes through)
      -> the patch applied to a disposable copy - never the clone
      -> whatever objective verification is actually available:
           a syntax check, always (cheap, safe, no execution of the
           repository's own code)
           VERIFIED is never claimed here - there is no reliable way to
           start or test an arbitrary repository without installing its
           dependencies, which Causeway does not do automatically, so a
           syntactically sound patch is reported IMPLEMENTED, VERIFICATION
           INCOMPLETE rather than VERIFIED

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
import py_compile
import time
from typing import Iterator

from causeway import intent as intent_module
from causeway import patch as patcher
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


def _syntax_check(root: str, files) -> Iterator[dict]:
    """py_compile only - parses and compiles to bytecode, never runs a line
    of the repository's own code. The one piece of objective evidence this
    path can always produce without installing anything or executing
    anything untrusted."""
    problems = []
    for relative in files:
        path = os.path.join(root, relative)
        try:
            # quiet=2 is deliberately NOT used here: on some interpreter
            # builds it has been observed to suppress doraise's own
            # PyCompileError along with the console message it silences,
            # which would turn a broken patch into a false IMPLEMENTED.
            py_compile.compile(path, doraise=True)
            ok, detail = True, "compiles cleanly"
        except py_compile.PyCompileError as exc:
            ok, detail = False, str(exc.msg)
            problems.append((relative, detail))
        except OSError as exc:
            ok, detail = False, str(exc)
            problems.append((relative, detail))
        yield {"type": "syntax_check", "file": relative, "passed": ok, "detail": detail}
    yield problems


def run(context, intent, offline: bool = None) -> Iterator[dict]:
    """Run a standard (manifest-less) repository investigation."""
    started = time.time()

    yield {"type": "requested_change_start", "instruction": intent.raw_instruction,
           "goal": intent.goal, "files_considered": list(context.sources)}
    yield {"type": "standard_repository", "language": context.language,
           "entrypoint": context.entrypoint or None,
           "all_python_files": len(context.all_python_files),
           "files_selected": list(context.sources),
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
               "reason": (outcome.fallback_reason or "no safe patch could be validated")}
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
    problems = []
    try:
        variant = materialise(context.workspace, None, edits=patcher.edits_for(patch))
        touched = [f.path for f in patch.files]
        for item in _syntax_check(variant.root, touched):
            if isinstance(item, list):
                problems = item
            else:
                yield item
    finally:
        if variant is not None:
            variant.cleanup()
    yield _stage("verification", "done")

    if problems:
        verdict = "FAILED"
        reason = ("the patch does not parse as valid Python: %s"
                  % "; ".join("%s - %s" % p for p in problems))
    else:
        verdict = "IMPLEMENTED_VERIFICATION_INCOMPLETE"
        reason = (
            "the patch was applied to a disposable copy and every patched file "
            "still compiles as valid Python. This repository has no causeway.json, "
            "so there is no controlled workload and no reliable way to start or "
            "run it - Causeway does not install a repository's dependencies or "
            "execute untrusted code automatically, so runtime behaviour was not "
            "verified. %s" % context.tests_note)

    yield {"type": "requested_change_verdict", "verdict": verdict, "reason": reason,
           "before": [], "after": []}
    yield {"type": "done", "elapsed_s": round(time.time() - started, 1)}
