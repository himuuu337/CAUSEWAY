"""Turning one measured phase into the events an interface can render.

Shared by both investigation paths so they judge identically. Imports
causeway.verdict and nothing else: the arithmetic here is the engine's, and
this module only decides how to say it.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from causeway import verdict


def phase_state(value: float, before: float, after: float) -> dict:
    """What a closed bracket says about the phase it surrounds.

    Every word returned here comes from causeway.verdict's own predicates -
    this function chooses none of them.
    """
    control = verdict.local_control(before, after)
    stable = verdict.controls_agree(before, after)
    if not stable:
        state = "unstable"
    elif verdict.failure_present(value, control):
        state = "broken"
    elif verdict.recovered(value, control):
        state = "healthy"
    else:
        state = "inconclusive"
    return {
        "state": state,
        "local_control_ms": round(control, 3),
        "ratio": round(value / control, 2) if control else None,
        "controls_agree": stable,
        "drift": round(verdict.drift(before, after), 3),
    }


def judged_event(results: Sequence, phase: str, brackets: Mapping,
                 metric: str, hypothesis_id: str, event_type: str) -> dict:
    """The `*_phase_judged` event for a phase whose bracket has just closed."""
    before, after = brackets[phase]
    values = {r.spec.phase: r.observed[metric] for r in results}
    return dict({"type": event_type, "hypothesis": hypothesis_id,
                 "phase": phase, "p95_ms": values[phase]},
                **phase_state(values[phase], values[before], values[after]))
