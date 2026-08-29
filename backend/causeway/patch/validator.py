"""The deterministic gate a requested-change patch must pass before it ever
touches a disposable copy of the repository.

Mirrors causeway.fixer.validator's role but for a general, Gemini-authored
CodePatch. There is no known-safe `after` to compare a proposal against here
- a requested change is not a repair for an already-diagnosed cause, so the
model is genuinely authoring new source, and this module is what keeps that
authorship inside a small, provably-safe envelope instead of trusting it:
relative paths with no traversal, resolved and proven inside the workspace,
touching only files the repository's own manifest both lists as analysable
AND declares patchable, never .git, .env, or anything that looks like a
credential, a before-text that matches the file exactly as it exists right
now, an after-text free of the constructs that would hand the patched process
a shell, and every constraint the user's own instruction enforces.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Tuple

from causeway.patch.schema import (MAX_FILES, MAX_HUNK_CHARS, MAX_TOTAL_HUNKS,
                                   Check, CodePatch, PatchFile, PatchHunk,
                                   PatchRequest)

CHECK_NAMES = (
    "schema",
    "file_count_bounded",
    "hunk_count_bounded",
    "hunk_size_bounded",
    "paths_are_relative_no_traversal",
    "paths_stay_inside_workspace",
    "paths_are_declared_sources_and_patchable",
    "paths_avoid_denied_files",
    "before_text_matches_current_source_exactly",
    "after_text_has_no_dangerous_construct",
    "intent_permits_every_file",
    "intent_max_changed_files_respected",
    "no_schema_change_respected",
)

# Substrings, checked case-insensitively against a repository-relative path.
# Defense in depth on top of "must be in sources and patchable": those two
# lists are the repository's own declaration, and this is Causeway's own
# floor under it regardless of what a manifest says.
_DENY_PATH_SUBSTRINGS = (".env", ".git/", ".git\\", "secret", "credential",
                         "token", ".pem", ".key", "id_rsa")

# Constructs an `after` text may not introduce. Not exhaustive - a
# defense-in-depth net, not a sandbox - but it rules out the obvious ways a
# patch could try to reach outside the file it claims to be editing.
_DANGEROUS_CONSTRUCTS = ("os.system(", "subprocess.", "eval(", "exec(",
                         "__import__(", "shell=True", "DROP TABLE", "rm -rf")


@dataclass(frozen=True)
class PatchValidationReport:
    checks: Tuple[Check, ...]
    patch: CodePatch = None

    @property
    def accepted(self) -> bool:
        return self.patch is not None and all(c.passed for c in self.checks)

    @property
    def rejections(self) -> Tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.passed)

    def as_dict(self) -> dict:
        return {
            "checks": [c.as_dict() for c in self.checks],
            "passed": sum(1 for c in self.checks if c.passed),
            "total": len(self.checks),
            "accepted": self.accepted,
        }


def _schema_ok(raw) -> Tuple[bool, str, CodePatch]:
    if not isinstance(raw, dict):
        return False, "patch is not an object", None
    missing = [k for k in ("summary", "files", "reasoning_summary") if k not in raw]
    if missing:
        return False, "missing %s" % ", ".join(missing), None
    if not isinstance(raw["summary"], str) or not isinstance(raw["reasoning_summary"], str):
        return False, "summary/reasoning_summary must be strings", None
    files_raw = raw["files"]
    if not isinstance(files_raw, list) or not files_raw:
        return False, "files must be a non-empty list", None
    files = []
    for entry in files_raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) \
                or not entry["path"]:
            return False, "each file needs a non-empty string path", None
        hunks_raw = entry.get("hunks")
        if not isinstance(hunks_raw, list) or not hunks_raw:
            return False, "%s needs a non-empty list of hunks" % entry["path"], None
        hunks = []
        for hunk in hunks_raw:
            if (not isinstance(hunk, dict) or not isinstance(hunk.get("before"), str)
                    or not isinstance(hunk.get("after"), str)):
                return False, "every hunk needs string before/after", None
            hunks.append(PatchHunk(before=hunk["before"], after=hunk["after"]))
        files.append(PatchFile(path=entry["path"], hunks=tuple(hunks)))
    patch = CodePatch(summary=raw["summary"], files=tuple(files),
                      reasoning_summary=raw["reasoning_summary"])
    return True, "well-formed", patch


def _resolve_inside(workspace: str, relative: str):
    base = os.path.realpath(workspace)
    target = os.path.realpath(os.path.join(base, relative))
    if target != base and not target.startswith(base + os.sep):
        return None
    return target


def validate(raw: Mapping, request: PatchRequest, workspace: str, intent=None
            ) -> PatchValidationReport:
    """Every rule that stands between a proposed patch and a disposable copy
    of the repository. `intent` is the parsed IntentSpec for this run; passed
    separately from `request.intent` (its plain-dict mirror, shown to the
    planner) because this function needs to CALL `permits_file`, not just
    read what was asked."""
    checks = []

    ok, detail, patch = _schema_ok(raw)
    checks.append(Check("schema", ok, detail))
    if not ok:
        return PatchValidationReport(tuple(checks))

    file_count_ok = len(patch.files) <= MAX_FILES
    checks.append(Check(
        "file_count_bounded", file_count_ok,
        "%d file(s) touched, bound is %d" % (len(patch.files), MAX_FILES)))

    total_hunks = sum(len(f.hunks) for f in patch.files)
    hunk_count_ok = total_hunks <= MAX_TOTAL_HUNKS
    checks.append(Check(
        "hunk_count_bounded", hunk_count_ok,
        "%d hunk(s) total, bound is %d" % (total_hunks, MAX_TOTAL_HUNKS)))

    hunk_size_ok = all(len(h.before) <= MAX_HUNK_CHARS and len(h.after) <= MAX_HUNK_CHARS
                       for f in patch.files for h in f.hunks)
    checks.append(Check(
        "hunk_size_bounded", hunk_size_ok,
        "every hunk is at most %d characters each way" % MAX_HUNK_CHARS))

    def _no_traversal(path: str) -> bool:
        if not isinstance(path, str) or not path or os.path.isabs(path):
            return False
        if ":" in path or "\x00" in path or path.startswith(("/", "\\")):
            return False
        return ".." not in path.replace("\\", "/").split("/")

    paths_relative_ok = all(_no_traversal(f.path) for f in patch.files)
    checks.append(Check(
        "paths_are_relative_no_traversal", paths_relative_ok,
        "every path is repository-relative with no .. component"))

    resolved = {}
    if paths_relative_ok:
        resolved = {f.path: _resolve_inside(workspace, f.path) for f in patch.files}
    inside_ok = paths_relative_ok and all(
        v is not None and os.path.isfile(v) for v in resolved.values())
    checks.append(Check(
        "paths_stay_inside_workspace", inside_ok,
        "every path resolves inside the repository workspace and exists"))

    declared_ok = inside_ok and all(
        f.path in request.sources and f.path in request.patchable for f in patch.files)
    checks.append(Check(
        "paths_are_declared_sources_and_patchable", declared_ok,
        "every path is both analysable and declared patchable by the "
        "repository's own manifest (sources=%s, patchable=%s)"
        % (", ".join(request.sources), ", ".join(request.patchable))))

    denied = [f.path for f in patch.files
             if any(bad in f.path.lower().replace("\\", "/") for bad in _DENY_PATH_SUBSTRINGS)]
    checks.append(Check(
        "paths_avoid_denied_files", not denied,
        "no denied file touched" if not denied
        else "denied file(s): %s" % ", ".join(denied)))

    before_ok, before_detail = True, "every hunk's before-text matches the file's current content, exactly once"
    if inside_ok:
        for f in patch.files:
            try:
                with open(resolved[f.path], "r", encoding="utf-8") as handle:
                    source = handle.read()
            except OSError as exc:
                before_ok, before_detail = False, "%s could not be read: %s" % (f.path, exc)
                break
            for hunk in f.hunks:
                if source.count(hunk.before) != 1:
                    before_ok = False
                    before_detail = ("the before-text for a hunk in %s does not appear "
                                     "exactly once in the file as it exists right now"
                                     % f.path)
                    break
            if not before_ok:
                break
    else:
        before_ok, before_detail = False, "skipped - paths were not valid"
    checks.append(Check("before_text_matches_current_source_exactly", before_ok, before_detail))

    dangerous = sorted({construct for f in patch.files for h in f.hunks
                        for construct in _DANGEROUS_CONSTRUCTS if construct in h.after})
    checks.append(Check(
        "after_text_has_no_dangerous_construct", not dangerous,
        "no disallowed construct introduced" if not dangerous
        else "disallowed construct(s): %s" % ", ".join(dangerous)))

    permits_ok, permits_detail = True, "every file is inside this run's permitted scope"
    if intent is not None:
        for f in patch.files:
            allowed, why = intent.permits_file(f.path)
            if not allowed:
                permits_ok, permits_detail = False, why
                break
    checks.append(Check("intent_permits_every_file", permits_ok, permits_detail))

    max_files_ok, max_files_detail = True, "no max_changed_files constraint on this run"
    if intent is not None:
        for constraint in intent.enforced:
            if constraint.kind == "max_changed_files" and isinstance(constraint.value, int):
                if len(patch.files) > constraint.value:
                    max_files_ok = False
                    max_files_detail = ("the instruction limited changes to %d file(s); "
                                        "this patch touches %d"
                                        % (constraint.value, len(patch.files)))
                else:
                    max_files_detail = "within the %d file limit the instruction set" \
                        % constraint.value
    checks.append(Check("intent_max_changed_files_respected", max_files_ok, max_files_detail))

    schema_ok, schema_detail = True, "no no_schema_change constraint on this run"
    if intent is not None and any(c.kind == "no_schema_change" for c in intent.enforced):
        touched = [f.path for f in patch.files if f.path.lower().endswith(".sql")]
        if touched:
            schema_ok, schema_detail = False, (
                "the instruction forbade a schema change; this patch touches %s"
                % ", ".join(touched))
        else:
            schema_detail = "no schema file touched"
    checks.append(Check("no_schema_change_respected", schema_ok, schema_detail))

    spec = patch if all(c.passed for c in checks) else None
    return PatchValidationReport(tuple(checks), spec)
