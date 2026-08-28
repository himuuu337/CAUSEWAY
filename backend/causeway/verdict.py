"""THE VERDICT ENGINE.

This module decides whether a candidate change is PROVEN, REFUTED, SUPPORTED
or UNRESOLVED, and it decides it from measured numbers alone.

    AI PROPOSES.  CODE VALIDATES.  SYSTEM EXPERIMENTS.  MEASUREMENTS DECIDE.

This file is the last clause. A language model may propose a hypothesis and
design an experiment; it may never reach this module. That is enforced
structurally, not by convention: tests/test_no_model_in_verdict.py walks this
module's import graph and fails the build if anything model-shaped, networked
or planner-shaped becomes reachable from it. The only imports here are the
standard library and causeway.measurement.

Two design rules earn their place:

1. NO STORED BASELINE. Every judgement is a ratio against a healthy control
   measured during the same run. An absolute millisecond threshold captured
   earlier is not a control - it is a memory of a machine that no longer
   exists. Battery state, CPU contention, an antivirus scan or a different
   laptop can move every number here by an order of magnitude while leaving
   the causal structure completely intact.

2. CONTROLS ARE INTERLEAVED, NOT BRACKETING THE WHOLE RUN. The protocol is

       control-1  reproduce  control-2  ablate  control-3  restore  control-4

   and each phase is judged only against the two controls beside it. A run
   long enough to reproduce an incident three times is long enough for a
   laptop to genuinely change speed - thermals, a scanner waking, the page
   cache filling. Charging that drift against every phase at once throws away
   good experiments. The machine is allowed to move over the run; what it may
   not do is move between a phase and the controls either side of it.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Mapping, Sequence, Tuple

from causeway import measurement

# ------------------------------------------------------------------ protocol

PHASE_CONTROL_1 = "control-1"
PHASE_REPRODUCE = "reproduce"
PHASE_CONTROL_2 = "control-2"
PHASE_ABLATE = "ablate"
PHASE_CONTROL_3 = "control-3"
PHASE_RESTORE = "restore"
PHASE_CONTROL_4 = "control-4"

PHASES = (PHASE_CONTROL_1, PHASE_REPRODUCE, PHASE_CONTROL_2, PHASE_ABLATE,
          PHASE_CONTROL_3, PHASE_RESTORE, PHASE_CONTROL_4)

CONTROL_PHASES = (PHASE_CONTROL_1, PHASE_CONTROL_2,
                  PHASE_CONTROL_3, PHASE_CONTROL_4)

# The phases that carry evidence. Every one of them is bracketed by controls.
EVIDENCE_PHASES = (PHASE_REPRODUCE, PHASE_ABLATE, PHASE_RESTORE)

# Which controls judge which phase. This mapping IS the protocol.
BRACKETS = {
    PHASE_REPRODUCE: (PHASE_CONTROL_1, PHASE_CONTROL_2),
    PHASE_ABLATE: (PHASE_CONTROL_2, PHASE_CONTROL_3),
    PHASE_RESTORE: (PHASE_CONTROL_3, PHASE_CONTROL_4),
}

# ------------------------------------------------------------------ verdicts

PROVEN = "PROVEN"
REFUTED = "REFUTED"
SUPPORTED = "SUPPORTED"
UNRESOLVED = "UNRESOLVED"

# ---------------------------------------------------------------- thresholds

# How much slower than the local control counts as "the failure is present".
FAILURE_FACTOR = 4.0
# How close to the local control counts as "the failure is gone".
RECOVERY_FACTOR = 2.5
# A noise floor, applied in BOTH directions. Two numbers that differ by less
# than this are not different, whatever their ratio says: 2 ms against 6 ms is
# a 3x ratio and no information at all. Applying a floor to only one side is
# how a few milliseconds of jitter in a 25 ms control becomes a finding.
MIN_DELTA_MS = 5.0
# How far the two controls bracketing a single phase may disagree before that
# phase is unjudgeable. Applied across ONE phase, not the whole run.
LOCAL_DRIFT_LIMIT = 3.0

METRIC = "p95_ms"


@dataclass(frozen=True)
class PhaseSpec:
    """What to do to the system for one phase. A planner may choose the
    hypothesis and the fixture; the phases themselves are built here."""

    hypothesis_id: str
    phase: str
    flags: Mapping[str, bool]
    fixture_id: str
    expected: Mapping[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class PhaseResult:
    """What the system did. Only the engine produces one of these."""

    spec: PhaseSpec
    observed: Mapping[str, float]
    passed: bool = True
    detail: Tuple[str, ...] = ()


# ----------------------------------------------------------------- decisions

def failure_present(value_ms: float, control_ms: float) -> bool:
    return (value_ms >= FAILURE_FACTOR * control_ms
            and value_ms >= control_ms + MIN_DELTA_MS)


def recovered(value_ms: float, control_ms: float) -> bool:
    """Back to the control, either as a ratio or by an absolute margin too
    small to mean anything. The second clause mirrors the noise floor in
    failure_present."""
    return (value_ms <= RECOVERY_FACTOR * control_ms
            or value_ms <= control_ms + MIN_DELTA_MS)


def drift(first_ms: float, second_ms: float) -> float:
    """How far the machine moved between two controls, as a ratio.

    1.0 means it held still. Differences below the noise floor report as 1.0.
    """
    low, high = sorted((abs(first_ms), abs(second_ms)))
    if high - low <= MIN_DELTA_MS:
        return 1.0
    if low <= 0:
        return float("inf")
    return high / low


def local_control(before_ms: float, after_ms: float) -> float:
    """The control a phase is judged against: the median of the two measured
    immediately before and after it."""
    return statistics.median((before_ms, after_ms))


def controls_agree(before_ms: float, after_ms: float) -> bool:
    """Did the machine hold still across this one phase?"""
    if before_ms <= 0 or after_ms <= 0:
        return False
    return drift(before_ms, after_ms) <= LOCAL_DRIFT_LIMIT


def signature_present(control_ms: float) -> dict:
    return {METRIC: {"op": ">=", "value": round(
        max(FAILURE_FACTOR * control_ms, control_ms + MIN_DELTA_MS), 3)}}


def signature_absent(control_ms: float) -> dict:
    return {METRIC: {"op": "<=", "value": round(
        max(RECOVERY_FACTOR * control_ms, control_ms + MIN_DELTA_MS), 3)}}


def separates(healthy_ms: float, incident_ms: float) -> bool:
    """Can this machine tell the two states apart at all? Used at setup time,
    expressed as the same ratio the verdict uses, so a machine that passes
    setup is a machine the experiment can run on."""
    return failure_present(incident_ms, healthy_ms)


# ------------------------------------------------------------------- reading

def _observed(results, phase):
    for result in results:
        if result.spec.phase == phase:
            return result.observed.get(METRIC)
    return None


def explain(results: Sequence[PhaseResult]) -> dict:
    """Everything the verdict is based on, as plain numbers, for display."""
    values = {phase: _observed(results, phase) for phase in PHASES}
    report = {"values": values, "controls": {}, "drifts": {}, "ratios": {},
              "unstable": [], "control_ms": None, "worst_drift": None,
              "reason": ""}
    if any(value is None for value in values.values()):
        report["reason"] = "the experiment did not run every phase"
        return report

    overall = statistics.median([values[p] for p in CONTROL_PHASES])
    report["control_ms"] = round(overall, 3)

    for phase in EVIDENCE_PHASES:
        before, after = BRACKETS[phase]
        control = local_control(values[before], values[after])
        report["controls"][phase] = round(control, 3)
        report["drifts"][phase] = round(drift(values[before], values[after]), 3)
        if not controls_agree(values[before], values[after]):
            report["unstable"].append(phase)
        if control > 0:
            report["ratios"][phase] = round(values[phase] / control, 2)

    if overall > 0:
        for phase in CONTROL_PHASES:
            report["ratios"][phase] = round(values[phase] / overall, 2)
    report["worst_drift"] = max(report["drifts"].values())
    return report


def decide(results: Sequence[PhaseResult]) -> str:
    """Pure function of measured results. THE ONLY PLACE A CAUSE IS DECIDED.

    Nothing here reads a stored baseline, a configuration file, a plan, an
    environment variable or a model. Every control it uses is one of the
    results, measured seconds ago on the machine running right now.
    """
    values = {phase: _observed(results, phase) for phase in PHASES}
    if any(value is None for value in values.values()):
        return UNRESOLVED
    if any(values[phase] <= 0 for phase in CONTROL_PHASES):
        return UNRESOLVED

    def bracket(phase):
        before, after = BRACKETS[phase]
        return values[before], values[after]

    # Did the incident reproduce, judged against the controls either side?
    if not controls_agree(*bracket(PHASE_REPRODUCE)):
        return UNRESOLVED
    if not failure_present(values[PHASE_REPRODUCE],
                           local_control(*bracket(PHASE_REPRODUCE))):
        # Nothing reproduced, so removing anything proves nothing.
        return UNRESOLVED

    # The ablation is the measurement the whole experiment exists for.
    if not controls_agree(*bracket(PHASE_ABLATE)):
        return UNRESOLVED
    ablate_control = local_control(*bracket(PHASE_ABLATE))
    ablate = values[PHASE_ABLATE]

    if recovered(ablate, ablate_control):
        # Removal worked. Two-sided confirmation needs the failure to return
        # when the change is put back - and needs that measurement to be sound.
        if not controls_agree(*bracket(PHASE_RESTORE)):
            # The recovery stands; the recurrence cannot be judged. That is
            # exactly what SUPPORTED means, and it is more honest than
            # discarding a clean ablation.
            return SUPPORTED
        return (PROVEN if failure_present(values[PHASE_RESTORE],
                                          local_control(*bracket(PHASE_RESTORE)))
                else SUPPORTED)

    if not failure_present(ablate, ablate_control):
        # Neither recovered nor clearly still failing: the ablation landed in
        # the gap between the two and says nothing either way.
        return UNRESOLVED

    return REFUTED


def reason(results: Sequence[PhaseResult]) -> str:
    """One line explaining the verdict, in the run's own numbers."""
    detail = explain(results)
    values = detail["values"]
    if detail["control_ms"] is None:
        return detail["reason"]
    if any(values[p] <= 0 for p in CONTROL_PHASES):
        return "a control phase measured nothing - the run is unusable"

    controls, drifts, unstable = detail["controls"], detail["drifts"], detail["unstable"]

    def moved(phase):
        before, after = BRACKETS[phase]
        return ("the controls either side of %s disagree by %.1fx (%.1f ms then "
                "%.1f ms) - the machine moved across that phase, so it cannot be "
                "judged" % (phase, drifts[phase], values[before], values[after]))

    if PHASE_REPRODUCE in unstable:
        return moved(PHASE_REPRODUCE)
    if not failure_present(values[PHASE_REPRODUCE], controls[PHASE_REPRODUCE]):
        return ("the incident did not reproduce: %.1f ms is only %.1fx the %.1f ms "
                "control measured either side of it"
                % (values[PHASE_REPRODUCE], detail["ratios"][PHASE_REPRODUCE],
                   controls[PHASE_REPRODUCE]))

    if PHASE_ABLATE in unstable:
        return moved(PHASE_ABLATE)
    ablate_ratio = detail["ratios"][PHASE_ABLATE]
    if recovered(values[PHASE_ABLATE], controls[PHASE_ABLATE]):
        if PHASE_RESTORE in unstable:
            return ("removing it returned latency to %.1fx its local control, but "
                    "the controls either side of the restore disagree by %.1fx - "
                    "one-sided evidence only"
                    % (ablate_ratio, drifts[PHASE_RESTORE]))
        if failure_present(values[PHASE_RESTORE], controls[PHASE_RESTORE]):
            return ("removing it returned latency to %.1fx its local control, and "
                    "restoring it brought the failure back at %.1fx its own"
                    % (ablate_ratio, detail["ratios"][PHASE_RESTORE]))
        return ("removing it recovered, but restoring it did not bring the failure "
                "back - one-sided evidence only")
    if not failure_present(values[PHASE_ABLATE], controls[PHASE_ABLATE]):
        return ("the ablation landed at %.1fx its local control, between recovery "
                "and failure - inconclusive" % ablate_ratio)
    return ("removing it left latency at %.1fx its local control - the failure "
            "survived its removal" % ablate_ratio)


def annotate(results: Sequence[PhaseResult]) -> Tuple[PhaseResult, ...]:
    """Fill in each evidence phase's PASS/FAIL against its own local control.

    Display only. decide() recomputes from the raw numbers and is the
    authority; this exists so the UI can show the same arithmetic.
    """
    detail = explain(results)
    controls = detail["controls"]
    if not controls:
        return tuple(results)
    expectations = {
        PHASE_REPRODUCE: signature_present(controls[PHASE_REPRODUCE]),
        PHASE_ABLATE: signature_absent(controls[PHASE_ABLATE]),
        PHASE_RESTORE: signature_present(controls[PHASE_RESTORE]),
    }
    out = []
    for result in results:
        expected = expectations.get(result.spec.phase)
        if expected is None:
            out.append(result)
            continue
        passed, lines = measurement.matches(result.observed, expected)
        spec = PhaseSpec(result.spec.hypothesis_id, result.spec.phase,
                         result.spec.flags, result.spec.fixture_id, expected)
        out.append(PhaseResult(spec, result.observed, passed, tuple(lines)))
    return tuple(out)


def plan_phases(hypothesis_id: str, incident_flags: Mapping[str, bool],
                fixture_id: str):
    """The seven phases, in order.

    control-1  every candidate off - what healthy costs right now
    reproduce  the incident state - the failure has to be there to study it
    control-2  healthy again, on the far side of the reproduction
    ablate     ONE candidate removed, every other flag held fixed
    control-3  healthy again - the ablation is now bracketed on both sides
    restore    the candidate put back
    control-4  healthy one last time
    """
    if hypothesis_id not in incident_flags:
        raise KeyError("%r is not part of the incident state" % hypothesis_id)

    healthy = {key: False for key in incident_flags}
    incident = dict(incident_flags)
    ablated = dict(incident_flags)
    ablated[hypothesis_id] = False

    def spec(phase, flags):
        return PhaseSpec(hypothesis_id, phase, dict(flags), fixture_id)

    return [
        spec(PHASE_CONTROL_1, healthy),
        spec(PHASE_REPRODUCE, incident),
        spec(PHASE_CONTROL_2, healthy),
        spec(PHASE_ABLATE, ablated),
        spec(PHASE_CONTROL_3, healthy),
        spec(PHASE_RESTORE, incident),
        spec(PHASE_CONTROL_4, healthy),
    ]
