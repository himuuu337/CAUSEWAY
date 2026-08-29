"""The Gemini fix planner: what it may propose, and what it may never reach.

Every test here mocks the transport, mirroring tests/test_gemini.py exactly -
nothing here touches the network.
"""
from __future__ import annotations

import json
import socket
import unittest
import urllib.error

from causeway import fix_verdict, verdict
from causeway.fixer import build_fix_request, plan_fix
from causeway.fixer.deterministic import DeterministicFixPlanner
from causeway.fixer.gemini import GeminiFixPlanner, build_prompt
from causeway.fixer.schema import ProviderUnavailable

CANDIDATE_B = {"change_id": "B", "branch": "perf/normalise-audit-predicate",
               "summary": "Normalise order_id inside the audit predicate",
               "lines_changed": 3, "files_changed": 1}

KEY = "AIzaSy-not-a-real-key-0123456789"


def request_for(hypothesis="B"):
    return build_fix_request(CANDIDATE_B, hypothesis, verdict.PROVEN,
                             "removing it recovered, restoring it broke it again")


def good_fix(**overrides):
    fix = DeterministicFixPlanner().propose(request_for())
    fix["reasoning_summary"] = (
        "Replace the wrapped predicate with a bare column comparison so the "
        "existing index on order_id can be used again.")
    fix.update(overrides)
    return fix


def envelope(fix) -> dict:
    text = fix if isinstance(fix, str) else json.dumps(fix)
    return {"candidates": [{"content": {"role": "model",
                                        "parts": [{"text": text}]}}]}


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


HTTP_500 = urllib.error.HTTPError("https://example.invalid", 500, "Server Error", {}, None)
HTTP_429 = urllib.error.HTTPError("https://example.invalid", 429, "Too Many Requests", {}, None)
UNREACHABLE = urllib.error.URLError("getaddrinfo failed")


def gemini(transport, key=KEY, model="gemini-fix-test"):
    return GeminiFixPlanner(api_key=key, model=model, timeout=5.0, transport=transport)


def outcome_from(provider, hypothesis="B"):
    return plan_fix(request_for(hypothesis), provider)


class AcceptedFixTests(unittest.TestCase):
    def setUp(self):
        self.transport = returns(envelope(good_fix()))
        self.outcome = outcome_from(gemini(self.transport))

    def test_the_fix_is_accepted(self):
        self.assertTrue(self.outcome.report.accepted)
        self.assertEqual(self.outcome.report.as_dict()["passed"],
                         self.outcome.report.as_dict()["total"])

    def test_provenance_says_gemini_and_not_fallback(self):
        provenance = self.outcome.as_dict()["provenance"]
        self.assertEqual(provenance["kind"], "gemini")
        self.assertEqual(provenance["source"], "gemini:gemini-fix-test")
        self.assertFalse(provenance["used_fallback"])
        self.assertEqual(provenance["fallback_reason"], "")

    def test_the_request_carries_the_structured_schema_not_free_text(self):
        config = self.transport.calls[0]["body"]["generationConfig"]
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertIn("responseSchema", config)

    def test_the_fix_verdict_module_is_never_read_or_written(self):
        """Gemini is not shown a fix verdict outcome, and the outcome does not
        (and structurally cannot) compute one - plan_fix never calls
        causeway.fix_verdict at all."""
        for word in (fix_verdict.VERIFIED, fix_verdict.FAILED, fix_verdict.UNRESOLVED):
            self.assertNotIn(word, json.dumps(self.outcome.as_dict()))


class FallbackTests(unittest.TestCase):
    def assert_fell_back(self, provider, expect_in_reason=None):
        outcome = outcome_from(provider)
        provenance = outcome.as_dict()["provenance"]
        self.assertEqual(provenance["kind"], "deterministic")
        self.assertEqual(provenance["source"], "deterministic")
        self.assertTrue(provenance["used_fallback"])
        self.assertTrue(provenance["fallback_reason"])
        self.assertEqual(provenance["proposed_by"], "gemini:gemini-fix-test")
        self.assertTrue(outcome.report.accepted)
        if expect_in_reason:
            self.assertIn(expect_in_reason, provenance["fallback_reason"])
        return outcome

    def test_malformed_json_falls_back(self):
        self.assert_fell_back(gemini(returns(envelope("not json at all"))))

    def test_an_empty_response_falls_back(self):
        self.assert_fell_back(gemini(returns({})))

    def test_a_schema_invalid_fix_falls_back(self):
        fix = good_fix()
        del fix["reasoning_summary"]
        self.assert_fell_back(gemini(returns(envelope(fix))), "schema")

    def test_a_validator_rejected_fix_falls_back(self):
        """A proposal naming an unregistered target."""
        fix = good_fix()
        fix["operation"] = dict(fix["operation"], target="ANYTHING_ELSE")
        self.assert_fell_back(gemini(returns(envelope(fix))),
                              "target_is_known_repair_surface")

    def test_an_unsafe_after_value_falls_back(self):
        fix = good_fix()
        fix["operation"] = dict(fix["operation"], after="order_id + 0 = ?")
        self.assert_fell_back(gemini(returns(envelope(fix))),
                              "after_state_is_a_known_safe_repair")

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


class NoKeyTests(unittest.TestCase):
    def test_a_keyless_provider_refuses_before_any_request(self):
        transport = returns(envelope(good_fix()))
        provider = GeminiFixPlanner(api_key="", transport=transport)
        with self.assertRaises(ProviderUnavailable):
            provider.propose(request_for())
        self.assertEqual(transport.calls, [], "no request may be attempted")

    def test_a_deterministic_run_is_not_labelled_a_fallback(self):
        outcome = outcome_from(DeterministicFixPlanner())
        provenance = outcome.as_dict()["provenance"]
        self.assertEqual(provenance["kind"], "deterministic")
        self.assertFalse(provenance["used_fallback"])
        self.assertEqual(provenance["fallback_reason"], "")


class NoVerdictFromTheModelTests(unittest.TestCase):
    """Gemini may propose a fix. It may not decide whether it worked, and
    there is no path by which it could - plan_fix never runs the sandbox."""

    def test_a_fix_carrying_a_verified_key_is_rejected(self):
        fix = good_fix()
        fix["verified"] = True
        outcome = outcome_from(gemini(returns(envelope(fix))))
        self.assertTrue(outcome.used_fallback)

    def test_a_fix_carrying_a_verdict_word_in_its_after_state_falls_back(self):
        fix = good_fix()
        fix["operation"] = dict(fix["operation"], after="order_id = ?  -- VERIFIED")
        outcome = outcome_from(gemini(returns(envelope(fix))))
        self.assertTrue(outcome.used_fallback)

    def test_verdict_language_in_reasoning_is_accepted_but_flagged(self):
        fix = good_fix(reasoning_summary="This fix is definitely VERIFIED to work.")
        outcome = outcome_from(gemini(returns(envelope(fix))))
        self.assertTrue(outcome.report.accepted)
        self.assertTrue(outcome.report.reasoning_flagged)
        self.assertEqual(outcome.as_dict()["provenance"]["kind"], "gemini")

    def test_plan_fix_never_imports_the_fix_verdict_decision_function(self):
        import inspect

        import causeway.fixer as fixer_pkg
        source = inspect.getsource(fixer_pkg)
        self.assertNotIn("fix_verdict.decide", source)
        self.assertNotIn("fix_verdict.VERIFIED", source)
        self.assertNotIn("fix_verdict.FAILED", source)


class InformationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.prompt = build_prompt(request_for("B"))
        self.transport = returns(envelope(good_fix()))
        outcome_from(gemini(self.transport))
        self.sent = json.dumps(self.transport.calls[0]["body"])

    def test_the_prompt_contains_no_fix_verdict_word(self):
        """PROVEN/REFUTED are legitimate context here - the fix planner is
        only ever asked about a hypothesis the causal experiment has already
        decided, and it needs to know that to make sense of the request. What
        must never appear is the FIX's own outcome word: no fix-verification
        has happened yet when this prompt is built."""
        upper = self.prompt.upper()
        for word in ("VERIFIED", "FAILED", "UNRESOLVED"):
            self.assertNotIn(word, upper, "%s leaked into the fix planner prompt" % word)

    def test_the_prompt_states_the_hypothesis_was_proven(self):
        """The one exception, and it is deliberate: the fix planner is only
        ever invoked for a PROVEN hypothesis, and is told so as the reason a
        fix is being requested at all - not as a hint about a future result."""
        self.assertIn("PROVEN", self.prompt.upper())

    def test_the_prompt_never_quotes_the_known_safe_repair_value(self):
        """The known-safe repair is what the fix validator checks a proposal
        against - the prompt must not simply hand the model the answer."""
        self.assertNotIn("order_id = ?", self.prompt)

    def test_the_prompt_contains_no_fix_verification_measurement(self):
        """No millisecond figure of any kind - none exists yet, since fix
        planning happens strictly before the fix sandbox ever runs."""
        import re
        lowered = self.prompt.lower()
        found = re.findall(r"\d+(?:\.\d+)?\s*(?:ms|milliseconds|s\b)", lowered)
        self.assertEqual(found, [], "a measurement leaked into the fix planner prompt: %r" % found)
        for token in ("fix-before", "fix-after", "fix-control", "local_control_ms",
                      "controls_agree"):
            self.assertNotIn(token, lowered)

    def test_the_request_has_no_field_that_could_carry_a_fix_result(self):
        import dataclasses

        from causeway.fixer.schema import FixRequest
        fields = {f.name for f in dataclasses.fields(FixRequest)}
        for forbidden in ("results", "observed", "measurements", "verdict",
                         "verified", "failed", "phases", "control", "p95_ms", "ratio"):
            self.assertNotIn(forbidden, fields)

    def test_nothing_but_the_prompt_and_schema_is_sent(self):
        body = self.transport.calls[0]["body"]
        self.assertEqual(sorted(body), ["contents", "generationConfig", "systemInstruction"])

    def test_the_system_instruction_states_the_boundary(self):
        instruction = self.transport.calls[0]["body"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("You do not know whether your fix will verify", instruction)
        self.assertIn("Do not provide or predict whether the fix will be VERIFIED "
                      "or FAILED", instruction)


class SecretTests(unittest.TestCase):
    def test_the_key_is_sent_as_a_header_not_in_the_url(self):
        transport = returns(envelope(good_fix()))
        outcome_from(gemini(transport))
        call = transport.calls[0]
        self.assertEqual(call["headers"]["x-goog-api-key"], KEY)
        self.assertNotIn(KEY, call["url"])

    def test_the_key_is_never_in_the_prompt_or_the_body(self):
        transport = returns(envelope(good_fix()))
        outcome_from(gemini(transport))
        self.assertNotIn(KEY, json.dumps(transport.calls[0]["body"]))

    def test_the_key_is_stripped_from_error_messages(self):
        provider = gemini(raises(RuntimeError("bad key %s rejected" % KEY)))
        outcome = outcome_from(provider)
        self.assertNotIn(KEY, outcome.fallback_reason)
        self.assertIn("***", outcome.fallback_reason)

    def test_the_key_is_never_in_anything_the_browser_receives(self):
        for provider in (gemini(returns(envelope(good_fix()))),
                         gemini(raises(RuntimeError("auth failed for %s" % KEY)))):
            self.assertNotIn(KEY, json.dumps(outcome_from(provider).as_dict()))


if __name__ == "__main__":
    unittest.main()
