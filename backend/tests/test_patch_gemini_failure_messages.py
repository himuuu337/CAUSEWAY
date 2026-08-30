"""A real Gemini failure (bad key, wrong model, rate limit, malformed
response) must not look like "your instruction was too vague" to a
dashboard - that was the actual behaviour before this fix, and it is the
most plausible reason a working Gemini integration reads as "unreliable":
every operational failure collapsed into the same generic
"provide a more specific problem description" message NO_PATCH_REASON
already carries for a genuinely vague instruction.

Mirrors tests/test_patch_timeout.py's transport-mocking and end-to-end
conventions exactly - no live network, no live GitHub.
"""
from __future__ import annotations

import unittest
import urllib.error
from unittest import mock

from causeway import orchestrator
from causeway.patch import (GEMINI_MALFORMED_RESPONSE_REASON, GEMINI_RATE_LIMIT_REASON,
                            GEMINI_UNAVAILABLE_REASON, NO_PATCH_REASON, PatchOutcome,
                            display_rejection_reason)
from causeway.patch.gemini import GeminiPatchPlanner
from causeway.patch.schema import (GEMINI_HTTP_ERROR, GEMINI_INVALID_JSON,
                                   GEMINI_INVALID_RESPONSE, GEMINI_RATE_LIMIT,
                                   UNKNOWN_PLANNER_FAILURE)
from causeway.patch.validator import PatchValidationReport
from causeway.repository import git as repogit
from tests.repo_fixtures import local_repo

KEY = "fake-test-key"
REPO_URL = "https://github.com/o/n"


def _outcome(reason_code: str, fallback_reason: str = "opaque internal detail") -> PatchOutcome:
    return PatchOutcome(patch=None, report=PatchValidationReport(()), source="x", kind="gemini",
                        reason_code=reason_code, fallback_reason=fallback_reason)


class DistinctMessagesPerFailureClassTests(unittest.TestCase):
    def test_a_rate_limit_gets_its_own_actionable_message(self):
        self.assertEqual(display_rejection_reason(_outcome(GEMINI_RATE_LIMIT)),
                         GEMINI_RATE_LIMIT_REASON)

    def test_an_http_error_gets_its_own_actionable_message(self):
        self.assertEqual(display_rejection_reason(_outcome(GEMINI_HTTP_ERROR)),
                         GEMINI_UNAVAILABLE_REASON)

    def test_an_invalid_response_gets_its_own_actionable_message(self):
        self.assertEqual(display_rejection_reason(_outcome(GEMINI_INVALID_RESPONSE)),
                         GEMINI_MALFORMED_RESPONSE_REASON)

    def test_invalid_json_gets_the_same_malformed_response_message(self):
        self.assertEqual(display_rejection_reason(_outcome(GEMINI_INVALID_JSON)),
                         GEMINI_MALFORMED_RESPONSE_REASON)

    def test_the_four_new_messages_are_all_distinct_from_each_other_and_from_no_patch(self):
        messages = {GEMINI_RATE_LIMIT_REASON, GEMINI_UNAVAILABLE_REASON,
                   GEMINI_MALFORMED_RESPONSE_REASON, NO_PATCH_REASON}
        self.assertEqual(len(messages), 4)

    def test_none_of_the_new_messages_leak_the_raw_provider_text(self):
        for code in (GEMINI_RATE_LIMIT, GEMINI_HTTP_ERROR, GEMINI_INVALID_RESPONSE,
                    GEMINI_INVALID_JSON):
            with self.subTest(code=code):
                shown = display_rejection_reason(_outcome(code))
                self.assertNotIn("opaque internal detail", shown)

    def test_an_unrecognised_or_unknown_code_still_gets_the_generic_message(self):
        """Unchanged behaviour: this is the honest "we don't know why, and
        it might genuinely be the instruction" case, not a regression."""
        self.assertEqual(display_rejection_reason(_outcome(UNKNOWN_PLANNER_FAILURE)),
                         NO_PATCH_REASON)
        self.assertEqual(display_rejection_reason(_outcome("")), NO_PATCH_REASON)

    def test_a_rate_limit_message_actually_mentions_waiting(self):
        self.assertIn("wait", GEMINI_RATE_LIMIT_REASON.lower())

    def test_an_unavailable_message_points_at_configuration_not_the_instruction(self):
        lowered = GEMINI_UNAVAILABLE_REASON.lower()
        self.assertIn("gemini_api_key", lowered)
        self.assertNotIn("more specific", lowered)


def _cloning_from(local_source):
    def _clone(ref, timeout=repogit.CLONE_TIMEOUT, source=None):
        return repogit.clone(ref, timeout=timeout, source=local_source)
    return _clone


class EndToEndHttpErrorTests(unittest.TestCase):
    """The exact reported scenario: a misconfigured or unreachable Gemini -
    here, a 404 an invalid model name would actually produce - must not
    read as "your instruction needs to be more specific" on the dashboard."""

    @classmethod
    def setUpClass(cls):
        def raises_404(url, headers, body):
            raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None,
                                         fp=__import__("io").BytesIO(b'{"error": "model not found"}'))

        with local_repo({"app.py": "def broken():\n    pass\n"}) as source:
            with mock.patch("causeway.patch.gemini.api_key_from_env", return_value=KEY), \
                 mock.patch.object(GeminiPatchPlanner, "_post", side_effect=raises_404), \
                 mock.patch("causeway.repository.clone", _cloning_from(source)):
                cls.events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=False,
                    instruction="fix the off-by-one bug in broken()",
                    mode="diagnose_and_fix"))

    def _of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]

    def test_the_dashboard_reason_names_a_configuration_problem_not_the_instruction(self):
        rejected = self._of("patch_rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], GEMINI_UNAVAILABLE_REASON)
        self.assertNotIn("more specific", rejected[0]["reason"])

    def test_the_raw_http_error_is_still_available_in_detail_for_logs(self):
        rejected = self._of("patch_rejected")[0]
        self.assertIn("404", rejected["detail"])

    def test_the_generation_failed_event_carries_the_http_error_reason_code(self):
        failed = self._of("patch_generation_failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["reason_code"], GEMINI_HTTP_ERROR)


if __name__ == "__main__":
    unittest.main()
