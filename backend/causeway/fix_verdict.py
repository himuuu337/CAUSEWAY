"""THE FIX VERDICT.

This module decides whether a proposed fix VERIFIED, FAILED or left the
outcome UNRESOLVED, and it decides that from measured numbers alone - the
second half of the same rule causeway/verdict.py enforces for the causal
claim itself:

    AI PROPOSES.  CODE VALIDATES.  SYSTEM EXPERIMENTS.  MEASUREMENTS DECIDE.

A language model may propose a fix; it may never reach this module. That is
enforced structurally, the same way it is for the causal verdict:
tests/test_no_model_in_fix_verdict.py walks this module's import graph and
fails the build if anything model-shaped, networked or fixer-shaped becomes
reachable from it. The only imports here are the standard library and
causeway.verdict, whose own import graph is already proven clean by
tests/test_no_model_in_verdict.py.

The protocol is five phases instead of causeway.verdict's seven - one
before/after comparison rather than a two-sided ablate/restore - but the
judging arithmetic is the identical rule, reused rather than reimplemented:
every phase is judged only against the median of the healthy controls
measured immediately beside it.

    fix-control-1  fix-before  fix-control-2  fix-after  fix-control-3

fix-before reproduces the ORIGINAL incident state on the UNPATCHED sandbox
service, bracketed by fix-control-1/fix-control-2. fix-after replays the
identical workload, in the identical incident state, against the PATCHED
service, bracketed by fix-control-2/fix-control-3. The controls establish
what healthy costs on each side of the swap; the failure has to reproduce
before a fix can be credited with removing it.
"""
from __future__ import annotations

from typing import Mapping, Sequence, Tuple

from causeway import measurement, verdict

# ------------------------------------------------------------------ protocol

FIX_CONTROL_1 = "fix-control-1"
FIX_BEFORE = "fix-before"
FIX_CONTROL_2 = "fix-control-2"
FIX_AFTER = "fix-after"
FIX_CONTROL_3 = "fix-control-3"

FIX_PHASES = (FIX_CONTROL_1, FIX_BEFORE, FIX_CONTROL_2, FIX_AFTER, FIX_CONTROL_3)
FIX_CONTROL_PHASES = (FIX_CONTROL_1, FIX_CONTROL_2, FIX_CONTROL_3)
FIX_EVIDENCE_PHASES = (FIX_BEFORE, FIX_AFTER)

FIX_BRACKETS = {
    FIX_BEFORE: (FIX_CONTROL_1, FIX_CONTROL_2),
    FIX_AFTER: (FIX_CONTROL_2, FIX_CONTROL_3),
}

# Which phases run against the PATCHED build. The swap happens after
# fix-before, so fix-control-2 is the first control measured on the patched
# side - it is what establishes that healthy still costs the same there, and
# fix-after is judged against it. This is a statement of the protocol, not a
# threshold: an actuator reads it to know which phases carry the fix.
FIX_UNPATCHED_PHASES = (FIX_CONTROL_1, FIX_BEFORE)
FIX_PATCHED_PHASES = (FIX_CONTROL_2, FIX_AFTER, FIX_CONTROL_3)

# ------------------------------------------------------------------ verdicts

VERIFIED = "VERIFIED"
FAILED = "FAILED"
UNRESOLVED = "UNRESOLVED"

METRIC = verdict.METRIC


def fix_phase_specs(hypothesis_id: str, incident_flags: Mapping[str, bool],
                    fixture_id: str) -> list:
    """The five phases, in order.

    fix-control-1  every candidate off, unpatched service - what healthy
                   costs before the fix is even in the picture
    fix-before     the incident state, unpatched service - the failure has
                   to be there to credit anything with removing it
    fix-control-2  every candidate off, on the far side of the swap to the
                   patched service
    fix-after      the SAME incident state, patched service - the fix
                   candidate, tested exactly as the original failure was
    fix-control-3  every candidate off, patched service, one last time
    """
    if hypothesis_id not in incident_flags:
        raise KeyError("%r is not part of the incident state" % hypothesis_id)

    healthy = {key: False for key in incident_flags}
    incident = dict(incident_flags)

    def spec(phase, flags):
        return verdict.PhaseSpec(hypothesis_id, phase, dict(flags), fixture_id)

    return [
        spec(FIX_CONTROL_1, healthy),
        spec(FIX_BEFORE, incident),
        spec(FIX_CONTROL_2, healthy),
        spec(FIX_AFTER, incident),
        spec(FIX_CONTROL_3, healthy),
    ]


# ------------------------------------------------------------------- reading

def _observed(results, phase):
    for result in results:
        if result.spec.phase == phase:
            return result.observed.get(METRIC)
    return None


def explain(results: Sequence["verdict.PhaseResult"]) -> dict:
    """Everything the fix verdict is based on, as plain numbers, for display.
    Mirrors causeway.verdict.explain's shape for the two-phase protocol."""
    values = {phase: _observed(results, phase) for phase in FIX_PHASES}
    report = {"values": values, "controls": {}, "drifts": {}, "ratios": {},
              "unstable": [], "reason": ""}
    if any(value is None for value in values.values()):
        report["reason"] = "the fix verification did not run every phase"
        return report

    for phase in FIX_EVIDENCE_PHASES:
        before, after = FIX_BRACKETS[phase]
        control = verdict.local_control(values[before], values[after])
        report["controls"][phase] = round(control, 3)
        report["drifts"][phase] = round(verdict.drift(values[before], values[after]), 3)
        if not verdict.controls_agree(values[before], values[after]):
            report["unstable"].append(phase)
        if control > 0:
            report["ratios"][phase] = round(values[phase] / control, 2)
    return report


def decide(results: Sequence["verdict.PhaseResult"]) -> str:
    """Pure function of measured results. THE ONLY PLACE A FIX IS VERIFIED.

    Nothing here reads a plan, a model response, a filesystem path or an
    environment variable - only the five measurements this run produced.
    """
    values = {phase: _observed(results, phase) for phase in FIX_PHASES}
    if any(value is None for value in values.values()):
        return UNRESOLVED
    if any(values[phase] <= 0 for phase in FIX_CONTROL_PHASES):
        return UNRESOLVED

    def bracket(phase):
        before, after = FIX_BRACKETS[phase]
        return values[before], values[after]

    # The incident has to reproduce on the unpatched service before a fix can
    # be credited with anything - otherwise there is nothing to have fixed.
    if not verdict.controls_agree(*bracket(FIX_BEFORE)):
        return UNRESOLVED
    if not verdict.failure_present(values[FIX_BEFORE],
                                   verdict.local_control(*bracket(FIX_BEFORE))):
        return UNRESOLVED

    if not verdict.controls_agree(*bracket(FIX_AFTER)):
        return UNRESOLVED
    after_control = verdict.local_control(*bracket(FIX_AFTER))
    after = values[FIX_AFTER]

    if verdict.recovered(after, after_control):
        return VERIFIED
    if verdict.failure_present(after, after_control):
        return FAILED
    # Neither clearly recovered nor clearly still broken: the measurement
    # landed in the gap between the two and says nothing either way.
    return UNRESOLVED


def reason(results: Sequence["verdict.PhaseResult"]) -> str:
    """One line explaining the fix verdict, in the run's own numbers."""
    detail = explain(results)
    values = detail["values"]
    if detail["reason"]:
        return detail["reason"]
    if any(values[p] <= 0 for p in FIX_CONTROL_PHASES):
        return "a control phase measured nothing - the run is unusable"

    controls, drifts, unstable = detail["controls"], detail["drifts"], detail["unstable"]

    def moved(phase):
        before, after = FIX_BRACKETS[phase]
        return ("the controls either side of %s disagree by %.1fx (%.1f ms then "
                "%.1f ms) - the machine moved across that phase, so it cannot be "
                "judged" % (phase, drifts[phase], values[before], values[after]))

    if FIX_BEFORE in unstable:
        return moved(FIX_BEFORE)
    if not verdict.failure_present(values[FIX_BEFORE], controls[FIX_BEFORE]):
        return ("the incident did not reproduce before the fix: %.1f ms is only "
                "%.1fx the %.1f ms control measured either side of it"
                % (values[FIX_BEFORE], detail["ratios"][FIX_BEFORE], controls[FIX_BEFORE]))

    if FIX_AFTER in unstable:
        return moved(FIX_AFTER)
    after_ratio = detail["ratios"][FIX_AFTER]
    if verdict.recovered(values[FIX_AFTER], controls[FIX_AFTER]):
        return ("with the fix applied, latency returned to %.1fx its local control"
                % after_ratio)
    if verdict.failure_present(values[FIX_AFTER], controls[FIX_AFTER]):
        return ("with the fix applied, latency remained at %.1fx its local control - "
                "the failure survived the fix" % after_ratio)
    return ("with the fix applied, latency landed at %.1fx its local control, between "
            "recovery and failure - inconclusive" % after_ratio)


def annotate(results: Sequence["verdict.PhaseResult"]) -> Tuple["verdict.PhaseResult", ...]:
    """Fill in each evidence phase's PASS/FAIL against its own local control.
    Display only, exactly as causeway.verdict.annotate is for the causal run."""
    detail = explain(results)
    controls = detail["controls"]
    if not controls:
        return tuple(results)
    expectations = {
        FIX_BEFORE: verdict.signature_present(controls[FIX_BEFORE]),
        FIX_AFTER: verdict.signature_absent(controls[FIX_AFTER]),
    }
    out = []
    for result in results:
        expected = expectations.get(result.spec.phase)
        if expected is None:
            out.append(result)
            continue
        passed, lines = measurement.matches(result.observed, expected)
        spec = verdict.PhaseSpec(result.spec.hypothesis_id, result.spec.phase,
                                 result.spec.flags, result.spec.fixture_id, expected)
        out.append(verdict.PhaseResult(spec, result.observed, passed, tuple(lines)))
    return tuple(out)
