"""The FixSpec a fix planner returns, and the JSON schema it must satisfy.

A fix designer - Gemini, or the deterministic fallback - is asked only after
a hypothesis is deterministically PROVEN, and returns one of these and
nothing else. It is given the proven hypothesis, the current (broken) value
at one named, whitelisted repair surface, and the causal mechanism already
established - never a measurement that has not happened yet, and never the
fix verification's own result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional, Tuple

# Re-exported rather than duplicated: a fix proposal is rejected by the same
# rule an experiment proposal is - no field the engine reads may carry a
# conclusion, and Check/ProviderUnavailable are generic enough to reuse as-is.
from causeway.planner.schema import Check, ProviderUnavailable, VERDICT_KEYS
from causeway.planner.schema import VERDICT_TOKENS as _CAUSAL_VERDICT_TOKENS

__all__ = ["ALLOWED_OPERATION_TYPES", "FIX_SCHEMA", "FixRequest", "FixOperation",
          "FixSpec", "Check", "ProviderUnavailable", "VERDICT_KEYS", "VERDICT_TOKENS"]

# The causal verdict words (PROVEN/REFUTED/SUPPORTED/UNRESOLVED) plus the fix
# loop's own words (VERIFIED/FAILED) - a fix proposal must not claim either.
VERDICT_TOKENS = _CAUSAL_VERDICT_TOKENS + (
    "verified", "failed", "fix works", "fix worked",
    "resolves the incident", "resolved the incident")

# MVP scope: exactly one kind of repair is representable at all. Widening this
# is future work, not something a proposal can talk its way into.
ALLOWED_OPERATION_TYPES = ("replace_predicate",)

FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "hypothesis_id": {"type": "string"},
        "summary": {"type": "string"},
        "operation": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "target": {"type": "string"},
                "before": {"type": "string"},
                "after": {"type": "string"},
            },
            "required": ["type", "target", "before", "after"],
        },
        "reasoning_summary": {"type": "string"},
    },
    "required": ["hypothesis_id", "summary", "operation", "reasoning_summary"],
}


@dataclass(frozen=True)
class FixRequest:
    """Everything a fix planner is allowed to see. Note what is absent: any
    measurement from the fix verification, because none has happened yet."""

    hypothesis_id: str
    candidate: Mapping[str, Any]         # branch/summary/lines/files - no numbers
    causal_verdict: str                  # must be "PROVEN" - set by code, not asked
    causal_reason: str                   # the one-line reason causeway.verdict gave
    repair_targets: Tuple[str, ...]      # symbolic target names available to patch
    current_code: Mapping[str, str]      # target -> its current (broken) value
    mechanism: str                       # why this repair surface causes the incident
    # None for the bundled demo (causeway.sandbox.repair.REPAIR_SURFACES).
    # Set only when this request was built for a repository loaded through
    # causeway.repository - its own manifest-declared repair surface,
    # never trusted, always re-validated the same way the bundled one is.
    surfaces: Optional[Mapping[str, Mapping[str, dict]]] = None


@dataclass(frozen=True)
class FixOperation:
    type: str
    target: str
    before: str
    after: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FixSpec:
    hypothesis_id: str
    summary: str
    operation: FixOperation
    # Presentation only. Quoted on screen, never read by the engine.
    reasoning_summary: str

    def as_dict(self) -> dict:
        data = asdict(self)
        data["operation"] = self.operation.as_dict()
        return data
