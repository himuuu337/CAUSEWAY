"""A hypothesis about a place in real source code.

This replaces the A/B change identities on the repository-backed path. A
hypothesis is no longer a label attached to a fabricated deploy record; it is
a file, a line, a symbol and the exact text a detector found there, together
with the counterfactual that would be written in its place to test whether it
is causal.

Nothing here decides anything. A hypothesis is a question, and
causeway.verdict answers it from measurements.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional, Tuple

from causeway.analysis.excerpt import SourceLine

# A fixed engineering-insight vocabulary, assigned only by which detector
# produced a finding - never guessed per finding, and never upgraded by
# anything downstream (a planner's prose, a fix's summary). Widening this
# happens by adding a detector's name here, in the same commit that adds the
# detector; a finding from a detector this dict does not name is UNKNOWN,
# honestly, rather than defaulted into a category nothing established.
CATEGORY_BY_DETECTOR = {
    "sql_predicate_index_usability": "DATABASE ISSUE",
    "resource_release_not_guaranteed": "RESOURCE ISSUE",
}
UNKNOWN_CATEGORY = "UNKNOWN"


def line_end_of(line: int, observed: str) -> int:
    """The last line `observed` occupies, counted from `line` itself - the
    single place this arithmetic lives, shared by CodeHypothesis.line_end
    and by a detector that wants the same number before an excerpt is cut."""
    newlines = observed.count("\n")
    if newlines == 0:
        return line
    trailing = observed.endswith("\n")
    return line + newlines - (1 if trailing else 0)


@dataclass(frozen=True)
class CodeHypothesis:
    """One suspicious location in the repository under investigation."""

    file: str                       # repository-relative, e.g. "db.py"
    line: int                       # 1-based, where `observed` starts
    symbol: str                     # the enclosing function or constant
    kind: str                       # "query_predicate"
    observed: str                   # the exact text found in the file
    counterfactual: Optional[str]   # what an ablation would write instead
    evidence: str                   # what the detector saw
    reason: str                     # why that is worth testing
    detector: str                   # which detector produced this
    context: Tuple[str, ...] = field(default_factory=tuple)   # extra facts, display only
    # A small window of the finding's own surrounding source, real line
    # numbers included - never fabricated, always sliced from the exact file
    # text the detector already read. Empty when a detector did not (or
    # could not) build one; the interface then falls back to `observed` alone.
    excerpt: Tuple[SourceLine, ...] = field(default_factory=tuple)

    @property
    def id(self) -> str:
        """Stable, opaque, and derived from the evidence itself.

        Readable enough to say out loud in a demo, and stable across runs
        because it is a function of where the finding is rather than of the
        order findings happened to be made in. It is never A or B.
        """
        stem = "%s:%s:%s" % (self.file, self.symbol, self.kind)
        digest = hashlib.sha256(
            ("%s:%d:%s" % (stem, self.line, self.observed)).encode("utf-8")
        ).hexdigest()[:6]
        return "%s@%s" % (stem, digest)

    @property
    def label(self) -> str:
        """What a human reads: db.py:84 lookup_order()."""
        return "%s:%d %s()" % (self.file, self.line, self.symbol)

    @property
    def testable(self) -> bool:
        """A hypothesis can only be ablated if the detector could derive a
        safe counterfactual for it. One that cannot is still reported - it is
        evidence - but it is never claimed to have been tested."""
        return bool(self.counterfactual) and self.counterfactual != self.observed

    @property
    def category(self) -> str:
        """The engineering-insight category this finding's own detector
        represents - a property of which detector found it, not a judgement
        made about this particular finding. See CATEGORY_BY_DETECTOR."""
        return CATEGORY_BY_DETECTOR.get(self.detector, UNKNOWN_CATEGORY)

    @property
    def line_end(self) -> int:
        """The last line `observed` actually occupies, counted from `line`
        itself - never asserted beyond what the observed text's own line
        count supports. Equal to `line` for a single-line finding."""
        return line_end_of(self.line, self.observed)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "file": self.file,
            "line": self.line, "line_end": self.line_end, "symbol": self.symbol,
            "kind": self.kind, "category": self.category,
            "observed": self.observed, "counterfactual": self.counterfactual,
            "evidence": self.evidence, "reason": self.reason,
            "detector": self.detector, "testable": self.testable,
            "context": list(self.context),
            "excerpt": [line.as_dict() for line in self.excerpt],
        }
