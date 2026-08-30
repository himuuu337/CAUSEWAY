"""Real, safe, timeout-bounded execution of a Python entrypoint - the one
piece of runtime evidence the manifest-less "standard repository" path did
not have before this module: every other check on that path is static
(source reading, a syntax check) because nothing there ever runs the
repository's own code. This module runs it once, in an already-disposable
copy the caller provides, and reports exactly what happened - a clean exit,
a captured exception at a real file and line, or an honest "this looks like
a long-running service, not a script" when nothing conclusive was observed.

Explicitly a best-effort subprocess sandbox, not OS-level isolation: argv
only, a disposable copy, a hard wall-clock timeout, and a stripped
environment (see causeway.languages._run_once.restricted_env) are what this
gives a caller. It protects against an accidentally slow or hung script,
not a deliberately adversarial one - the same honesty this codebase already
applies to what a syntax check does and does not prove
(causeway/languages/adapters.py's PythonAdapter.verify() docstring: "never
executes the module"; this module is the one place that changes, and only
for a target this narrow).

Python only. Every other language's verification is unchanged.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

from causeway.languages import _run_once
from causeway.languages.python_traceback import TracebackFinding, parse_traceback
from causeway.sandbox.variant import VariantRejected, resolve_inside

STDOUT_TAIL_CHARS = 2000
STDERR_TAIL_CHARS = 2000


@dataclass(frozen=True)
class RuntimeObservation:
    attempted: bool
    entrypoint: str                      # workspace-relative; "" when attempted is False
    exited_cleanly: bool
    timed_out: bool
    crashed: bool                        # a real, parsed exception was captured
    traceback: Optional[TracebackFinding]
    stdout_tail: str
    stderr_tail: str
    duration_s: float
    note: str                            # always a plain-English sentence

    def as_dict(self) -> dict:
        return {
            "attempted": self.attempted, "entrypoint": self.entrypoint,
            "exited_cleanly": self.exited_cleanly, "timed_out": self.timed_out,
            "crashed": self.crashed,
            "traceback": self.traceback.as_dict() if self.traceback is not None else None,
            "stdout_tail": self.stdout_tail, "stderr_tail": self.stderr_tail,
            "duration_s": round(self.duration_s, 3), "note": self.note,
        }


_NOT_ATTEMPTED = RuntimeObservation(
    attempted=False, entrypoint="", exited_cleanly=False, timed_out=False, crashed=False,
    traceback=None, stdout_tail="", stderr_tail="", duration_s=0.0,
    note="no unambiguous Python entrypoint was identified - execution was not attempted")


def select_entrypoint(entrypoint: str, sources: Sequence[str], primary_language: str) -> str:
    """"" unless there is exactly one unambiguous target to run. Never
    guesses among several candidate files: a repository with two or more
    Python sources and no recognised entrypoint name is left alone rather
    than picked for."""
    if primary_language != "python":
        return ""
    if entrypoint and entrypoint in sources:
        return entrypoint
    python_sources = [s for s in sources if s.endswith(".py")]
    if len(python_sources) == 1:
        return python_sources[0]
    return ""


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[-limit:]


def observe(root: str, entrypoint: str, timeout: float = None) -> RuntimeObservation:
    """Run `entrypoint` once inside `root` - an already-disposable copy the
    caller materialised; this function never copies anything itself and
    never touches a real clone or checkout."""
    if not entrypoint:
        return _NOT_ATTEMPTED

    try:
        absolute_path = resolve_inside(root, entrypoint)
    except VariantRejected as exc:
        return RuntimeObservation(
            attempted=False, entrypoint=entrypoint, exited_cleanly=False, timed_out=False,
            crashed=False, traceback=None, stdout_tail="", stderr_tail="", duration_s=0.0,
            note="the entrypoint could not be resolved safely: %s" % exc)

    run = _run_once.run_python_script([sys.executable, absolute_path],
                                      cwd=os.path.dirname(absolute_path), timeout=timeout)

    if run.timed_out:
        if not run.stdout and not run.stderr:
            return RuntimeObservation(
                attempted=True, entrypoint=entrypoint, exited_cleanly=False, timed_out=True,
                crashed=False, traceback=None, stdout_tail="", stderr_tail="",
                duration_s=run.duration_s,
                note=("did not exit within %.0fs - this may be a long-running service "
                     "(for example one that calls serve_forever()) rather than a script; "
                     "execution could not be verified" % run.duration_s))
        finding = parse_traceback(run.stderr, root)
        note = (("crashed during startup, before it would otherwise have run past the "
                "timeout: %s" % finding.exception_type) if finding is not None else
               ("did not exit within %.0fs, but produced output before the timeout; no "
                "recognisable Python traceback was in it" % run.duration_s))
        return RuntimeObservation(
            attempted=True, entrypoint=entrypoint, exited_cleanly=False, timed_out=True,
            crashed=finding is not None, traceback=finding,
            stdout_tail=_tail(run.stdout, STDOUT_TAIL_CHARS),
            stderr_tail=_tail(run.stderr, STDERR_TAIL_CHARS),
            duration_s=run.duration_s, note=note)

    if run.ok:
        return RuntimeObservation(
            attempted=True, entrypoint=entrypoint, exited_cleanly=True, timed_out=False,
            crashed=False, traceback=None,
            stdout_tail=_tail(run.stdout, STDOUT_TAIL_CHARS),
            stderr_tail=_tail(run.stderr, STDERR_TAIL_CHARS), duration_s=run.duration_s,
            note="ran to completion in %.2fs with exit code 0" % run.duration_s)

    finding = parse_traceback(run.stderr, root)
    note = (("crashed with %s at %s" % (finding.exception_type,
                                        ("%s:%s" % (finding.file, finding.line))
                                        if finding.frame_available else "an unresolved location"))
           if finding is not None else
           ("exited with code %s; stderr did not match a recognisable Python traceback"
            % run.returncode))
    return RuntimeObservation(
        attempted=True, entrypoint=entrypoint, exited_cleanly=False, timed_out=False,
        crashed=finding is not None, traceback=finding,
        stdout_tail=_tail(run.stdout, STDOUT_TAIL_CHARS),
        stderr_tail=_tail(run.stderr, STDERR_TAIL_CHARS),
        duration_s=run.duration_s, note=note)


def summarise_for_prompt(observation: RuntimeObservation) -> str:
    """One plain-text block for a Gemini prompt - see causeway/patch/gemini.py."""
    if not observation.attempted:
        return ""
    lines = ["entrypoint: %s" % observation.entrypoint, observation.note]
    if observation.traceback is not None:
        t = observation.traceback
        location = ("%s:%s in %s()" % (t.file, t.line, t.function) if t.function
                   else "%s:%s" % (t.file, t.line)) if t.frame_available else \
                  "exact source location unavailable"
        lines.append("%s: %s (%s)" % (t.exception_type, t.message, location))
    elif observation.stderr_tail:
        lines.append("stderr: %s" % observation.stderr_tail)
    return "\n".join(lines)


def _identity(observation: RuntimeObservation):
    t = observation.traceback
    if t is None:
        return None
    return (t.exception_type, t.file, t.line)


def compare(before: RuntimeObservation, after: RuntimeObservation) -> Optional[bool]:
    """None when there is nothing conclusive to compare - `before` was never
    attempted, or `before` did not crash (nothing was resolved, so nothing
    is reported either way). True only when `before` crashed and `after`'s
    crash - if any - is a genuinely different (exception, file, line); False
    when the identical triple recurs. Never a fuzzy message comparison."""
    if not before.attempted or not before.crashed:
        return None
    if after.exited_cleanly:
        return True
    before_identity = _identity(before)
    after_identity = _identity(after)
    if after_identity is None:
        # after neither crashed with a parsed traceback nor exited cleanly
        # (e.g. it also timed out with no output) - genuinely inconclusive.
        return None
    return after_identity != before_identity
