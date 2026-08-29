"""The fix verdict: VERIFIED / FAILED / UNRESOLVED, from measurements alone.

Mirrors tests/test_verdict.py's decision-table style for the five-phase fix
protocol. Every result here is constructed by hand - no sandbox, no
subprocess, nothing but the arithmetic.
"""
from __future__ import annotations

import unittest

from causeway import fix_verdict, verdict


def phase(phase_name, p95_ms):
    spec = verdict.PhaseSpec("B", phase_name, {}, "incident-001")
    return verdict.PhaseResult(spec, {"p95_ms": p95_ms})


def run(control1, before, control2, after, control3):
    return [
        phase(fix_verdict.FIX_CONTROL_1, control1),
        phase(fix_verdict.FIX_BEFORE, before),
        phase(fix_verdict.FIX_CONTROL_2, control2),
        phase(fix_verdict.FIX_AFTER, after),
        phase(fix_verdict.FIX_CONTROL_3, control3),
    ]


class DecisionTableTests(unittest.TestCase):
    def test_a_clean_recovery_is_verified(self):
        # healthy ~10ms, before-fix reproduces at 14x, after-fix recovers.
        results = run(10.0, 140.0, 10.0, 11.0, 10.0)
        self.assertEqual(fix_verdict.decide(results), fix_verdict.VERIFIED)

    def test_the_failure_surviving_the_fix_is_failed(self):
        results = run(10.0, 140.0, 10.0, 138.0, 10.0)
        self.assertEqual(fix_verdict.decide(results), fix_verdict.FAILED)

    def test_a_fix_landing_between_recovery_and_failure_is_unresolved(self):
        # 3x its control: below the 4x failure floor, above the 2.5x
        # recovery ceiling - neither clearly worked nor clearly didn't.
        results = run(10.0, 140.0, 10.0, 30.0, 10.0)
        self.assertEqual(fix_verdict.decide(results), fix_verdict.UNRESOLVED)

    def test_an_incident_that_never_reproduced_before_the_fix_is_unresolved(self):
        """Nothing to credit a fix with removing if it never reproduced."""
        results = run(10.0, 12.0, 10.0, 11.0, 10.0)
        self.assertEqual(fix_verdict.decide(results), fix_verdict.UNRESOLVED)

    def test_a_missing_phase_is_unresolved(self):
        results = run(10.0, 140.0, 10.0, 11.0, 10.0)[:-1]
        self.assertEqual(fix_verdict.decide(results), fix_verdict.UNRESOLVED)

    def test_a_zero_control_is_unresolved(self):
        results = run(0.0, 140.0, 10.0, 11.0, 10.0)
        self.assertEqual(fix_verdict.decide(results), fix_verdict.UNRESOLVED)

    def test_unstable_controls_around_before_are_unresolved(self):
        # control1 and control2 disagree by far more than the 3x drift limit.
        results = run(10.0, 140.0, 60.0, 11.0, 10.0)
        self.assertEqual(fix_verdict.decide(results), fix_verdict.UNRESOLVED)

    def test_unstable_controls_around_after_are_unresolved(self):
        results = run(10.0, 140.0, 10.0, 11.0, 60.0)
        self.assertEqual(fix_verdict.decide(results), fix_verdict.UNRESOLVED)

    def test_small_absolute_differences_are_not_causal_findings(self):
        """Below the noise floor, a fix cannot be credited or blamed."""
        results = run(2.0, 3.0, 2.0, 2.5, 2.0)
        self.assertEqual(fix_verdict.decide(results), fix_verdict.UNRESOLVED)

    def test_is_deterministic(self):
        results = run(10.0, 140.0, 10.0, 11.0, 10.0)
        self.assertEqual(fix_verdict.decide(results), fix_verdict.decide(results))


class ScaleAndDriftTests(unittest.TestCase):
    def test_verified_survives_global_scaling(self):
        base = run(10.0, 140.0, 10.0, 11.0, 10.0)
        for factor in (0.05, 1.0, 100.0):
            scaled = [phase(r.spec.phase, r.observed["p95_ms"] * factor) for r in base]
            self.assertEqual(fix_verdict.decide(scaled), fix_verdict.VERIFIED,
                            "factor=%.2f" % factor)

    def test_failed_survives_global_scaling(self):
        base = run(10.0, 140.0, 10.0, 138.0, 10.0)
        for factor in (0.05, 1.0, 100.0):
            scaled = [phase(r.spec.phase, r.observed["p95_ms"] * factor) for r in base]
            self.assertEqual(fix_verdict.decide(scaled), fix_verdict.FAILED,
                            "factor=%.2f" % factor)

    def test_verified_survives_drift_the_machine_holds_within_a_phase(self):
        """The controls move noticeably over the whole run (10 -> 35) - a
        whole-run guard would flag that. The local-control design should not,
        as long as each individual bracket stays within the drift limit."""
        results = run(10.0, 140.0, 13.0, 14.0, 35.0)
        self.assertEqual(fix_verdict.decide(results), fix_verdict.VERIFIED)


class ExplainAndReasonTests(unittest.TestCase):
    def test_explain_reports_the_two_evidence_phases(self):
        results = run(10.0, 140.0, 10.0, 11.0, 10.0)
        detail = fix_verdict.explain(results)
        self.assertIn(fix_verdict.FIX_BEFORE, detail["controls"])
        self.assertIn(fix_verdict.FIX_AFTER, detail["controls"])

    def test_reason_is_a_nonempty_sentence_for_every_branch(self):
        for results in (
            run(10.0, 140.0, 10.0, 11.0, 10.0),
            run(10.0, 140.0, 10.0, 138.0, 10.0),
            run(10.0, 140.0, 10.0, 30.0, 10.0),
            run(10.0, 12.0, 10.0, 11.0, 10.0),
        ):
            self.assertTrue(fix_verdict.reason(results))

    def test_annotate_marks_before_present_and_after_absent(self):
        results = fix_verdict.annotate(run(10.0, 140.0, 10.0, 11.0, 10.0))
        by_phase = {r.spec.phase: r for r in results}
        self.assertTrue(by_phase[fix_verdict.FIX_BEFORE].passed)
        self.assertTrue(by_phase[fix_verdict.FIX_AFTER].passed)


class ProtocolTests(unittest.TestCase):
    def test_fix_phase_specs_produces_five_phases_in_order(self):
        specs = fix_verdict.fix_phase_specs("B", {"A": True, "B": True}, "incident-001")
        self.assertEqual([s.phase for s in specs], list(fix_verdict.FIX_PHASES))

    def test_control_phases_turn_every_candidate_off(self):
        specs = fix_verdict.fix_phase_specs("B", {"A": True, "B": True}, "incident-001")
        for spec in specs:
            if spec.phase in fix_verdict.FIX_CONTROL_PHASES:
                self.assertEqual(spec.flags, {"A": False, "B": False})

    def test_evidence_phases_use_the_incident_state_unchanged(self):
        """Unlike the causal ablate phase, the fix protocol never flips the
        hypothesis flag off - the point is testing the fix WITH the change
        still deployed, exactly as the original incident had it."""
        specs = fix_verdict.fix_phase_specs("B", {"A": True, "B": True}, "incident-001")
        by_phase = {s.phase: s for s in specs}
        self.assertEqual(by_phase[fix_verdict.FIX_BEFORE].flags, {"A": True, "B": True})
        self.assertEqual(by_phase[fix_verdict.FIX_AFTER].flags, {"A": True, "B": True})

    def test_an_unknown_hypothesis_is_rejected(self):
        with self.assertRaises(KeyError):
            fix_verdict.fix_phase_specs("Z", {"A": True, "B": True}, "incident-001")


if __name__ == "__main__":
    unittest.main()
