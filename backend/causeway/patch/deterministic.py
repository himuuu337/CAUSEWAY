"""The deterministic requested-change planner.

Two jobs, the same two jobs every deterministic planner in this codebase has:
it runs when no model is configured, and it is the fallback for every
possible Gemini failure. Unlike causeway.fixer.deterministic, there is no
single whitelisted repair surface to fall back on here - a requested change
can in principle ask for anything - so this planner is deliberately narrow
and says so. It recognises one shape of instruction (rejecting non-positive
quantities on the demo repository's order-creation endpoint) by finding the
exact anchor text its patch would replace; if that anchor is not present
verbatim, it raises ProviderUnavailable rather than emitting a patch that
would just fail the validator anyway. That is honest degradation, not a
second hidden answer: the SAME text this planner proposes is still checked
by causeway.patch.validator, exactly as anything Gemini proposes is.
"""
from __future__ import annotations

import re

from causeway.patch.schema import PatchRequest, ProviderUnavailable

NAME = "deterministic"

_QUANTITY_WORDS = ("quantity", "qty")
_REJECT_WORDS = ("reject", "refuse", "disallow", "forbid", "validate", "require",
                "must be positive", "should not accept", "prevent")

# The exact text this planner knows how to extend safely: the quantity-type
# check already present in the demo repository's POST /orders handler.
_ANCHOR = (
    '            if not isinstance(quantity, int) or isinstance(quantity, bool):\n'
    '                return self._json(400, {"error": "quantity must be an integer"})\n'
)
_ANCHOR_WITH_GUARD = _ANCHOR + (
    '            if quantity <= 0:\n'
    '                return self._json(400, {"error": "quantity must be a positive '
    'integer"})\n'
)


def _looks_like_the_supported_scenario(instruction: str) -> bool:
    lowered = instruction.lower()
    return (any(w in lowered for w in _QUANTITY_WORDS)
            and any(w in lowered for w in _REJECT_WORDS))


class DeterministicPatchPlanner:
    name = NAME
    available = True
    kind = "deterministic"

    def propose(self, request: PatchRequest, schema=None) -> dict:
        if not _looks_like_the_supported_scenario(request.instruction):
            raise ProviderUnavailable(
                "the deterministic fallback only recognises requests to reject "
                "non-positive order quantities; %r does not match"
                % request.instruction)

        target = "app.py"
        current = request.file_contents.get(target, "")
        if current.count(_ANCHOR) != 1:
            raise ProviderUnavailable(
                "the deterministic fallback's anchor text was not found exactly "
                "once in %s - the file no longer matches what this narrow "
                "fallback knows how to extend" % target)

        return {
            "summary": "Reject orders with a non-positive quantity",
            "files": [{
                "path": target,
                "hunks": [{"before": _ANCHOR, "after": _ANCHOR_WITH_GUARD}],
            }],
            "reasoning_summary": (
                "Added a guard immediately after the existing type check: a "
                "quantity of zero or less is rejected with 400 before an order "
                "is ever written."),
        }
