"""Applying a validated fix to a disposable sandbox copy of the order-service.

The real `causeway/sandbox/service.py` is never opened for writing here. This
module reads its source once (`inspect.getsource`), copies the text into a
throwaway temp directory, substitutes exactly one constant's value in that
copy, and hands back the copy's path. The developer's checkout, and the
process import cache underneath it, are untouched - the sandbox that runs the
patched behaviour is a brand new `python <copy>/service.py` subprocess, never
a reload of the real module.

Substitution is a targeted regex against one `NAME = "..."` assignment, not a
blind string replace: if the target does not appear in the source exactly
once, this refuses rather than guessing.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass

from causeway.sandbox import service

SERVICE_FILENAME = "service.py"


@dataclass(frozen=True)
class AppliedFix:
    workdir: str
    service_path: str
    target: str
    before: str
    after: str

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


def _assignment_pattern(name: str) -> re.Pattern:
    # NAME = "...."   - a top-level string-literal assignment, one line. The
    # captured group is the value alone, without quotes or the variable name,
    # so `before` compares like-for-like with what repair.current_value and a
    # validated FixSpec.operation.before both carry.
    return re.compile(r'^%s\s*=\s*"(.*)"\s*$' % re.escape(name), re.MULTILINE)


def apply(target: str, after: str, workdir: str = None) -> AppliedFix:
    """Patch `target` to `after` in a disposable copy of the sandbox service.

    Callers are expected to have already validated `after` against the known
    safe repair for this target (`causeway.sandbox.repair.is_safe_after`) -
    this function's own job is only to apply an already-approved value
    without ever touching the real source file.
    """
    source = inspect.getsource(service)
    pattern = _assignment_pattern(target)
    matches = pattern.findall(source)
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one assignment to %r in service.py, found %d"
            % (target, len(matches)))
    before_line = matches[0]

    patched_line = "%s = %s" % (target, json.dumps(after))
    patched_source = pattern.sub(patched_line.replace("\\", "\\\\"), source, count=1)

    directory = workdir or tempfile.mkdtemp(prefix="causeway-fix-")
    os.makedirs(directory, exist_ok=True)
    dest = os.path.join(directory, SERVICE_FILENAME)
    with open(dest, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(patched_source)

    return AppliedFix(workdir=directory, service_path=dest, target=target,
                      before=before_line, after=after)
