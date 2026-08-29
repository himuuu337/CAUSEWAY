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

    def as_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "file": self.file,
            "line": self.line, "symbol": self.symbol, "kind": self.kind,
            "observed": self.observed, "counterfactual": self.counterfactual,
            "evidence": self.evidence, "reason": self.reason,
            "detector": self.detector, "testable": self.testable,
            "context": list(self.context),
        }
