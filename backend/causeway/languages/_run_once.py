"""One safe way to run a Python entrypoint to completion (or timeout) and
capture what it actually did - stdout, stderr, exit code, wall-clock time.

A deliberate sibling to `_toolrun.run`, not an extension of it.
`_toolrun.run` merges stdout+stderr and discards everything on a timeout -
a contract every language adapter's syntax check already depends on. A
captured traceback needs stderr kept separate (interleaving it with a
script's own stdout prints would corrupt the parse) and needs whatever
partial output `subprocess.TimeoutExpired` still carries, because a crash
during import can happen milliseconds before a process would otherwise have
blocked forever - discarding that would turn a real, captured crash into a
false "timed out, nothing learned".

Argv-only, always - never a shell string, and never the repository's own
declared commands. `restricted_env` is deliberately an allowlist: only the
handful of variables a Python interpreter needs to start at all are copied
forward, so a secret in this process's own environment (GEMINI_API_KEY,
CAUSEWAY_GEMINI_KEY, or anything else) is excluded by construction rather
than by pattern-matching a name and hoping the list of names is complete.

This is a best-effort subprocess sandbox, not OS-level isolation: it
protects against a hung or accidentally slow script, not a deliberately
adversarial one. Nothing here claims otherwise - see
causeway/languages/python_runtime.py's own module docstring for where that
limitation is stated to a caller.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, Sequence

DEFAULT_TIMEOUT_SECONDS = 8.0
TIMEOUT_ENV_VAR = "CAUSEWAY_RUNTIME_TIMEOUT_SECONDS"
MIN_TIMEOUT = 2.0
MAX_TIMEOUT = 30.0

# Only what a Python interpreter needs to locate itself, find a temp
# directory, and start - nothing else survives into the child process.
_ALLOWED_VARS = ("PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "COMSPEC")


def timeout_from_env() -> float:
    """CAUSEWAY_RUNTIME_TIMEOUT_SECONDS, defaulting to 8s and clamped to
    [MIN_TIMEOUT, MAX_TIMEOUT]. Missing, empty, non-numeric, or NaN all fall
    back to the default rather than raising - mirrors
    causeway.patch.gemini.timeout_from_env exactly, for the same reason: a
    malformed environment variable must never be the reason a run cannot
    start."""
    raw = os.environ.get(TIMEOUT_ENV_VAR)
    if raw is None or not raw.strip():
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    if value != value:                          # NaN
        return DEFAULT_TIMEOUT_SECONDS
    return max(MIN_TIMEOUT, min(MAX_TIMEOUT, value))


def restricted_env() -> dict:
    """A minimal environment for a child process that is about to run
    untrusted repository code. An allowlist, not a blocklist: everything
    this process's own environment carries - API keys, tokens, data paths -
    is absent unless its name is in _ALLOWED_VARS."""
    env = {name: os.environ[name] for name in _ALLOWED_VARS if name in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@dataclass(frozen=True)
class ScriptRun:
    ok: bool                         # True only on a clean, non-timed-out exit 0
    timed_out: bool
    returncode: Optional[int]        # None only when timed_out
    stdout: str
    stderr: str
    duration_s: float

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "timed_out": self.timed_out, "returncode": self.returncode,
            "stdout": self.stdout, "stderr": self.stderr,
            "duration_s": round(self.duration_s, 3),
        }


def run_python_script(argv: Sequence[str], cwd: str, timeout: float = None,
                      env: Optional[dict] = None) -> ScriptRun:
    """Run one Python entrypoint, argv-only, never a shell. Never raises:
    a missing interpreter, a timeout, or any other OSError all become a
    ScriptRun the caller can inspect, exactly like _toolrun.run's contract
    for its own callers - just with stdout and stderr kept apart, and
    partial output kept on a timeout instead of discarded."""
    timeout = timeout_from_env() if timeout is None else timeout
    env = restricted_env() if env is None else env
    started = time.monotonic()
    try:
        result = subprocess.run(
            list(argv), cwd=cwd, timeout=timeout, shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, check=False)
        duration = time.monotonic() - started
        return ScriptRun(ok=result.returncode == 0, timed_out=False,
                         returncode=result.returncode,
                         stdout=result.stdout or "", stderr=result.stderr or "",
                         duration_s=duration)
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(
            "utf-8", errors="replace") if exc.stdout else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(
            "utf-8", errors="replace") if exc.stderr else ""
        return ScriptRun(ok=False, timed_out=True, returncode=None,
                         stdout=stdout, stderr=stderr, duration_s=duration)
    except FileNotFoundError:
        return ScriptRun(ok=False, timed_out=False, returncode=None, stdout="",
                         stderr="%s is not available on this machine" % argv[0],
                         duration_s=time.monotonic() - started)
    except OSError as exc:
        return ScriptRun(ok=False, timed_out=False, returncode=None, stdout="",
                         stderr="%s could not be run: %s" % (argv[0], exc),
                         duration_s=time.monotonic() - started)
