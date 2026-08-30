"""Structured patch-generation diagnostics: reason codes, the
patch_generation_failed event, and the vague-instruction guard that stops
Causeway from guessing at a fix with no concrete defect to act on.
"""
from __future__ import annotations

import unittest
from unittest import mock

from causeway import orchestrator
from causeway.patch import (NEEDS_CLARIFICATION_REASON, check_actionable,
                            message_for_reason_code)
from causeway.patch.schema import (NEEDS_CLARIFICATION, NO_ACTIONABLE_DEFECT_FOUND,
                                   PatchRequest, REASON_CODES, SOURCE_CONTEXT_INSUFFICIENT)
from causeway.repository import git as repogit
from tests.repo_fixtures import local_repo

REPO_URL = "https://github.com/o/n"


def _request(instruction="", file_contents=None):
    return PatchRequest(
        instruction=instruction, goal=instruction, intent={}, service="demo",
        entrypoint="", sources=("app.py",), patchable=("app.py",),
        file_contents=file_contents if file_contents is not None
        else {"app.py": "def broken():\n    pass\n"}, acceptance={})


def _cloning_from(local_source):
    def _clone(ref, timeout=repogit.CLONE_TIMEOUT, source=None):
        return repogit.clone(ref, timeout=timeout, source=local_source)
    return _clone


class ReasonCodesAreDeclaredTests(unittest.TestCase):
    def test_every_required_reason_code_exists(self):
        required = ("GEMINI_TIMEOUT", "GEMINI_HTTP_ERROR", "GEMINI_RATE_LIMIT",
                   "GEMINI_INVALID_RESPONSE", "GEMINI_INVALID_JSON",
                   "GEMINI_SCHEMA_ERROR", "EMPTY_PATCH", "PATCH_VALIDATION_REJECTED",
                   "SOURCE_CONTEXT_INSUFFICIENT", "UNKNOWN_PLANNER_FAILURE")
        for name in required:
            with self.subTest(name=name):
                self.assertIn(name, REASON_CODES)


class CheckActionableTests(unittest.TestCase):
    def test_an_empty_instruction_needs_clarification(self):
        self.assertEqual(check_actionable(_request("")), NEEDS_CLARIFICATION)
        self.assertEqual(check_actionable(_request("   ")), NEEDS_CLARIFICATION)

    def test_a_bare_vague_instruction_has_no_actionable_defect(self):
        for phrase in ("fix the code", "fix it", "improve this", "clean up"):
            with self.subTest(phrase=phrase):
                self.assertEqual(check_actionable(_request(phrase)),
                                 NO_ACTIONABLE_DEFECT_FOUND)

    def test_a_specific_instruction_is_actionable(self):
        self.assertIsNone(check_actionable(
            _request("fix the off-by-one error in total() in orders.ts")))
        self.assertIsNone(check_actionable(
            _request("reject orders with a non-positive quantity")))

    def test_empty_source_context_is_insufficient_even_with_a_specific_instruction(self):
        request = _request("fix the off-by-one error in total()", file_contents={})
        self.assertEqual(check_actionable(request), SOURCE_CONTEXT_INSUFFICIENT)

    def test_the_message_never_reveals_internal_pattern_text(self):
        message = message_for_reason_code(NO_ACTIONABLE_DEFECT_FOUND)
        self.assertEqual(message, NEEDS_CLARIFICATION_REASON)
        self.assertNotIn("order quantities", message)


class VagueInstructionGuardEndToEndTests(unittest.TestCase):
    """The guard fires before any provider is asked - proven by never
    mocking a transport at all here and still getting a clean rejection
    instead of an error about a missing API key."""

    def test_a_vague_instruction_is_rejected_without_calling_any_provider(self):
        with local_repo({"app.py": "def broken():\n    pass\n"}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=True,
                    instruction="fix the code", mode="diagnose_and_fix"))
        types = [e["type"] for e in events]
        self.assertIn("patch_generation_failed", types)
        self.assertIn("patch_rejected", types)
        for forbidden in ("patch_plan", "patch_apply", "verification_check",
                         "requested_change_verdict"):
            self.assertNotIn(forbidden, types)

        failed = [e for e in events if e["type"] == "patch_generation_failed"][0]
        self.assertEqual(failed["reason_code"], "NO_ACTIONABLE_DEFECT_FOUND")
        self.assertIn("selected_file_count", failed)
        self.assertIn("selected_char_count", failed)

        rejected = [e for e in events if e["type"] == "patch_rejected"][0]
        self.assertEqual(rejected["reason"], NEEDS_CLARIFICATION_REASON)

    def test_a_specific_instruction_is_not_blocked_by_the_guard(self):
        """Offline mode with an instruction the deterministic fallback does
        not recognise still reaches plan_patch (and fails there, honestly)
        rather than being stopped by the vague-instruction guard."""
        with local_repo({"app.py": "def broken():\n    pass\n"}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=True,
                    instruction="fix the off-by-one bug in broken()",
                    mode="diagnose_and_fix"))
        failed = [e for e in events if e["type"] == "patch_generation_failed"]
        self.assertEqual(len(failed), 1)
        self.assertNotEqual(failed[0]["reason_code"], "NO_ACTIONABLE_DEFECT_FOUND")
        self.assertNotEqual(failed[0]["reason_code"], "NEEDS_CLARIFICATION")
        self.assertEqual(failed[0]["stage"], "gemini")


if __name__ == "__main__":
    unittest.main()
