"""The deterministic experiment planner.

Two jobs. It is the planner when no model is configured, and it is the
fallback for every possible Gemini failure - no key, no network, a timeout,
malformed JSON, or a plan the validator rejects. All of those land in the same
place, because a demo that depends on a remote service is not a demo.

It emits the same shape a model emits and goes through the same validator, so
neither path gets a privilege the other does not have.

When this planner is used, the UI must say so. Claiming AI designed an
experiment that this file designed would be the one dishonest thing Causeway
could do.
"""
from __future__ import annotations

from causeway.planner.schema import PlanRequest

NAME = "deterministic"


class DeterministicPlanner:
    name = NAME
    available = True
    kind = "deterministic"

    def propose(self, request: PlanRequest, schema=None) -> dict:
        target = request.target_hypothesis
        others = [c for c in request.candidate_ids if c != target]
        return {
            "hypothesis_id": target,
            "intervention": {"flag": target, "value": False},
            "fixture_id": request.fixtures[0],
            "expected_signature": {
                "metric": "p95_ms",
                "op": "<=",
                "relative_to": "control",
                "factor": request.recovery_factor,
            },
            "discriminates_between": [target] + others,
            "reasoning_summary": (
                "Measure a healthy control on this machine, reproduce the "
                "incident, then %s %s while holding %s fixed and replay "
                "%s. If %s is causal, p95 should return to within %.1fx of the "
                "control measured beside that phase; if it is not, the failure "
                "will survive its removal."
                % ("rewrite the code at" if request.is_code else "disable",
                   target, ", ".join(others) or "nothing else",
                   request.fixtures[0], target, request.recovery_factor)),
        }
