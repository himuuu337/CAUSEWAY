"""The verdict is a pure function of measurements taken during one run.

Two things are pinned here.

That no stored threshold can enter a decision: scale every measurement in a
run by the same factor and the verdict must not move.

And that a machine drifting over a long run does not destroy a good
experiment. The controls are interleaved with the phases they judge, so each
phase is compared only with the machine as it was on either side of it.
"""
from __future__ import annotations

import unittest

from causeway import verdict as v

INCIDENT = {"A": True, "B": True}


def phase(name, p95_ms, hypothesis="B"):
    spec = v.PhaseSpec(hypothesis, name, dict(INCIDENT), "incident-001")
    return v.PhaseResult(spec, {"p95_ms": float(p95_ms)})


def run(controls, reproduce, ablate, restore, hypothesis="B"):
    """One seven-phase run. `controls` are the four control measurements in the
    order they were taken."""
    c1, c2, c3, c4 = controls
    return [
        phase(v.PHASE_CONTROL_1, c1, hypothesis),
        phase(v.PHASE_REPRODUCE, reproduce, hypothesis),
        phase(v.PHASE_CONTROL_2, c2, hypothesis),
        phase(v.PHASE_ABLATE, ablate, hypothesis),
        phase(v.PHASE_CONTROL_3, c3, hypothesis),
        phase(v.PHASE_RESTORE, restore, hypothesis),
        phase(v.PHASE_CONTROL_4, c4, hypothesis),
    ]


def steady(control, reproduce, ablate, restore, hypothesis="B"):
    return run([control] * 4, reproduce, ablate, restore, hypothesis)


def scaled(results, factor):
    return [v.PhaseResult(r.spec, {"p95_ms": r.observed["p95_ms"] * factor})
            for r in results]


class DemoIncidentTests(unittest.TestCase):
    """The demo's own numbers: healthy ~23 ms, incident ~333 ms."""

    def test_B_the_causal_change_is_proven(self):
        self.assertEqual(v.decide(steady(23, 333, 21, 331)), v.PROVEN)

    def test_A_the_decoy_is_refuted(self):
        self.assertEqual(v.decide(steady(23, 330, 315, 330, "A")), v.REFUTED)
        self.assertIn("survived its removal",
                      v.reason(steady(23, 330, 315, 330, "A")))

    def test_the_story_the_numbers_tell_is_high_low_high(self):
        detail = v.explain(steady(23, 333, 21, 331))
        self.assertGreater(detail["ratios"][v.PHASE_REPRODUCE], 4.0)
        self.assertLess(detail["ratios"][v.PHASE_ABLATE], 2.5)
        self.assertGreater(detail["ratios"][v.PHASE_RESTORE], 4.0)


class GlobalDriftTests(unittest.TestCase):
    """A run long enough to reproduce an incident three times is long enough
    for a laptop to change speed. That must not destroy the experiment."""

    CONTROLS = [23.0, 40.0, 68.0, 110.0]     # the machine slows down 4.8x overall

    def results(self):
        return run(self.CONTROLS, 333.0, 34.0, 640.0)

    def test_the_verdict_survives_the_machine_drifting_under_it(self):
        self.assertEqual(v.decide(self.results()), v.PROVEN)

    def test_a_whole_run_guard_would_have_abstained(self):
        """Proves the test above is exercising the problem: start to end, this
        machine moved further than any whole-run guard would tolerate."""
        self.assertGreater(v.drift(self.CONTROLS[0], self.CONTROLS[-1]),
                           v.LOCAL_DRIFT_LIMIT)

    def test_every_local_bracket_is_nonetheless_stable(self):
        for before, after in zip(self.CONTROLS, self.CONTROLS[1:]):
            self.assertTrue(v.controls_agree(before, after))

    def test_a_machine_speeding_up_reads_the_same_as_one_slowing_down(self):
        backwards = run(list(reversed(self.CONTROLS)), 640.0, 34.0, 333.0)
        self.assertEqual(v.decide(backwards), v.PROVEN)

    def test_each_phase_is_judged_against_its_own_neighbours(self):
        detail = v.explain(self.results())
        self.assertEqual(detail["controls"][v.PHASE_REPRODUCE], 31.5)   # 23, 40
        self.assertEqual(detail["controls"][v.PHASE_ABLATE], 54.0)      # 40, 68
        self.assertEqual(detail["controls"][v.PHASE_RESTORE], 89.0)     # 68, 110


class LocalInstabilityTests(unittest.TestCase):
    """Abstention has not gone anywhere - it has been made local."""

    def test_instability_around_the_ablation_is_unresolved(self):
        results = run([23.0, 23.0, 300.0, 300.0], 333.0, 45.0, 331.0)
        self.assertEqual(v.decide(results), v.UNRESOLVED)
        self.assertIn("either side of ablate", v.reason(results))

    def test_instability_around_the_reproduction_is_unresolved(self):
        results = run([20.0, 300.0, 300.0, 300.0], 4000.0, 310.0, 4000.0)
        self.assertEqual(v.decide(results), v.UNRESOLVED)
        self.assertIn("either side of reproduce", v.reason(results))

    def test_instability_around_the_restore_is_one_sided_evidence(self):
        """The clean ablation stands; the recurrence cannot be judged, so the
        verdict stops short of PROVEN rather than throwing the run away."""
        results = run([23.0, 23.0, 23.0, 400.0], 333.0, 21.0, 331.0)
        self.assertEqual(v.decide(results), v.SUPPORTED)
        self.assertIn("one-sided", v.reason(results))

    def test_the_unstable_phase_is_named(self):
        detail = v.explain(run([23.0, 23.0, 300.0, 300.0], 333.0, 45.0, 331.0))
        self.assertEqual(detail["unstable"], [v.PHASE_ABLATE])


class ScaleInvarianceTests(unittest.TestCase):
    """A verdict is a statement about ratios, not about milliseconds.

    The range stops above the noise floor, and that is the honest boundary
    rather than a convenience: at 0.01x this incident is a 0.23 ms control
    against a 3.3 ms failure, an absolute gap of 3 ms. No machine can tell
    that apart from jitter, and the last test here pins that the engine
    abstains there instead of pretending.
    """

    SCALES = (0.05, 0.2, 1.0, 10.0, 100.0)

    def test_proven_survives_global_scaling(self):
        base = steady(23, 333, 21, 331)
        for scale in self.SCALES:
            self.assertEqual(v.decide(scaled(base, scale)), v.PROVEN,
                             "broke at scale %s" % scale)

    def test_refuted_survives_global_scaling(self):
        base = steady(23, 330, 315, 330, "A")
        for scale in self.SCALES:
            self.assertEqual(v.decide(scaled(base, scale)), v.REFUTED,
                             "broke at scale %s" % scale)

    def test_proven_survives_scaling_while_drifting(self):
        base = run([23.0, 40.0, 68.0, 110.0], 333.0, 34.0, 640.0)
        for scale in self.SCALES:
            self.assertEqual(v.decide(scaled(base, scale)), v.PROVEN,
                             "broke at scale %s" % scale)

    def test_below_the_noise_floor_the_engine_abstains_rather_than_guessing(self):
        """Scaled to sub-millisecond, the same causal structure is no longer
        measurable. UNRESOLVED is the correct answer, and the reason says so."""
        shrunk = scaled(steady(23, 333, 21, 331), 0.01)
        self.assertEqual(v.decide(shrunk), v.UNRESOLVED)
        self.assertIn("did not reproduce", v.reason(shrunk))


class DecisionTableTests(unittest.TestCase):
    def test_two_sided_confirmation_is_proven(self):
        self.assertEqual(v.decide(steady(10, 500, 11, 520)), v.PROVEN)

    def test_failure_surviving_removal_is_refuted(self):
        self.assertEqual(v.decide(steady(10, 500, 480, 510)), v.REFUTED)

    def test_recovery_without_recurrence_is_supported(self):
        self.assertEqual(v.decide(steady(10, 500, 11, 12)), v.SUPPORTED)

    def test_a_failure_that_does_not_reproduce_is_unresolved(self):
        self.assertEqual(v.decide(steady(10, 12, 11, 13)), v.UNRESOLVED)

    def test_an_ablation_between_recovery_and_failure_is_unresolved(self):
        self.assertEqual(v.decide(steady(50, 5000, 170, 5100)), v.UNRESOLVED)

    def test_small_absolute_differences_are_not_causal_findings(self):
        self.assertEqual(v.decide(steady(0.2, 2.0, 0.3, 2.1)), v.UNRESOLVED)

    def test_a_missing_phase_is_unresolved(self):
        self.assertEqual(v.decide(steady(10, 500, 11, 520)[:6]), v.UNRESOLVED)

    def test_a_zero_control_is_unresolved(self):
        self.assertEqual(v.decide(run([0, 10, 10, 10], 500, 11, 520)), v.UNRESOLVED)

    def test_is_deterministic(self):
        results = steady(23, 333, 21, 331)
        self.assertEqual(v.decide(results), v.decide(results))


class NoiseFloorTests(unittest.TestCase):
    """Small numbers make large ratios out of nothing. The floor applies in
    both directions - a restriction on what counts as a difference, not a
    relaxation of what counts as a failure."""

    def test_a_difference_below_the_floor_is_not_drift(self):
        self.assertEqual(v.drift(2.0, 6.0), 1.0)
        self.assertTrue(v.controls_agree(2.0, 6.0))

    def test_a_difference_above_the_floor_is_still_drift(self):
        self.assertFalse(v.controls_agree(2.0, 20.0))

    def test_recovery_may_be_established_by_an_absolute_margin(self):
        self.assertTrue(v.recovered(6.0, 2.0))

    def test_the_floor_cannot_turn_a_real_failure_into_a_recovery(self):
        self.assertFalse(v.recovered(333.0, 23.0))

    def test_the_failure_floor_still_holds(self):
        self.assertEqual(v.MIN_DELTA_MS, 5.0)
        self.assertFalse(v.failure_present(5.9, 1.0))
        self.assertTrue(v.failure_present(333.0, 23.0))


class ProtocolTests(unittest.TestCase):
    def test_the_protocol_interleaves_controls_with_the_phases_they_judge(self):
        specs = v.plan_phases("B", INCIDENT, "incident-001")
        self.assertEqual([s.phase for s in specs], list(v.PHASES))
        self.assertEqual(len(specs), 7)

    def test_every_evidence_phase_has_a_control_on_each_side(self):
        order = list(v.PHASES)
        for measured in v.EVIDENCE_PHASES:
            before, after = v.BRACKETS[measured]
            self.assertEqual(order[order.index(measured) - 1], before)
            self.assertEqual(order[order.index(measured) + 1], after)

    def test_control_phases_turn_every_candidate_off(self):
        specs = {s.phase: s for s in v.plan_phases("B", INCIDENT, "incident-001")}
        for name in v.CONTROL_PHASES:
            self.assertEqual(specs[name].flags, {"A": False, "B": False})

    def test_the_ablation_moves_exactly_one_flag(self):
        specs = {s.phase: s for s in v.plan_phases("B", INCIDENT, "incident-001")}
        self.assertEqual(specs[v.PHASE_REPRODUCE].flags, {"A": True, "B": True})
        self.assertEqual(specs[v.PHASE_ABLATE].flags, {"A": True, "B": False})
        self.assertEqual(specs[v.PHASE_RESTORE].flags, {"A": True, "B": True})

    def test_an_unknown_hypothesis_is_rejected(self):
        with self.assertRaises(KeyError):
            v.plan_phases("Z", INCIDENT, "incident-001")

    def test_separation_uses_the_same_ratio_the_verdict_uses(self):
        self.assertTrue(v.separates(23.0, 333.0))
        self.assertFalse(v.separates(23.0, 60.0))


if __name__ == "__main__":
    unittest.main()
