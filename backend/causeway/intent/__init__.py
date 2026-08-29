"""User intent: what the person asked Causeway to do.

    instruction (+ an optional mode chosen in the interface)
      -> IntentSpec
      -> deterministic enforcement of the constraints that can be enforced
      -> advisory record of the ones that cannot

Gemini may interpret the words. It may not decide the goal, and it may not
relax a constraint: enforcement lives in deterministic code, on the far side
of the validator, exactly like the experiment plan's does.
"""
from __future__ import annotations

from causeway.intent import deterministic
from causeway.intent.schema import (DIAGNOSE_AND_FIX, DIAGNOSE_ONLY, MODES,
                                    NEEDS_CLARIFICATION, REQUESTED_CHANGE,
                                    Constraint, IntentSpec)

__all__ = ["IntentSpec", "Constraint", "parse", "default_intent", "MODES",
           "DIAGNOSE_ONLY", "DIAGNOSE_AND_FIX", "REQUESTED_CHANGE",
           "NEEDS_CLARIFICATION"]

DEFAULT_SOURCE = "default"


def default_intent() -> IntentSpec:
    """What a run means when no instruction was given at all.

    Not the same thing as an empty answer to a question: nobody was asked.
    The safe reading of "investigate this repository" is diagnose it and
    change nothing, so that is what an absent instruction means, and the
    interface is told the intent came from this default rather than from the
    user.
    """
    return IntentSpec(
        raw_instruction="", mode=DIAGNOSE_ONLY,
        goal="diagnose the incident and change nothing",
        source=DEFAULT_SOURCE)


def parse(instruction: str, requested_mode: str = None, provider=None) -> IntentSpec:
    """Turn an instruction into an IntentSpec.

    `provider` is where a Gemini intent parser will be handed in; until one is
    configured this is the deterministic reading, and the interface is told
    which it was via IntentSpec.source.
    """
    return deterministic.parse(instruction, requested_mode)
