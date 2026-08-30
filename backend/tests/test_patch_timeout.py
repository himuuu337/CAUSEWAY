"""The Gemini patch-planner timeout: its own default, its own environment
variable, sensible bounds, and what a dashboard is allowed to say when it
fires.

Mirrors tests/test_fix_gemini.py's transport-mocking pattern - nothing here
touches the network. No live GitHub either: the end-to-end class redirects
causeway.repository.clone at a local git repository, the same seam
tests/test_repository_end_to_end.py already uses.
"""
from __future__ import annotations

import socket
import unittest
from unittest import mock

from causeway import orchestrator
from causeway.patch import (NO_PATCH_REASON, TIMEOUT_REASON,
                            display_rejection_reason, plan_patch)
from causeway.patch.deterministic import DeterministicPatchPlanner
from causeway.patch.gemini import (DEFAULT_TIMEOUT, MAX_TIMEOUT, MIN_TIMEOUT,
                                   TIMEOUT_ENV_VAR, GeminiPatchPlanner,
                                   timeout_from_env)
from causeway.patch.schema import PatchRequest, ProviderTimeout, ProviderUnavailable
from causeway.patch.validator import PatchValidationReport
from causeway.repository import git as repogit
from tests.repo_fixtures import local_repo

KEY = "fake-test-key"


def request_for(instruction="fix the bug in this repository"):
    return PatchRequest(
        instruction=instruction, goal=instruction, intent={}, service="demo",
        entrypoint="", sources=("app.py",), patchable=("app.py",),
        file_contents={"app.py": "def broken():\n    pass\n"}, acceptance={})


# ------------------------------------------------------- the default itself

class DefaultTimeoutTests(unittest.TestCase):
    def test_the_default_is_90_seconds(self):
        self.assertEqual(DEFAULT_TIMEOUT, 90.0)

    def test_an_unset_environment_variable_uses_the_default(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop(TIMEOUT_ENV_VAR, None)
            self.assertEqual(timeout_from_env(), 90.0)

    def test_a_freshly_constructed_planner_uses_the_default_when_unset(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop(TIMEOUT_ENV_VAR, None)
            planner = GeminiPatchPlanner(api_key=KEY)
        self.assertEqual(planner.timeout, 90.0)

    def test_this_is_its_own_variable_not_the_one_the_other_two_planners_share(self):
        """Widening the patch planner's timeout must never silently widen
        (or narrow) causeway.planner.gemini's or causeway.fixer.gemini's -
        those send small, structured requests and have no reason to wait as
        long as a patch request carrying real repository source might."""
        self.assertEqual(TIMEOUT_ENV_VAR, "CAUSEWAY_GEMINI_PATCH_TIMEOUT_SECONDS")
        self.assertNotEqual(TIMEOUT_ENV_VAR, "CAUSEWAY_GEMINI_TIMEOUT")


# ---------------------------------------------------- configuring the timeout

class ConfiguredTimeoutTests(unittest.TestCase):
    def _with_env(self, value):
        import os
        return mock.patch.dict(os.environ, {TIMEOUT_ENV_VAR: value})

    def test_a_valid_value_is_honoured(self):
        with self._with_env("45"):
            self.assertEqual(timeout_from_env(), 45.0)

    def test_a_valid_value_reaches_a_freshly_constructed_planner(self):
        with self._with_env("45"):
            planner = GeminiPatchPlanner(api_key=KEY)
        self.assertEqual(planner.timeout, 45.0)

    def test_an_explicit_constructor_timeout_still_overrides_the_environment(self):
        with self._with_env("45"):
            planner = GeminiPatchPlanner(api_key=KEY, timeout=12.0)
        self.assertEqual(planner.timeout, 12.0)


class InvalidTimeoutFallsBackSafelyTests(unittest.TestCase):
    def _with_env(self, value):
        import os
        return mock.patch.dict(os.environ, {TIMEOUT_ENV_VAR: value})

    def test_non_numeric_text_falls_back_to_the_default(self):
        with self._with_env("not-a-number"):
            self.assertEqual(timeout_from_env(), DEFAULT_TIMEOUT)

    def test_an_empty_string_falls_back_to_the_default(self):
        with self._with_env("   "):
            self.assertEqual(timeout_from_env(), DEFAULT_TIMEOUT)

    def test_nan_falls_back_to_the_default(self):
        with self._with_env("nan"):
            self.assertEqual(timeout_from_env(), DEFAULT_TIMEOUT)

    def test_a_negative_value_is_clamped_up_to_the_minimum(self):
        with self._with_env("-5"):
            self.assertEqual(timeout_from_env(), MIN_TIMEOUT)

    def test_zero_is_clamped_up_to_the_minimum(self):
        with self._with_env("0"):
            self.assertEqual(timeout_from_env(), MIN_TIMEOUT)

    def test_an_absurdly_large_value_is_clamped_down_to_the_maximum(self):
        with self._with_env("999999"):
            self.assertEqual(timeout_from_env(), MAX_TIMEOUT)

    def test_infinity_is_clamped_down_to_the_maximum(self):
        """A hard ceiling regardless of what the environment says - a
        request may never be allowed to hang indefinitely."""
        with self._with_env("inf"):
            self.assertEqual(timeout_from_env(), MAX_TIMEOUT)

    def test_the_maximum_is_itself_finite(self):
        self.assertLess(MAX_TIMEOUT, float("inf"))
        self.assertGreater(MAX_TIMEOUT, 0)


# --------------------------------------------------------------- the failure

class TimeoutRaisesADistinctExceptionTests(unittest.TestCase):
    def test_a_socket_timeout_from_the_transport_becomes_provider_timeout(self):
        def timing_out(url, headers, body):
            raise socket.timeout("timed out")

        planner = GeminiPatchPlanner(api_key=KEY, timeout=1.0, transport=timing_out)
        with self.assertRaises(ProviderTimeout) as caught:
            planner.propose(request_for())
        self.assertIn("timed out after 1s", str(caught.exception))

    def test_provider_timeout_is_still_a_provider_unavailable(self):
        """Every existing `except ProviderUnavailable` in this codebase must
        keep catching a timeout too - this is a narrower type, not a
        different one."""
        self.assertTrue(issubclass(ProviderTimeout, ProviderUnavailable))


class PlanPatchTimeoutTests(unittest.TestCase):
    def _timing_out_provider(self):
        def timing_out(url, headers, body):
            raise socket.timeout("timed out")
        return GeminiPatchPlanner(api_key=KEY, timeout=1.0, transport=timing_out)

    def test_a_timeout_produces_no_patch(self):
        with local_repo({"app.py": "def broken():\n    pass\n"}) as root:
            outcome = plan_patch(request_for(), self._timing_out_provider(), root)
        self.assertIsNone(outcome.patch)
        self.assertFalse(outcome.report.accepted)

    def test_a_timeout_is_flagged_on_the_outcome(self):
        with local_repo({"app.py": "def broken():\n    pass\n"}) as root:
            outcome = plan_patch(request_for(), self._timing_out_provider(), root)
        self.assertTrue(outcome.timed_out)

    def test_a_non_timeout_failure_is_not_flagged_as_one(self):
        def refuses(url, headers, body):
            raise RuntimeError("boom")
        provider = GeminiPatchPlanner(api_key=KEY, timeout=1.0, transport=refuses)
        with local_repo({"app.py": "def broken():\n    pass\n"}) as root:
            outcome = plan_patch(request_for(), provider, root)
        self.assertFalse(outcome.timed_out)

    def test_the_deterministic_fallback_itself_timing_out_is_not_meaningful(self):
        """The fallback never makes a network call, so it cannot time out -
        this just proves the flag stays false for it, for completeness."""
        with local_repo({"app.py": "def broken():\n    pass\n"}) as root:
            outcome = plan_patch(request_for("reject orders with a negative quantity"),
                                 DeterministicPatchPlanner(), root)
        self.assertFalse(outcome.timed_out)


# -------------------------------------------------------- the clean message

class DisplayRejectionReasonTests(unittest.TestCase):
    def _make_outcome(self, timed_out=False, checks=(), fallback_reason="", reason_code=""):
        from causeway.patch import PatchOutcome
        return PatchOutcome(patch=None, report=PatchValidationReport(tuple(checks)),
                            source="x", kind="gemini", timed_out=timed_out,
                            fallback_reason=fallback_reason, reason_code=reason_code)

    def test_a_timeout_gets_the_exact_clean_sentence(self):
        outcome = self._make_outcome(timed_out=True, fallback_reason=(
            "Gemini timed out after 25s; the deterministic fallback also declined: "
            "the deterministic fallback only recognises requests to reject "
            "non-positive order quantities; 'fix the bug' does not match"))
        self.assertEqual(display_rejection_reason(outcome), TIMEOUT_REASON)

    def test_the_narrow_fallback_pattern_never_appears_in_a_timeout_message(self):
        outcome = self._make_outcome(timed_out=True, fallback_reason=(
            "the deterministic fallback only recognises requests to reject "
            "non-positive order quantities; 'fix the bug' does not match"))
        shown = display_rejection_reason(outcome)
        self.assertNotIn("order quantities", shown)
        self.assertNotIn("non-positive", shown)

    def test_no_candidate_at_all_gets_the_generic_clean_message(self):
        """Reachable without any timeout at all - e.g. offline mode with an
        instruction the deterministic fallback does not recognise. The raw
        pattern text must not leak here either."""
        outcome = self._make_outcome(timed_out=False, checks=(), fallback_reason=(
            "the deterministic fallback only recognises requests to reject "
            "non-positive order quantities; 'fix the bug' does not match"))
        shown = display_rejection_reason(outcome)
        self.assertEqual(shown, NO_PATCH_REASON)
        self.assertNotIn("order quantities", shown)

    def test_a_real_validator_rejection_is_shown_with_its_check_names(self):
        """A validator rejection is useful, safe information - it names
        which deterministic check failed, never an internal pattern - and
        must not be flattened into the generic message."""
        from causeway.planner.schema import Check
        checks = (Check("before_text_matches_current_source_exactly", False,
                        "the before-text does not match"),)
        outcome = self._make_outcome(
            timed_out=False, checks=checks, reason_code="PATCH_VALIDATION_REJECTED",
            fallback_reason=("the patch validator rejected the proposal: "
                            "before_text_matches_current_source_exactly"))
        shown = display_rejection_reason(outcome)
        self.assertIn("before_text_matches_current_source_exactly", shown)


# --------------------------------------------------- end to end, real path

REPO_URL = "https://github.com/o/n"
TS_FIXTURE = {
    "package.json": '{"name": "demo"}\n',
    "tsconfig.json": "{}\n",
    "src/index.ts": "function total(items: number[]): number {\n  return items.length;\n}\n",
}


def _cloning_from(local_source):
    def _clone(ref, timeout=repogit.CLONE_TIMEOUT, source=None):
        return repogit.clone(ref, timeout=timeout, source=local_source)
    return _clone


class EndToEndTimeoutNeverReachesApplicationTests(unittest.TestCase):
    """The exact reported scenario: a repository whose bounded context is
    larger than the tiny demo fixtures, an instruction that does not match
    the narrow deterministic fallback, and a Gemini call that times out."""

    @classmethod
    def setUpClass(cls):
        transport = mock.Mock(side_effect=socket.timeout("timed out"))

        with local_repo(TS_FIXTURE) as source:
            with mock.patch("causeway.patch.gemini.api_key_from_env",
                            return_value=KEY), \
                 mock.patch.object(GeminiPatchPlanner, "_post", transport), \
                 mock.patch("causeway.repository.clone", _cloning_from(source)):
                cls.events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=False,
                    instruction="improve the total() helper somehow",
                    mode="diagnose_and_fix"))
        cls.transport = transport

    def _of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]

    def test_the_dashboard_reason_is_the_exact_clean_timeout_sentence(self):
        rejected = self._of("patch_rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], TIMEOUT_REASON)

    def test_the_narrow_fallback_implementation_detail_never_reaches_the_reason_field(self):
        rejected = self._of("patch_rejected")[0]
        self.assertNotIn("order quantities", rejected["reason"])
        self.assertNotIn("non-positive", rejected["reason"])

    def test_the_raw_detail_is_still_available_for_backend_logs(self):
        """Not thrown away - just not the thing the dashboard leads with."""
        rejected = self._of("patch_rejected")[0]
        self.assertIn("timed out", rejected["detail"])

    def test_no_patch_was_ever_proposed_shown_or_applied(self):
        for forbidden in ("patch_plan", "patch_apply", "verification_check",
                         "requested_change_verdict"):
            self.assertEqual(self._of(forbidden), [],
                             "%s must not appear after a timeout" % forbidden)

    def test_exactly_one_gemini_request_was_attempted_no_retry(self):
        self.assertEqual(self.transport.call_count, 1)

    def test_the_run_ends_cleanly(self):
        self.assertEqual(self.events[-1]["type"], "done")


if __name__ == "__main__":
    unittest.main()
