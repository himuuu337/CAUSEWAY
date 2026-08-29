"""The fix-application surface: a narrow, whitelisted registry of exactly the
constants Milestone 5 is allowed to patch in a disposable sandbox copy of the
order-service, plus the one known-safe repair for each.

Deliberately not a general patcher. `target` is a symbolic name resolved
through this dict, never a filesystem path - there is nothing here a proposal
could path-traverse with. And `after` is checked elsewhere (the fix validator)
against `safe_after`, so nothing reaches the patched source file unless it
matches, whitespace aside, the one string this module already knows is safe.
That also means Gemini is never told the answer: it sees `current()`, the
broken value, and has to derive the replacement; the validator is what already
knows it.

Only B has a repair surface. A is never PROVEN in this demo, so it is never
asked for a fix, and there is nothing registered for it to fix.
"""
from __future__ import annotations

from typing import Optional

from causeway.sandbox import service


def _normalize(text: str) -> str:
    return " ".join(str(text).split())


REPAIR_SURFACES = {
    "B": {
        "SCANNING_PREDICATE": {
            "operation_type": "replace_predicate",
            "current": lambda: service.SCANNING_PREDICATE,
            "safe_after": service.INDEXED_PREDICATE,
            "description": (
                "the predicate order_id lookups use against order_audit; "
                "wrapping the column in an expression makes the index "
                "idx_audit_order(order_id) unusable"),
        },
    },
}


def repair_surface(hypothesis_id: str, target: str) -> Optional[dict]:
    return REPAIR_SURFACES.get(hypothesis_id, {}).get(target)


def targets_for(hypothesis_id: str) -> tuple:
    return tuple(sorted(REPAIR_SURFACES.get(hypothesis_id, {})))


def current_value(hypothesis_id: str, target: str) -> Optional[str]:
    surface = repair_surface(hypothesis_id, target)
    return surface["current"]() if surface else None


def is_safe_after(hypothesis_id: str, target: str, after: str) -> bool:
    surface = repair_surface(hypothesis_id, target)
    if surface is None:
        return False
    return _normalize(after) == _normalize(surface["safe_after"])


def matches_current(hypothesis_id: str, target: str, before: str) -> bool:
    current = current_value(hypothesis_id, target)
    return current is not None and _normalize(before) == _normalize(current)
