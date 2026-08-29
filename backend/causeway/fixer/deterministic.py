"""The deterministic fix planner.

Two jobs, the same two jobs causeway/planner/deterministic.py has for
experiments: it is the planner when no model is configured, and it is the
fallback for every possible Gemini failure. It emits the same shape a model
emits and goes through the same validator, so neither path gets a privilege
the other does not have.

It can propose the known-safe repair because causeway.sandbox.repair already
carries it - the same fact the validator checks any proposal against. That is
not a shortcut unique to this planner: it is what "deterministic" means here.
"""
from __future__ import annotations

from causeway.fixer.schema import FixRequest
from causeway.sandbox import repair

NAME = "deterministic"


class DeterministicFixPlanner:
    name = NAME
    available = True
    kind = "deterministic"

    def propose(self, request: FixRequest, schema=None) -> dict:
        target = request.repair_targets[0]
        surface = repair.repair_surface(request.hypothesis_id, target)
        before = request.current_code.get(target, "")
        after = surface["safe_after"]
        mechanism = surface["description"]
        sentence = mechanism[0].upper() + mechanism[1:]
        return {
            "hypothesis_id": request.hypothesis_id,
            "summary": "Restore the index-friendly form of %s" % target,
            "operation": {
                "type": surface["operation_type"],
                "target": target,
                "before": before,
                "after": after,
            },
            "reasoning_summary": (
                "%s. Replacing the wrapped expression with a bare column "
                "comparison lets the existing index be used again." % sentence),
        }
