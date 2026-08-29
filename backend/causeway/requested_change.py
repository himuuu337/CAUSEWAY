"""The requested-change investigation.

    GitHub URL + user instruction, mode = REQUESTED_CHANGE
      -> bounded read of the repository's own patchable source
      -> Gemini authors a CodePatch (or the deterministic fallback does)
      -> deterministic validator
      -> the patch applied to a disposable copy, launched, and probed with
         real HTTP requests - the SAME requests sent to an unpatched
         disposable copy first, so "verified" means "measurably different
         and now correct", never "the model said so"
      -> deterministic verdict: VERIFIED, FAILED, or UNRESOLVED

This is deliberately separate from causeway.repo_investigation, which answers
a different question (why is an already-present incident happening, and
what proves it). A requested change has no hypothesis and no proven cause -
there is nothing here to run a controlled experiment against - so this module
never imports causeway.incident, causeway.localizer or causeway.observational
either, for the same reason repo_investigation does not.
"""
from __future__ import annotations

import io
import os
import time
from typing import Iterator

from causeway import patch as patcher
from causeway.sandbox import probe
from causeway.sandbox.runner import Sandbox
from causeway.sandbox.variant import materialise

# Bounded context: a requested-change planner is shown the repository's own
# patchable files, not the whole repository, and not without limit.
MAX_FILE_CHARS = 8000


def _stage(name: str, status: str, **extra) -> dict:
    return dict({"type": "stage", "stage": name, "status": status,
                 "t": round(time.time(), 3)}, **extra)


def _bounded_file_contents(workspace: str, patchable) -> dict:
    contents = {}
    for relative in patchable:
        path = os.path.join(workspace, relative)
        if not os.path.isfile(path):
            continue
        with io.open(path, encoding="utf-8") as handle:
            text = handle.read()
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n# ...[truncated]\n"
        contents[relative] = text
    return contents


def _probe_all(host: str, port: int, probes: dict, phase: str) -> Iterator[dict]:
    """Send every declared probe case once against a running sandbox, and
    yield one `verification_case` event per case. The last item yielded is
    the list of result dicts, for the caller to judge."""
    results = []
    for probe_name, spec in probes.items():
        method, path = spec["method"], spec["path"]
        for case in spec["cases"]:
            outcome = probe.send(host, port, method, path, body=case.get("body"))
            expected = case["expect_status"]
            passed = outcome["status"] in expected
            record = {"probe": probe_name, "case": case["name"], "method": method,
                      "path": path, "body": case.get("body"), "status": outcome["status"],
                      "expected_status": expected, "passed": passed,
                      "error": outcome["error"]}
            results.append(record)
            yield {"type": "verification_case", "phase": phase, **record}
    yield results


def run(context, intent, offline: bool = None) -> Iterator[dict]:
    """Run a requested change against an already-loaded RepositoryContext."""
    started = time.time()

    if not context.probes:
        yield {"type": "patch_rejected",
               "reason": ("this repository declares no verification probes in its "
                          "manifest, so a requested change here cannot be checked "
                          "against real behaviour - only diagnose or diagnose-and-fix "
                          "are supported on it")}
        yield {"type": "done", "elapsed_s": round(time.time() - started, 1)}
        return

    yield {"type": "requested_change_start", "instruction": intent.raw_instruction,
           "goal": intent.goal, "files_considered": list(context.patchable)}

    # ---- 1. planning + validation (AI proposes, code validates) -----------
    file_contents = _bounded_file_contents(context.workspace, context.patchable)
    request = patcher.PatchRequest(
        instruction=intent.raw_instruction, goal=intent.goal, intent=intent.as_dict(),
        service=context.service, entrypoint=context.entrypoint,
        sources=context.sources, patchable=context.patchable,
        file_contents=file_contents, acceptance=context.probes,
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

    # ---- 2. verification: real requests, unpatched then patched -----------
    yield _stage("verification", "running")
    yield {"type": "verification_start",
           "cases": [{"probe": name, "method": spec["method"], "path": spec["path"],
                     "cases": [c["name"] for c in spec["cases"]]}
                    for name, spec in context.probes.items()]}

    before_results = None
    variant = None
    try:
        variant = materialise(context.workspace, context.entrypoint, edits=())
        sandbox = Sandbox(context.database_path, context.work_db,
                          service_path=variant.service_path).start()
        try:
            for item in _probe_all(sandbox.host, sandbox.port, context.probes, "before"):
                if isinstance(item, list):
                    before_results = item
                else:
                    yield item
        finally:
            sandbox.stop()
    finally:
        if variant is not None:
            variant.cleanup()

    after_results = None
    variant = None
    try:
        variant = materialise(context.workspace, context.entrypoint,
                              edits=patcher.edits_for(patch))
        sandbox = Sandbox(context.database_path, context.work_db,
                          service_path=variant.service_path).start()
        try:
            for item in _probe_all(sandbox.host, sandbox.port, context.probes, "after"):
                if isinstance(item, list):
                    after_results = item
                else:
                    yield item
        finally:
            sandbox.stop()
    finally:
        if variant is not None:
            variant.cleanup()
    yield _stage("verification", "done")

    # ---- 3. the verdict - measured, never asserted -------------------------
    before_failed = [r for r in before_results if not r["passed"]]
    after_failed = [r for r in after_results if not r["passed"]]
    if after_failed:
        verdict, reason = "FAILED", (
            "%d of %d checks still fail against the patched copy: %s"
            % (len(after_failed), len(after_results),
               ", ".join(r["case"] for r in after_failed)))
    elif not before_failed:
        verdict, reason = "UNRESOLVED", (
            "every check already passed against the unpatched copy, so the patch "
            "could not be shown to have changed anything observable")
    else:
        verdict, reason = "VERIFIED", (
            "%d check(s) failed against the unpatched copy (%s) and all %d checks "
            "pass against the patched copy"
            % (len(before_failed), ", ".join(r["case"] for r in before_failed),
               len(after_results)))

    yield {"type": "requested_change_verdict", "verdict": verdict, "reason": reason,
           "before": before_results, "after": after_results}
    yield {"type": "done", "elapsed_s": round(time.time() - started, 1)}
