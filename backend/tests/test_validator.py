"""The gate between a proposed experiment and the sandbox.

Every plan - from a model or from the fallback - goes through the same eight
checks. These tests are the specification of what a planner is not allowed to
do, written as attacks.
"""
from __future__ import annotations

import unittest

from causeway import planner, verdict
from causeway.incident import deploy_record
from causeway.localizer import localize
from causeway.planner.deterministic import DeterministicPlanner
from causeway.planner.schema import ProviderUnavailable
from causeway.planner.validator import CHECK_NAMES, validate

RECORD = deploy_record()
CANDIDATES, _ = localize(RECORD)
STATE = {c.change_id: True for c in CANDIDATES}
REQUEST = planner.build_request(RECORD["incident"], CANDIDATES, STATE,
                                ["incident-001"], "B")


def good_plan(**overrides):
    plan = DeterministicPlanner().propose(REQUEST)
    plan.update(overrides)
    return plan


def rejected_names(plan):
    return [c.name for c in validate(plan, REQUEST).rejections]


class AcceptanceTests(unittest.TestCase):
    def test_a_well_formed_plan_is_accepted_by_all_eight_checks(self):
        report = validate(good_plan(), REQUEST)
        self.assertTrue(report.accepted)
        self.assertEqual(len(report.checks), 8)
        self.assertEqual(tuple(c.name for c in report.checks), CHECK_NAMES)

    def test_the_accepted_plan_matches_the_published_shape(self):
        plan = validate(good_plan(), REQUEST).plan
        self.assertEqual(plan.hypothesis_id, "B")
        self.assertEqual(plan.intervention, {"flag": "B", "value": False})
        self.assertEqual(plan.fixture_id, "incident-001")
        self.assertEqual(plan.expected_signature["metric"], "p95_ms")
        self.assertEqual(plan.expected_signature["relative_to"], "control")
        self.assertEqual(plan.expected_signature["factor"], verdict.RECOVERY_FACTOR)

    def test_validation_is_deterministic(self):
        first = validate(good_plan(), REQUEST).as_dict()
        self.assertEqual(first, validate(good_plan(), REQUEST).as_dict())


class RejectionTests(unittest.TestCase):
    def test_a_candidate_that_was_never_localised_is_rejected(self):
        self.assertIn("hypothesis_in_candidates",
                      rejected_names(good_plan(hypothesis_id="Z",
                                               intervention={"flag": "Z", "value": False})))

    def test_an_intervention_the_sandbox_cannot_make_is_rejected(self):
        self.assertIn("intervention_surface_exists",
                      rejected_names(good_plan(intervention={"flag": "restart_db",
                                                             "value": False})))

    def test_intervening_on_a_different_change_than_the_hypothesis_is_rejected(self):
        names = rejected_names(good_plan(intervention={"flag": "A", "value": False}))
        self.assertIn("single_independent_variable", names)

    def test_a_no_op_intervention_is_rejected(self):
        """Setting the flag to the value it already has moves nothing."""
        self.assertIn("single_independent_variable",
                      rejected_names(good_plan(intervention={"flag": "B", "value": True})))

    def test_a_fabricated_fixture_is_rejected(self):
        self.assertIn("fixture_exists", rejected_names(good_plan(fixture_id="made-up")))

    def test_an_experiment_that_discriminates_nothing_is_rejected(self):
        self.assertIn("discriminates_between_two",
                      rejected_names(good_plan(discriminates_between=["B"])))

    def test_an_unknown_name_in_discrimination_is_rejected(self):
        self.assertIn("discriminates_between_two",
                      rejected_names(good_plan(discriminates_between=["B", "Q"])))

    def test_a_planner_cannot_choose_its_own_threshold(self):
        self.assertIn("expected_signature_wellformed",
                      rejected_names(good_plan(expected_signature={
                          "metric": "p95_ms", "op": "<=", "relative_to": "control",
                          "factor": 50.0})))

    def test_a_planner_cannot_reintroduce_an_absolute_millisecond_threshold(self):
        self.assertIn("schema", rejected_names(good_plan(expected_signature={
            "metric": "p95_ms", "op": "<=", "relative_to": "control",
            "factor": 2.5, "value_ms": 25.0})))

    def test_a_reference_other_than_the_live_control_is_rejected(self):
        self.assertIn("expected_signature_wellformed",
                      rejected_names(good_plan(expected_signature={
                          "metric": "p95_ms", "op": "<=",
                          "relative_to": "stored_baseline", "factor": 2.5})))

    def test_a_metric_of_the_planners_own_choosing_is_rejected(self):
        self.assertIn("expected_signature_wellformed",
                      rejected_names(good_plan(expected_signature={
                          "metric": "vibes", "op": "<=", "relative_to": "control",
                          "factor": 2.5})))

    def test_an_encoded_verdict_key_is_rejected(self):
        self.assertIn("schema", rejected_names(good_plan(verdict="PROVEN")))

    def test_a_verdict_smuggled_into_a_structural_field_is_rejected(self):
        self.assertIn("no_encoded_verdict",
                      rejected_names(good_plan(fixture_id="incident-001",
                                               discriminates_between=["B", "PROVEN"])))

    def test_a_missing_required_field_is_rejected(self):
        plan = good_plan()
        del plan["reasoning_summary"]
        self.assertIn("schema", rejected_names(plan))

    def test_a_non_object_plan_is_rejected(self):
        self.assertIn("schema", rejected_names("just a string"))


class ReasoningIsProseTests(unittest.TestCase):
    """reasoning_summary is quoted on screen and never read by the engine, so
    verdict language in it is flagged rather than rejected - and cannot change
    anything."""

    SMUGGLED = ("B is definitively PROVEN to be the root cause; the experiment "
                "will confirm it.")

    def test_verdict_language_in_reasoning_is_accepted_but_flagged(self):
        report = validate(good_plan(reasoning_summary=self.SMUGGLED), REQUEST)
        self.assertTrue(report.accepted)
        self.assertTrue(report.reasoning_flagged)

    def test_reasoning_cannot_change_the_phases_that_run(self):
        clean = validate(good_plan(), REQUEST).plan
        smuggled = validate(good_plan(reasoning_summary=self.SMUGGLED), REQUEST).plan
        self.assertNotEqual(clean.reasoning_summary, smuggled.reasoning_summary)
        self.assertEqual(planner.phases_for(clean, STATE),
                         planner.phases_for(smuggled, STATE))

    def test_reasoning_cannot_change_the_verdict(self):
        """Prose claiming PROVEN, over measurements that mean REFUTED."""
        plan = validate(good_plan(reasoning_summary=self.SMUGGLED), REQUEST).plan
        measured = {verdict.PHASE_CONTROL_1: 23.0, verdict.PHASE_REPRODUCE: 330.0,
                    verdict.PHASE_CONTROL_2: 24.0, verdict.PHASE_ABLATE: 315.0,
                    verdict.PHASE_CONTROL_3: 22.0, verdict.PHASE_RESTORE: 330.0,
                    verdict.PHASE_CONTROL_4: 23.0}
        results = [verdict.PhaseResult(spec, {"p95_ms": measured[spec.phase]})
                   for spec in planner.phases_for(plan, STATE)]
        self.assertEqual(verdict.decide(results), verdict.REFUTED)


class FallbackTests(unittest.TestCase):
    """Any planner failure at all lands in the same place, and the outcome says
    so honestly."""

    class Exploding:
        name, kind, available = "flaky-model", "gemini", True

        def propose(self, request, schema=None):
            raise RuntimeError("connection reset by peer")

    class Unavailable:
        name, kind, available = "gemini:none", "gemini", False

        def propose(self, request, schema=None):
            raise ProviderUnavailable("no API key configured")

    class Garbage:
        name, kind, available = "garbage-model", "gemini", True

        def propose(self, request, schema=None):
            return {"nonsense": True}

    class Smuggler:
        name, kind, available = "cheating-model", "gemini", True

        def propose(self, request, schema=None):
            plan = DeterministicPlanner().propose(request)
            plan["verdict"] = "PROVEN"
            return plan

    def _plan_with(self, provider):
        return planner.plan_experiment(REQUEST, provider)

    def test_an_exception_falls_back_and_says_why(self):
        outcome = self._plan_with(self.Exploding())
        self.assertEqual(outcome.kind, "deterministic")
        self.assertTrue(outcome.used_fallback)
        self.assertIn("connection reset", outcome.fallback_reason)
        self.assertEqual(outcome.proposed_by, "flaky-model")

    def test_an_unavailable_provider_falls_back(self):
        outcome = self._plan_with(self.Unavailable())
        self.assertEqual(outcome.kind, "deterministic")
        self.assertIn("no API key", outcome.fallback_reason)

    def test_malformed_output_falls_back(self):
        outcome = self._plan_with(self.Garbage())
        self.assertEqual(outcome.kind, "deterministic")
        self.assertIn("validator rejected", outcome.fallback_reason)

    def test_a_smuggled_verdict_falls_back_and_names_the_check(self):
        outcome = self._plan_with(self.Smuggler())
        self.assertEqual(outcome.kind, "deterministic")
        self.assertIn("schema", outcome.fallback_reason)

    def test_the_fallback_always_produces_a_runnable_plan(self):
        for provider in (self.Exploding(), self.Garbage(), self.Smuggler()):
            outcome = self._plan_with(provider)
            self.assertTrue(outcome.report.accepted)
            self.assertEqual(len(planner.phases_for(outcome.plan, STATE)), 7)

    def test_provenance_never_claims_ai_when_the_fallback_ran(self):
        """The one dishonest thing Causeway could do."""
        for provider in (self.Exploding(), self.Unavailable(), self.Garbage()):
            provenance = self._plan_with(provider).as_dict()["provenance"]
            self.assertEqual(provenance["kind"], "deterministic")
            self.assertEqual(provenance["source"], "deterministic")
            self.assertTrue(provenance["used_fallback"])
            self.assertTrue(provenance["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
