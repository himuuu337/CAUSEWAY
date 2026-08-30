"""A small, deterministic window of a finding's own surrounding source.

Every line this module returns is copied verbatim from the exact file text a
detector already read to make its finding - nothing here fabricates a line,
infers one, or reaches back out to disk. This is what lets the interface
show real code around a finding (line numbers, a few lines of real context,
the relevant lines marked) instead of only the matched fragment a detector's
own regex or AST walk happened to capture.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

# A few lines either side is enough to read the finding in place without
# turning a small predicate or a four-line acquire/release pair into a wall
# of code nobody asked to see.
CONTEXT_LINES = 2
# A hard cap independent of `context`, so a detector that ever derives an
# unusually large span (a big function, say) cannot silently dump the whole
# file into an event.
MAX_EXCERPT_LINES = 14


@dataclass(frozen=True)
class SourceLine:
    number: int              # 1-based, matches the file's own line numbering
    text: str                # the file's own text on this line, unmodified
    highlighted: bool        # True for a line the finding itself occupies

    def as_dict(self) -> dict:
        return {"number": self.number, "text": self.text, "highlighted": self.highlighted}


def excerpt_for(source: str, start_line: int, end_line: int,
                context: int = CONTEXT_LINES) -> Tuple[SourceLine, ...]:
    """Lines `start_line`..`end_line` (1-based, inclusive) of `source`, plus
    up to `context` real lines on either side, clamped to the file's actual
    bounds and to MAX_EXCERPT_LINES. Empty for an out-of-range request rather
    than raising - a detector's own line arithmetic is trusted, but this
    function does not assume it is always in bounds."""
    lines = source.splitlines()
    if not lines or start_line < 1 or start_line > len(lines):
        return ()

    end_line = max(start_line, min(end_line, len(lines)))
    lo = max(1, start_line - context)
    hi = min(len(lines), end_line + context)
    if hi - lo + 1 > MAX_EXCERPT_LINES:
        hi = lo + MAX_EXCERPT_LINES - 1

    return tuple(
        SourceLine(number=n, text=lines[n - 1], highlighted=start_line <= n <= end_line)
        for n in range(lo, hi + 1)
    )
