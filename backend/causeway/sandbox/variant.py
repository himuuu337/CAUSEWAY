"""Source variants: the actuator that makes a causal experiment an edit to
code rather than a flip of a runtime switch.

A variant is a disposable copy of a repository workspace with zero or more
validated counterfactual edits applied to it. The sandbox launches the copy;
the original workspace - and Causeway's own checkout - are never written to.

    original clone  ->  copy  ->  apply N validated edits  ->  run the copy

This is what lets a hypothesis be a place in real source rather than a
symbolic flag. The three states the seven-phase protocol needs map onto it
directly:

    healthy      every testable hypothesis replaced by its counterfactual
    incident     the repository exactly as cloned, nothing applied
    ablated:<id> as cloned, with exactly ONE hypothesis's counterfactual

Every path here is resolved inside the workspace and re-checked after
resolution, so a `file` that walks upward, is absolute, or slips through a
symlink cannot escape - which matters because a proposal that reaches this
module may have been drafted by a model.
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

# Directories never copied into a variant: version-control metadata, caches,
# and anything else that only makes the copy slower.
_SKIP = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".venv",
                               "node_modules", ".pytest_cache", ".mypy_cache")


class VariantRejected(RuntimeError):
    """An edit could not be applied safely. Nothing was written."""


@dataclass(frozen=True)
class SourceEdit:
    """One exact-text replacement inside one repository-relative file.

    `before` must occur exactly once in the file. Not "at least once": an
    ambiguous match means the caller does not actually know which occurrence
    it is changing, and a causal experiment cannot be built on that.
    """

    file: str        # repository-relative, e.g. "db.py"
    before: str
    after: str
    label: str = ""

    def as_dict(self) -> dict:
        return {"file": self.file, "before": self.before,
                "after": self.after, "label": self.label}


@dataclass(frozen=True)
class AppliedEdit:
    file: str
    before: str
    after: str
    label: str
    line: int        # 1-based line where the replacement landed

    def as_dict(self) -> dict:
        return {"file": self.file, "before": self.before, "after": self.after,
                "label": self.label, "line": self.line}


def _force_remove(func, path, exc_info):
    """Copied trees can contain read-only files; clear the bit and retry once
    so a cleanup never silently leaves a workspace behind on Windows."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


@dataclass
class SourceVariant:
    root: str                       # the disposable copy of the workspace
    service_path: Optional[str]     # the entrypoint inside that copy, if any
    applied: Tuple[AppliedEdit, ...]
    workdir: str                    # disposable parent; removed whole

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, onerror=_force_remove)

    def as_dict(self) -> dict:
        return {"applied": [edit.as_dict() for edit in self.applied]}


def resolve_inside(root: str, relative: str) -> str:
    """Resolve a repository-relative path and prove it stayed inside.

    Checked after realpath, not before, so a symlink pointing out of the
    workspace is caught rather than trusted.
    """
    if not isinstance(relative, str) or not relative.strip():
        raise VariantRejected("a source edit must name a file")
    if os.path.isabs(relative) or relative.startswith("\\") or ":" in relative:
        raise VariantRejected("%r must be a repository-relative path" % relative)

    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(base, relative))
    if target != base and not target.startswith(base + os.sep):
        raise VariantRejected("%r resolves outside the repository workspace" % relative)
    if not os.path.isfile(target):
        raise VariantRejected("%r does not exist in the repository" % relative)
    return target


def _apply_edit(root: str, edit: SourceEdit) -> AppliedEdit:
    path = resolve_inside(root, edit.file)
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()

    occurrences = source.count(edit.before)
    if occurrences == 0:
        raise VariantRejected(
            "the text to replace was not found in %s - the file does not say "
            "what the edit expected" % edit.file)
    if occurrences > 1:
        raise VariantRejected(
            "the text to replace appears %d times in %s - an edit must be "
            "unambiguous" % (occurrences, edit.file))

    line = source[:source.index(edit.before)].count("\n") + 1
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(source.replace(edit.before, edit.after, 1))
    return AppliedEdit(edit.file, edit.before, edit.after, edit.label, line)


def materialise(workspace: str, entrypoint: Optional[str],
                edits: Sequence[SourceEdit] = ()) -> SourceVariant:
    """Copy `workspace` and apply `edits` to the copy.

    Raises VariantRejected - having removed the copy - if any edit cannot be
    applied. A partially edited variant is never returned and never run.

    `entrypoint` is None for a variant that will never be launched as a
    service - a standard (manifest-less) repository, where Causeway has no
    reliable way to start or run the code and does not guess one. The copy
    and the edits happen exactly the same either way; only the resolved
    `service_path` is absent.
    """
    workdir = tempfile.mkdtemp(prefix="causeway-variant-")
    root = os.path.join(workdir, "repo")
    try:
        shutil.copytree(workspace, root, ignore=_SKIP, symlinks=False)
        service_path = None
        if entrypoint is not None:
            entrypoint_rel = os.path.relpath(entrypoint, workspace) \
                if os.path.isabs(entrypoint) else entrypoint
            service_path = resolve_inside(root, entrypoint_rel)
        applied = tuple(_apply_edit(root, edit) for edit in edits)
    except BaseException:
        shutil.rmtree(workdir, onerror=_force_remove)
        raise

    return SourceVariant(root=root, service_path=service_path,
                         applied=applied, workdir=workdir)
