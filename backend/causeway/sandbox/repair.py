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

Every function below takes an optional `surfaces` override, defaulting to
this module's own REPAIR_SURFACES when omitted. That is the only thing
Milestone 6 added here: a repository loaded through causeway.repository
builds its own surfaces mapping (from its manifest's one declared
repair_surface, `current` read live from the cloned file rather than
trusted from the manifest) and passes it through explicitly - the bundled
demo path above never sees or supplies one, so its behaviour is unchanged.
"""
from __future__ import annotations

import re
from typing import Mapping, Optional

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


def _assignment_pattern(name: str) -> "re.Pattern":
    return re.compile(r'^%s\s*=\s*"(.*)"\s*$' % re.escape(name), re.MULTILINE)


def read_current_from_file(path: str, name: str) -> Optional[str]:
    """Read a top-level `NAME = "value"` assignment from a python source file
    on disk - the same assignment shape causeway.sandbox.fixapply patches.
    Used for a repository's repair surface, where the current value must be
    read from the actual cloned file rather than trusted from its manifest."""
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    matches = _assignment_pattern(name).findall(source)
    return matches[0] if len(matches) == 1 else None


def repair_surface(hypothesis_id: str, target: str,
                   surfaces: Mapping = None) -> Optional[dict]:
    table = REPAIR_SURFACES if surfaces is None else surfaces
    return table.get(hypothesis_id, {}).get(target)


def targets_for(hypothesis_id: str, surfaces: Mapping = None) -> tuple:
    table = REPAIR_SURFACES if surfaces is None else surfaces
    return tuple(sorted(table.get(hypothesis_id, {})))


def current_value(hypothesis_id: str, target: str,
                  surfaces: Mapping = None) -> Optional[str]:
    surface = repair_surface(hypothesis_id, target, surfaces=surfaces)
    return surface["current"]() if surface else None


def is_safe_after(hypothesis_id: str, target: str, after: str,
                  surfaces: Mapping = None) -> bool:
    surface = repair_surface(hypothesis_id, target, surfaces=surfaces)
    if surface is None:
        return False
    return _normalize(after) == _normalize(surface["safe_after"])


def matches_current(hypothesis_id: str, target: str, before: str,
                    surfaces: Mapping = None) -> bool:
    current = current_value(hypothesis_id, target, surfaces=surfaces)
    return current is not None and _normalize(before) == _normalize(current)
