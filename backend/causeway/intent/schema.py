"""What the user asked for, as a structure the rest of the system can enforce.

The instruction the user types is the goal. It is never rewritten, never
"improved", and never replaced by what a model would have preferred. Gemini
may decide HOW to satisfy it; it may not decide WHAT was asked.

The distinction that keeps this honest is between constraints Causeway can
mechanically enforce and constraints it can only record. "Only modify db.py"
is checked before a patch is applied. "Keep it maintainable" is not checkable
and is shown to the human as advisory - claiming to have enforced it would be
the same class of dishonesty as labelling a fallback "Gemini".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence, Tuple

DIAGNOSE_ONLY = "diagnose_only"
DIAGNOSE_AND_FIX = "diagnose_and_fix"
REQUESTED_CHANGE = "requested_change"
NEEDS_CLARIFICATION = "needs_clarification"

MODES = (DIAGNOSE_ONLY, DIAGNOSE_AND_FIX, REQUESTED_CHANGE)

# Constraint kinds deterministic code can actually check.
ENFORCEABLE = ("diagnose_only", "only_modify", "do_not_modify",
               "no_new_dependencies", "no_schema_change", "max_changed_files")


@dataclass(frozen=True)
class Constraint:
    kind: str                 # an ENFORCEABLE kind, or "advisory"
    value: Any = None
    source: str = ""          # the phrase in the instruction it came from

    @property
    def enforceable(self) -> bool:
        return self.kind in ENFORCEABLE

    def as_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value, "source": self.source,
                "enforceable": self.enforceable}


@dataclass(frozen=True)
class IntentSpec:
    raw_instruction: str
    mode: str
    goal: str
    constraints: Tuple[Constraint, ...] = ()
    allowed_scope: Tuple[str, ...] = ()      # files a change may touch
    prohibited_scope: Tuple[str, ...] = ()   # files a change may not touch
    question: str = ""                       # set when mode is NEEDS_CLARIFICATION
    source: str = "deterministic"            # who parsed it

    # -- what the rest of the system asks it ------------------------------
    @property
    def allows_fix(self) -> bool:
        """May a persistent repair be proposed and applied at all?

        A diagnostic intervention is not a fix: an experiment edits a
        disposable copy to establish causality and throws it away. A fix is a
        change the human is being asked to keep. DIAGNOSE_ONLY permits the
        first and forbids the second.
        """
        if self.mode != DIAGNOSE_AND_FIX:
            return False
        return not any(c.kind == "diagnose_only" for c in self.constraints)

    @property
    def no_fix_reason(self) -> str:
        if self.mode == DIAGNOSE_ONLY:
            return ("the instruction asked for diagnosis only, so no persistent "
                    "fix was generated or applied")
        if self.mode == REQUESTED_CHANGE:
            return "this run is a requested change, not an incident repair"
        if self.mode == NEEDS_CLARIFICATION:
            return "the instruction was ambiguous"
        for constraint in self.constraints:
            if constraint.kind == "diagnose_only":
                return "the instruction forbade modifying anything: %r" % constraint.source
        return ""

    @property
    def enforced(self) -> Tuple[Constraint, ...]:
        return tuple(c for c in self.constraints if c.enforceable)

    @property
    def advisory(self) -> Tuple[Constraint, ...]:
        return tuple(c for c in self.constraints if not c.enforceable)

    def permits_file(self, relative: str) -> Tuple[bool, str]:
        """May a patch touch this file, given the enforced constraints?"""
        for constraint in self.enforced:
            if constraint.kind == "only_modify":
                allowed = [v.lower() for v in (constraint.value or ())]
                if allowed and relative.lower() not in allowed:
                    return False, ("the instruction restricted changes to %s"
                                   % ", ".join(constraint.value))
            if constraint.kind == "do_not_modify":
                blocked = [v.lower() for v in (constraint.value or ())]
                if relative.lower() in blocked:
                    return False, ("the instruction forbade changes to %s"
                                   % ", ".join(constraint.value))
        if self.allowed_scope and relative not in self.allowed_scope:
            return False, "%s is outside the scope this run may modify" % relative
        if relative in self.prohibited_scope:
            return False, "%s is outside the scope this run may modify" % relative
        return True, ""

    def as_dict(self) -> dict:
        return {
            "raw_instruction": self.raw_instruction, "mode": self.mode,
            "goal": self.goal, "question": self.question, "source": self.source,
            "allows_fix": self.allows_fix, "no_fix_reason": self.no_fix_reason,
            "constraints": [c.as_dict() for c in self.constraints],
            "enforced": [c.as_dict() for c in self.enforced],
            "advisory": [c.as_dict() for c in self.advisory],
            "allowed_scope": list(self.allowed_scope),
            "prohibited_scope": list(self.prohibited_scope),
        }
