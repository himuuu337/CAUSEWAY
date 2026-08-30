"""Deterministic parsing of Python's own traceback text.

No subprocess, no filesystem access beyond the path arithmetic needed to
decide whether a frame is inside a workspace - this module is pure text in,
a dataclass (or None) out, and is exercised directly with plain strings.

Nothing here guesses. A file, a line or a function is reported only when a
real traceback frame named it and that frame resolves inside the workspace
being investigated; otherwise the finding says so plainly rather than
pointing at the nearest thing that looked right.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

MAX_RAW_CHARS = 4000

_CHAIN_SEPARATORS = (
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
)

_TRACEBACK_HEADER = "Traceback (most recent call last):"

# "  File "app.py", line 5, in some_func" - a runtime frame.
_RUNTIME_FRAME = re.compile(r'^  File "(?P<file>.+)", line (?P<line>\d+), in (?P<func>.+)$')
# "  File "app.py", line 5" - a SyntaxError's one frame, no enclosing function.
_SYNTAX_FRAME = re.compile(r'^  File "(?P<file>.+)", line (?P<line>\d+)$')
# The final "ExceptionType: message" line. "SomeError" alone (no colon) is
# also valid Python (e.g. a bare `raise SystemExit`).
_EXCEPTION_LINE = re.compile(r'^(?P<type>[\w.]+)(?::\s?(?P<msg>.*))?$')

_SYNTAX_EXCEPTION_TYPES = ("SyntaxError", "IndentationError", "TabError")


@dataclass(frozen=True)
class TracebackFinding:
    kind: str                        # "runtime" | "syntax"
    exception_type: str
    message: str
    file: Optional[str]              # workspace-relative; None when no frame resolved inside it
    line: Optional[int]
    function: Optional[str]          # always None for a "syntax" finding
    frame_available: bool
    raw: str                         # the parsed segment's own text, bounded

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "exception_type": self.exception_type, "message": self.message,
            "file": self.file, "line": self.line, "function": self.function,
            "frame_available": self.frame_available, "raw": self.raw,
        }


def _last_segment(text: str) -> str:
    """Only the most-recently-raised exception matters - it is the one that
    actually terminated the process. Chained-exception preambles are real
    Python output, not noise, but they describe an exception this run's
    outcome does not hinge on."""
    segment = text
    for separator in _CHAIN_SEPARATORS:
        parts = segment.split(separator)
        segment = parts[-1]
    return segment.strip("\n")


def _resolve_inside(candidate: str, workspace_root: str) -> Optional[str]:
    """The same containment rule causeway.sandbox.variant.resolve_inside
    applies to a source edit: resolved via realpath, compared by prefix, so
    a relative path, a symlink or a differently-cased Windows path cannot
    fool this into claiming a frame is inside the workspace when it is not."""
    base = os.path.realpath(workspace_root)
    try:
        target = os.path.realpath(candidate)
    except (OSError, ValueError):
        return None
    if target == base or target.startswith(base + os.sep):
        return os.path.relpath(target, base).replace(os.sep, "/")
    return None


def _parse_runtime(segment: str, workspace_root: str) -> Optional[TracebackFinding]:
    lines = segment.split("\n")
    if not lines or lines[0].strip() != _TRACEBACK_HEADER:
        return None

    frames: List[Tuple[str, int, str]] = []
    for line in lines[1:]:
        match = _RUNTIME_FRAME.match(line)
        if match:
            frames.append((match.group("file"), int(match.group("line")), match.group("func")))

    # The final non-frame, non-blank, non-indented line is the exception.
    exc_type, message = "", ""
    for line in reversed(lines):
        if not line.strip() or line.startswith("  "):
            continue
        match = _EXCEPTION_LINE.match(line.strip())
        if match:
            exc_type = match.group("type")
            message = match.group("msg") or ""
        break

    if not exc_type:
        return None

    resolved_file, resolved_line, resolved_func = None, None, None
    for file_path, line_no, func_name in reversed(frames):
        relative = _resolve_inside(file_path, workspace_root)
        if relative is not None:
            resolved_file, resolved_line, resolved_func = relative, line_no, func_name
            break

    return TracebackFinding(
        kind="runtime", exception_type=exc_type, message=message,
        file=resolved_file, line=resolved_line, function=resolved_func,
        frame_available=resolved_file is not None, raw=segment[:MAX_RAW_CHARS],
    )


def _parse_syntax(segment: str, workspace_root: str) -> Optional[TracebackFinding]:
    lines = [line for line in segment.split("\n") if line.strip()]
    frame = None
    for line in lines:
        match = _SYNTAX_FRAME.match(line)
        if match:
            frame = (match.group("file"), int(match.group("line")))
            break
    if frame is None:
        return None

    exc_type, message = "", ""
    for line in reversed(lines):
        match = _EXCEPTION_LINE.match(line.strip())
        if match and match.group("type") in _SYNTAX_EXCEPTION_TYPES:
            exc_type = match.group("type")
            message = match.group("msg") or ""
            break
    if not exc_type:
        return None

    resolved_file = _resolve_inside(frame[0], workspace_root)
    return TracebackFinding(
        kind="syntax", exception_type=exc_type, message=message,
        file=resolved_file, line=frame[1] if resolved_file is not None else None,
        function=None, frame_available=resolved_file is not None,
        raw=segment[:MAX_RAW_CHARS],
    )


def parse_traceback(stderr_text: str, workspace_root: str) -> Optional[TracebackFinding]:
    """None when `stderr_text` is not a Python traceback at all - a clean
    run's empty stderr, or some other program output entirely. Never a
    partially-filled finding: either the shape is recognised and every
    field that can be determined is, or nothing is returned."""
    if not stderr_text or not stderr_text.strip():
        return None

    segment = _last_segment(stderr_text)
    if _TRACEBACK_HEADER in segment:
        # A chained SyntaxError still opens with the runtime header before
        # its differently-shaped frame - so try syntax first when the
        # segment's own exception line names one of the syntax types.
        tail = segment.strip().split("\n")[-1]
        if any(tail.startswith(name) for name in _SYNTAX_EXCEPTION_TYPES):
            found = _parse_syntax(segment, workspace_root)
            if found is not None:
                return found
        return _parse_runtime(segment, workspace_root)
    return _parse_syntax(segment, workspace_root)
