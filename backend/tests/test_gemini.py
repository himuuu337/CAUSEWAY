"""The Gemini planner: what it may propose, and what it may never reach.

Every test here mocks the transport. Nothing in this file touches the network,
so the suite is the same on a plane as it is on stage.

    AI PROPOSES.  CODE VALIDATES.  SYSTEM EXPERIMENTS.  MEASUREMENTS DECIDE.

These tests are the first two clauses: Gemini's output is a proposal that goes
through the same eight checks the offline planner's output goes through, and
every possible failure lands in the same place.
"""
from __future__ import annotations

import json
import socket
import unittest
import urllib.error

from causeway import planner, verdict
from causeway.incident import deploy_record
from causeway.localizer import localize
from causeway import observational
from causeway.planner.deterministic import DeterministicPlanner
from causeway.planner.gemini import GeminiPlanner, build_prompt
from causeway.planner.schema import ProviderUnavailable

RECORD = deploy_record()
CANDIDATES, _ = localize(RECORD)
SCORES = observational.rank(CANDIDATES, RECORD["incident"])
STATE = {c.change_id: True for c in CANDIDATES}

KEY = "AIzaSy-not-a-real-key-0123456789"


def request_for(target="B"):
    return planner.build_request(RECORD["incident"], CANDIDATES, STATE,
                                 ["incident-001"], target, observational=SCORES)


def good_plan(target="B", **overrides):
    plan = DeterministicPlanner().propose(request_for(target))
    plan["reasoning_summary"] = (
        "Remove %s while holding the other change fixed and replay the recorded "
        "traffic; if it is causal, latency should return near the control." % target)
    plan.update(overrides)
    return plan


def envelope(plan) -> dict:
    """What generateContent actually returns around the JSON."""
    text = plan if isinstance(plan, str) else json.dumps(plan)
    return {"candidates": [{"content": {"role": "model",
                                        "parts": [{"text": text}]}}]}


# ----------------------------------------------------------------- transports

def returns(payload):
    def transport(url, headers, body):
        transport.calls.append({"url": url, "headers": headers, "body": body})
        return payload
    transport.calls = []
    return transport


def raises(error):
    def transport(url, headers, body):
        transport.calls.append({"url": url, "headers": headers, "body": body})
        raise error
    transport.calls = []
    return transport


HTTP_500 = urllib.error.HTTPError("https://example.invalid", 500, "Server Error",
                                  {}, None)
HTTP_429 = urllib.error.HTTPError("https://example.invalid", 429, "Too Many Requests",
                                  {}, None)
UNREACHABLE = urllib.error.URLError("getaddrinfo failed")


def gemini(transport, key=KEY, model="gemini-test"):
    return GeminiPlanner(api_key=key, model=model, timeout=5.0, transport=transport)


def outcome_from(provider, target="B"):
    return planner.plan_experiment(request_for(target), provider)


def lowered_of(text: str) -> str:
    return text.lower()


# --------------------------------------------------------------- the happy path

class AcceptedPlanTests(unittest.TestCase):
    """1. a well-formed Gemini response becomes an accepted ExperimentSpec."""

    def setUp(self):
        self.transport = returns(envelope(good_plan()))
        self.outcome = outcome_from(gemini(self.transport))

    def test_the_plan_is_accepted(self):
        self.assertTrue(self.outcome.report.accepted)
        self.assertEqual(self.outcome.report.as_dict()["passed"], 8)

    def test_the_plan_is_the_one_gemini_proposed(self):
        plan = self.outcome.plan
        self.assertEqual(plan.hypothesis_id, "B")
        self.assertEqual(plan.intervention, {"flag": "B", "value": False})
        self.assertEqual(plan.fixture_id, "incident-001")
        self.assertIn("Remove B", plan.reasoning_summary)

    def test_provenance_says_gemini_and_not_fallback(self):
        provenance = self.outcome.as_dict()["provenance"]
        self.assertEqual(provenance["kind"], "gemini")
        self.assertEqual(provenance["source"], "gemini:gemini-test")
        self.assertFalse(provenance["used_fallback"])
        self.assertEqual(provenance["fallback_reason"], "")

    def test_an_accepted_plan_still_produces_the_engine_s_own_seven_phases(self):
        """The plan contributes which change to remove and which fixture to
        replay. The protocol is built by causeway.verdict either way."""
        phases = planner.phases_for(self.outcome.plan, STATE)
        self.assertEqual([spec.phase for spec in phases], list(verdict.PHASES))

    def test_the_request_carries_the_structured_schema_not_free_text(self):
        config = self.transport.calls[0]["body"]["generationConfig"]
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertIn("responseSchema", config)
        self.assertEqual(config["responseSchema"]["required"],
                         ["hypothesis_id", "intervention", "fixture_id",
                          "expected_signature", "discriminates_between",
                          "reasoning_summary"])


# ------------------------------------------------------------ every failure path

class FallbackTests(unittest.TestCase):
    """2-6. Anything at all going wrong lands in the same place, and the
    outcome says so rather than pretending a model was involved."""

    def assert_fell_back(self, provider, expect_in_reason=None):
        outcome = outcome_from(provider)
        provenance = outcome.as_dict()["provenance"]
        self.assertEqual(provenance["kind"], "deterministic")
        self.assertEqual(provenance["source"], "deterministic")
        self.assertTrue(provenance["used_fallback"])
        self.assertTrue(provenance["fallback_reason"])
        self.assertEqual(provenance["proposed_by"], "gemini:gemini-test")
        # and the investigation can still run
        self.assertTrue(outcome.report.accepted)
        self.assertEqual(len(planner.phases_for(outcome.plan, STATE)), 7)
        if expect_in_reason:
            self.assertIn(expect_in_reason, provenance["fallback_reason"])
        return outcome

    def test_malformed_json_falls_back(self):
        self.assert_fell_back(gemini(returns(envelope("this is not JSON at all"))))

    def test_an_empty_response_falls_back(self):
        self.assert_fell_back(gemini(returns({})))

    def test_a_response_that_is_not_a_plan_falls_back(self):
        self.assert_fell_back(gemini(returns(envelope({"answer": 42}))))

    def test_a_schema_invalid_plan_falls_back(self):
        plan = good_plan()
        del plan["expected_signature"]
        self.assert_fell_back(gemini(returns(envelope(plan))), "schema")

    def test_an_extra_field_falls_back(self):
        """A plan may not smuggle a field the engine does not read."""
        self.assert_fell_back(
            gemini(returns(envelope(good_plan(hint="test B first")))), "schema")

    def test_a_validator_rejected_plan_falls_back(self):
        """Intervening on a different change than the hypothesis under test."""
        self.assert_fell_back(
            gemini(returns(envelope(good_plan(intervention={"flag": "A", "value": False})))),
            "single_independent_variable")

    def test_a_plan_naming_an_unknown_candidate_falls_back(self):
        self.assert_fell_back(
            gemini(returns(envelope(good_plan(hypothesis_id="Z",
                                              intervention={"flag": "Z", "value": False})))))

    def test_a_plan_inventing_an_intervention_falls_back(self):
        self.assert_fell_back(
            gemini(returns(envelope(good_plan(intervention={"flag": "restart_db",
                                                            "value": False})))))

    def test_a_plan_choosing_its_own_threshold_falls_back(self):
        self.assert_fell_back(gemini(returns(envelope(good_plan(
            expected_signature={"metric": "p95_ms", "op": "<=",
                                "relative_to": "control", "factor": 40.0})))),
            "expected_signature_wellformed")

    def test_a_plan_pinning_an_absolute_millisecond_threshold_falls_back(self):
        self.assert_fell_back(gemini(returns(envelope(good_plan(
            expected_signature={"metric": "p95_ms", "op": "<=",
                                "relative_to": "control", "factor": 2.5,
                                "value_ms": 25.0})))), "schema")

    def test_an_http_error_falls_back(self):
        self.assert_fell_back(gemini(raises(HTTP_500)), "HTTP 500")

    def test_rate_limiting_falls_back(self):
        self.assert_fell_back(gemini(raises(HTTP_429)), "HTTP 429")

    def test_an_unreachable_api_falls_back(self):
        self.assert_fell_back(gemini(raises(UNREACHABLE)), "unreachable")

    def test_a_timeout_falls_back(self):
        self.assert_fell_back(gemini(raises(socket.timeout("timed out"))), "timed out")

    def test_an_unexpected_exception_falls_back(self):
        self.assert_fell_back(gemini(raises(ValueError("something odd"))))


# ------------------------------------------------------------------- no API key

class NoKeyTests(unittest.TestCase):
    """7. A machine with no key runs deterministically. That is a deterministic
    RUN, not a fallback, and nothing may call it one."""

    def test_the_default_provider_is_deterministic_without_a_key(self):
        provider = GeminiPlanner(api_key="", transport=returns({}))
        self.assertFalse(provider.available)

    def test_a_keyless_gemini_provider_refuses_before_any_request(self):
        transport = returns(envelope(good_plan()))
        provider = GeminiPlanner(api_key="", transport=transport)
        with self.assertRaises(ProviderUnavailable):
            provider.propose(request_for())
        self.assertEqual(transport.calls, [], "no request may be attempted")

    def test_a_deterministic_run_is_not_labelled_a_fallback(self):
        provenance = outcome_from(DeterministicPlanner()).as_dict()["provenance"]
        self.assertEqual(provenance["kind"], "deterministic")
        self.assertFalse(provenance["used_fallback"])
        self.assertEqual(provenance["fallback_reason"], "")
        self.assertEqual(provenance["proposed_by"], "deterministic")

    def test_offline_is_deterministic_even_with_a_key_present(self):
        self.assertEqual(planner.default_provider(offline=True).kind, "deterministic")


# ------------------------------------------------------ Gemini cannot decide

class NoVerdictFromTheModelTests(unittest.TestCase):
    """8. The model may propose an experiment. It may not decide the outcome,
    and there is no path by which it could."""

    def test_a_plan_carrying_a_verdict_key_is_rejected(self):
        outcome = outcome_from(gemini(returns(envelope(good_plan(verdict="PROVEN")))))
        self.assertEqual(outcome.as_dict()["provenance"]["kind"], "deterministic")
        self.assertTrue(outcome.used_fallback)

    def test_a_plan_carrying_a_confidence_score_is_rejected(self):
        outcome = outcome_from(gemini(returns(envelope(good_plan(confidence=0.97)))))
        self.assertTrue(outcome.used_fallback)

    def test_a_verdict_word_in_a_structural_field_is_rejected(self):
        outcome = outcome_from(gemini(returns(envelope(
            good_plan(discriminates_between=["B", "PROVEN"])))))
        self.assertTrue(outcome.used_fallback)

    def test_verdict_language_in_the_reasoning_is_accepted_but_flagged(self):
        """reasoning_summary is prose for a human. It is quoted on screen and
        never read by the engine, so it is flagged rather than rejected."""
        outcome = outcome_from(gemini(returns(envelope(good_plan(
            reasoning_summary="B is definitively PROVEN to be the root cause.")))))
        self.assertTrue(outcome.report.accepted)
        self.assertTrue(outcome.report.reasoning_flagged)
        self.assertEqual(outcome.as_dict()["provenance"]["kind"], "gemini")

    def test_that_reasoning_cannot_change_the_verdict(self):
        """Prose claiming PROVEN, over measurements that mean REFUTED."""
        outcome = outcome_from(gemini(returns(envelope(good_plan(
            reasoning_summary="B is definitively PROVEN to be the root cause.")))))
        measured = {verdict.PHASE_CONTROL_1: 23.0, verdict.PHASE_REPRODUCE: 330.0,
                    verdict.PHASE_CONTROL_2: 24.0, verdict.PHASE_ABLATE: 315.0,
                    verdict.PHASE_CONTROL_3: 22.0, verdict.PHASE_RESTORE: 330.0,
                    verdict.PHASE_CONTROL_4: 23.0}
        results = [verdict.PhaseResult(spec, {"p95_ms": measured[spec.phase]})
                   for spec in planner.phases_for(outcome.plan, STATE)]
        self.assertEqual(verdict.decide(results), verdict.REFUTED)

    def test_the_verdict_function_cannot_be_handed_a_plan(self):
        import inspect
        self.assertEqual(list(inspect.signature(verdict.decide).parameters),
                         ["results"])


# --------------------------------------------------------- information boundary

class InformationBoundaryTests(unittest.TestCase):
    """9. The planner is given pre-experiment evidence and nothing else."""

    def setUp(self):
        self.prompt = build_prompt(request_for("B"))
        self.transport = returns(envelope(good_plan()))
        outcome_from(gemini(self.transport))
        self.sent = json.dumps(self.transport.calls[0]["body"])

    def test_the_prompt_contains_no_verdict_word(self):
        upper = self.prompt.upper()
        for word in ("PROVEN", "REFUTED", "SUPPORTED", "UNRESOLVED"):
            self.assertNotIn(word, upper, "%s leaked into the planner prompt" % word)

    def test_the_prompt_names_no_phase_of_the_experiment(self):
        """The engine's judging RULE is fair game - the model has to know the
        recovery factor is relative to a control. What it may not see is any
        phase of an experiment that has run."""
        lowered = self.prompt.lower()
        for token in ("ablate", "ablation", "reproduce", "restore", "recheck",
                      "control-1", "control-2", "control-3", "control-4",
                      "phase_result", "phase_judged", "healthy_p95",
                      "incident_p95"):
            self.assertNotIn(token, lowered,
                             "%r leaked into the planner prompt" % token)

    def test_the_prompt_contains_no_measured_latency(self):
        """No millisecond figure of any kind: not a control, not an incident
        p95, not a calibration number."""
        import re
        found = re.findall(r"\d+(?:\.\d+)?\s*(?:ms|milliseconds|s\b)", lowered_of(self.prompt))
        self.assertEqual(found, [], "a latency leaked into the planner prompt: %r" % found)

    def test_the_prompt_never_says_which_candidate_is_the_cause(self):
        lowered = self.prompt.lower()
        for phrase in ("is the cause", "is the root cause", "actual cause",
                       "the decoy", "innocent", "b caused", "known cause"):
            self.assertNotIn(phrase, lowered)

    def test_the_request_has_no_field_that_could_carry_a_result(self):
        """Structural, not textual: PlanRequest simply has nowhere to put one."""
        import dataclasses
        from causeway.planner.schema import PlanRequest
        fields = {f.name for f in dataclasses.fields(PlanRequest)}
        for forbidden in ("results", "observed", "measurements", "verdict",
                          "phases", "control", "p95_ms", "ratio", "outcome"):
            self.assertNotIn(forbidden, fields)

    def test_observational_scores_are_allowed_evidence(self):
        """They say how suspicious a change looks, not whether it is causal."""
        self.assertIn("correlation-only score 0.961", self.prompt)
        self.assertIn("correlation-only score 0.567", self.prompt)

    def test_nothing_but_the_prompt_and_schema_is_sent(self):
        body = self.transport.calls[0]["body"]
        self.assertEqual(sorted(body), ["contents", "generationConfig",
                                        "systemInstruction"])

    def test_the_system_instruction_states_the_boundary(self):
        instruction = self.transport.calls[0]["body"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("You do not know the experiment outcome", instruction)
        self.assertIn("Do not provide or predict the final root-cause verdict",
                      instruction)
        self.assertIn("exactly one independent variable", instruction)


# ------------------------------------------------------------------- provenance

class ProvenanceTests(unittest.TestCase):
    """10 and 11. The interface may only say Gemini when Gemini's plan ran."""

    def test_gemini_only_after_an_accepted_gemini_plan(self):
        accepted = outcome_from(gemini(returns(envelope(good_plan()))))
        self.assertEqual(accepted.as_dict()["provenance"]["kind"], "gemini")

    def test_never_gemini_when_the_attempt_failed(self):
        for provider in (gemini(raises(HTTP_500)),
                         gemini(raises(socket.timeout("timed out"))),
                         gemini(returns(envelope("nonsense"))),
                         gemini(returns(envelope(good_plan(verdict="PROVEN"))))):
            provenance = outcome_from(provider).as_dict()["provenance"]
            self.assertNotEqual(provenance["kind"], "gemini")
            self.assertTrue(provenance["used_fallback"])

    def test_the_three_states_are_distinguishable(self):
        gemini_run = outcome_from(gemini(returns(envelope(good_plan())))).as_dict()["provenance"]
        fallback_run = outcome_from(gemini(raises(HTTP_500))).as_dict()["provenance"]
        offline_run = outcome_from(DeterministicPlanner()).as_dict()["provenance"]

        self.assertEqual(
            (gemini_run["kind"], gemini_run["used_fallback"]), ("gemini", False))
        self.assertEqual(
            (fallback_run["kind"], fallback_run["used_fallback"]), ("deterministic", True))
        self.assertEqual(
            (offline_run["kind"], offline_run["used_fallback"]), ("deterministic", False))


# --------------------------------------------------------------------- secrets

class SecretTests(unittest.TestCase):
    """The key travels in one header and appears nowhere else, ever."""

    def test_the_key_is_sent_as_a_header_not_in_the_url(self):
        transport = returns(envelope(good_plan()))
        outcome_from(gemini(transport))
        call = transport.calls[0]
        self.assertEqual(call["headers"]["x-goog-api-key"], KEY)
        self.assertNotIn(KEY, call["url"])
        self.assertNotIn("key=", call["url"])

    def test_the_key_is_never_in_the_prompt_or_the_body(self):
        transport = returns(envelope(good_plan()))
        outcome_from(gemini(transport))
        self.assertNotIn(KEY, json.dumps(transport.calls[0]["body"]))

    def test_the_key_is_stripped_from_error_messages(self):
        """An exception carrying the key would put it in the event stream."""
        provider = gemini(raises(RuntimeError("bad key %s rejected" % KEY)))
        outcome = outcome_from(provider)
        self.assertNotIn(KEY, outcome.fallback_reason)
        self.assertIn("***", outcome.fallback_reason)

    def test_the_key_is_never_in_anything_the_browser_receives(self):
        for provider in (gemini(returns(envelope(good_plan()))),
                         gemini(raises(RuntimeError("auth failed for %s" % KEY)))):
            self.assertNotIn(KEY, json.dumps(outcome_from(provider).as_dict()))

    def test_the_provider_name_exposes_the_model_and_nothing_else(self):
        self.assertEqual(gemini(returns({})).name, "gemini:gemini-test")


# ----------------------------------------------- the verdict stays AI-independent

class EngineIndependenceTests(unittest.TestCase):
    """12. Adding a model to the project must not have moved the boundary."""

    def test_the_verdict_cannot_reach_the_gemini_module(self):
        from tests.test_no_model_in_verdict import reachable
        reached = reachable("causeway.verdict")
        self.assertNotIn("causeway.planner.gemini", reached)
        self.assertEqual(
            sorted(name for name in reached if "gemini" in name.lower()), [])

    def test_the_verdict_still_cannot_reach_the_network(self):
        from tests.test_no_model_in_verdict import reachable
        network = {"http", "http.client", "urllib", "urllib.request", "socket"}
        self.assertEqual(sorted(reachable("causeway.verdict") & network), [])

    def test_the_planner_is_the_side_that_depends_on_the_engine(self):
        from tests.test_no_model_in_verdict import reachable
        self.assertIn("causeway.verdict", reachable("causeway.planner"))
        self.assertNotIn("causeway.planner", reachable("causeway.verdict"))


if __name__ == "__main__":
    unittest.main()
