"""causeway.prediction.rollup: a pure aggregation over RiskAssessments the
engine already produced. No detector, no hysteresis, no score is computed
here - every test constructs RiskAssessment objects directly, the same way
tests/test_prediction.py exercises each detector without a telemetry store.
"""
from __future__ import annotations

import unittest

from causeway.prediction.rollup import (ELEVATED, HIGH_RISK, INSUFFICIENT_DATA,
                                        STABLE, WATCH, service_risk, state_for,
                                        system_risk, worst_state)
from causeway.prediction.schema import HIGH, LOW, MEDIUM, RiskAssessment


def assessment(level, confirmed=False, score=0.5, service="svc", detector="d"):
    return RiskAssessment(service=service, detector=detector, level=level, score=score,
                          predicted_failure="something", evidence=(), current_values={},
                          trends={}, eta_seconds=None, sample_count=10, confirmed=confirmed)


class StateForTests(unittest.TestCase):
    def test_low_is_stable(self):
        self.assertEqual(state_for(assessment(LOW)), STABLE)

    def test_medium_is_watch(self):
        self.assertEqual(state_for(assessment(MEDIUM)), WATCH)

    def test_high_unconfirmed_is_elevated(self):
        self.assertEqual(state_for(assessment(HIGH, confirmed=False)), ELEVATED)

    def test_high_confirmed_is_high_risk(self):
        self.assertEqual(state_for(assessment(HIGH, confirmed=True)), HIGH_RISK)


class WorstStateTests(unittest.TestCase):
    def test_no_assessments_is_insufficient_data(self):
        self.assertEqual(worst_state([]), INSUFFICIENT_DATA)

    def test_the_most_severe_assessment_wins(self):
        assessments = [assessment(LOW), assessment(MEDIUM), assessment(HIGH, confirmed=True)]
        self.assertEqual(worst_state(assessments), HIGH_RISK)

    def test_an_unconfirmed_high_does_not_outrank_a_confirmed_one(self):
        assessments = [assessment(HIGH, confirmed=True), assessment(HIGH, confirmed=False)]
        self.assertEqual(worst_state(assessments), HIGH_RISK)


class ServiceRiskTests(unittest.TestCase):
    def test_score_is_the_worst_assessments_own_score_scaled_to_100(self):
        risk = service_risk("svc", [assessment(LOW, score=0.2), assessment(HIGH, score=0.9)])
        self.assertEqual(risk.score, 90.0)
        self.assertEqual(risk.state, ELEVATED)

    def test_no_assessments_is_insufficient_data_at_zero_score(self):
        risk = service_risk("svc", [])
        self.assertEqual(risk.state, INSUFFICIENT_DATA)
        self.assertEqual(risk.score, 0.0)

    def test_as_dict_carries_every_underlying_assessment(self):
        risk = service_risk("svc", [assessment(MEDIUM)])
        payload = risk.as_dict()
        self.assertEqual(payload["service"], "svc")
        self.assertEqual(len(payload["assessments"]), 1)


class SystemRiskTests(unittest.TestCase):
    def test_no_services_at_all_is_insufficient_data(self):
        risk = system_risk({})
        self.assertEqual(risk.state, INSUFFICIENT_DATA)
        self.assertEqual(risk.score, 0.0)
        self.assertEqual(risk.services_degraded, 0)
        self.assertEqual(risk.services, ())

    def test_the_system_state_is_the_worst_service_state(self):
        risk = system_risk({
            "a": [assessment(LOW, service="a")],
            "b": [assessment(HIGH, confirmed=True, service="b")],
        })
        self.assertEqual(risk.state, HIGH_RISK)
        self.assertEqual(risk.score, 100.0 * assessment(HIGH).score)

    def test_services_degraded_counts_watch_elevated_and_high_risk_not_stable_or_insufficient(self):
        risk = system_risk({
            "stable": [assessment(LOW, service="stable")],
            "watching": [assessment(MEDIUM, service="watching")],
            "elevated": [assessment(HIGH, confirmed=False, service="elevated")],
            "confirmed": [assessment(HIGH, confirmed=True, service="confirmed")],
            "no_data": [],
        })
        self.assertEqual(risk.services_degraded, 3)
        self.assertEqual(len(risk.services), 5)

    def test_a_service_with_no_assessments_does_not_drag_the_system_into_insufficient_data(self):
        # One real signal anywhere in the system is enough to report on it -
        # a second, quieter service with no samples yet should not hide that.
        risk = system_risk({
            "loud": [assessment(HIGH, confirmed=True, service="loud")],
            "quiet": [],
        })
        self.assertEqual(risk.state, HIGH_RISK)

    def test_deterministic_same_input_same_output(self):
        per_service = {"a": [assessment(MEDIUM, service="a")]}
        self.assertEqual(system_risk(per_service), system_risk(per_service))

    def test_services_are_reported_in_a_stable_sorted_order(self):
        risk = system_risk({
            "zeta": [assessment(LOW, service="zeta")],
            "alpha": [assessment(LOW, service="alpha")],
        })
        self.assertEqual([s.service for s in risk.services], ["alpha", "zeta"])


if __name__ == "__main__":
    unittest.main()
