"""The standard repository path: a normal public GitHub repository with no
causeway.json.

The fixture below is a byte-for-byte copy of the actual repository this was
reported against - https://github.com/bhoomikmoharana/test - captured once
(git ls-remote HEAD 8dd0bcc47f2422498407b497538af37c9778eeff) so this suite
never depends on the network or on that repository staying reachable or
unchanged. No live GitHub anywhere in this file: causeway.repository.clone is
redirected at a local git repository, the same seam
tests/test_repository_end_to_end.py already uses.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from causeway import orchestrator
from causeway.patch.gemini import GeminiPatchPlanner
from causeway.repository import git as repogit
from causeway.repository import has_manifest
from causeway.repository.standard import (detect_python, detect_tests,
                                          discover_sources, guess_entrypoint,
                                          load_standard)
from tests.repo_fixtures import local_repo

REPO_URL = "https://github.com/bhoomikmoharana/test"

# The real content of the reported repository's one file.
BROKEN_SOURCE = "import random\nfor i in apple:\n    \n"

FIXED_SOURCE = (
    "import random\n\napple = list(range(10))\nfor i in apple:\n    pass\n")


def _cloning_from(local_source: str):
    def _clone(ref, timeout=repogit.CLONE_TIMEOUT, source=None):
        return repogit.clone(ref, timeout=timeout, source=local_source)
    return _clone


def _envelope(patch: dict) -> dict:
    return {"candidates": [{"content": {"role": "model",
                                        "parts": [{"text": json.dumps(patch)}]}}]}


class DetectionTests(unittest.TestCase):
    """No network, no git - just the filesystem heuristics."""

    def test_a_single_py_file_with_no_project_markers_is_still_detected(self):
        with local_repo({"test.py": BROKEN_SOURCE}) as root:
            self.assertTrue(detect_python(root))
            self.assertFalse(has_manifest(root))

    def test_a_repository_with_only_a_requirements_file_is_detected(self):
        with local_repo({"requirements.txt": "flask==3.0\n"}) as root:
            self.assertTrue(detect_python(root))

    def test_a_repository_with_no_python_signal_at_all_is_not_detected(self):
        with local_repo({"README.md": "just words"}) as root:
            self.assertFalse(detect_python(root))

    def test_discover_sources_finds_and_reads_the_one_file(self):
        with local_repo({"test.py": BROKEN_SOURCE}) as root:
            chosen, contents, all_files = discover_sources(root, "fix the bug")
            self.assertEqual(chosen, ["test.py"])
            self.assertEqual(contents["test.py"], BROKEN_SOURCE)
            self.assertEqual(all_files, ["test.py"])

    def test_an_entrypoint_name_is_preferred_when_present(self):
        with local_repo({"app.py": "print(1)\n", "helpers.py": "print(2)\n"}) as root:
            chosen, _contents, _all = discover_sources(root)
            self.assertEqual(guess_entrypoint(chosen), "app.py")

    def test_no_recognisable_entrypoint_is_reported_as_empty_not_guessed(self):
        self.assertEqual(guess_entrypoint(["test.py"]), "")

    def test_tests_are_detected_by_filename(self):
        with local_repo({"test.py": "x = 1\n"}) as root:
            found, _note = detect_tests(root, ["test.py"])
            self.assertFalse(found)   # "test.py" is not "test_*.py"
        with local_repo({"tests/test_thing.py": "x = 1\n"}) as root:
            found, note = detect_tests(root, ["tests/test_thing.py"])
            self.assertTrue(found)
            self.assertIn("does not install", note)


class LoadStandardRejectionTests(unittest.TestCase):
    def test_a_non_python_repository_is_rejected_by_analysis_not_by_manifest(self):
        from causeway.repository.errors import RepositoryRejected
        from causeway.repository.urlcheck import RepoRef

        with local_repo({"README.md": "nothing here"}) as root:
            with self.assertRaises(RepositoryRejected) as caught:
                load_standard(
                    repogit.ClonedRepo(path=root, commit_sha="x" * 40, workdir=root),
                    RepoRef(owner="o", name="n", url=REPO_URL))
        self.assertEqual(caught.exception.stage, "analysis")
        self.assertNotIn("causeway.json", caught.exception.reason)


class EndToEndAgainstTheReportedRepositoryTests(unittest.TestCase):
    """The exact bug report: a normal public Python repository with no
    causeway.json must be read, proposed against, and patched - not
    rejected as unsupported. Gemini is mocked (no live key in CI, and no
    dependency on a stranger's repository staying reachable), but the
    mock's own assertions prove the REAL cloned source reached the prompt
    Gemini was sent, and that its response drives a real diff against the
    real file."""

    @classmethod
    def setUpClass(cls):
        with local_repo({"test.py": BROKEN_SOURCE}) as source:
            patch = {
                "summary": "Fix the undefined name and empty loop body",
                "files": [{"path": "test.py",
                          "hunks": [{"before": BROKEN_SOURCE, "after": FIXED_SOURCE}]}],
                "reasoning_summary": ("apple was never defined and the for-loop body "
                                     "was empty, which is a SyntaxError - defined a "
                                     "list to iterate and gave the loop a body."),
            }
            transport = mock.Mock(return_value=_envelope(patch))
            with mock.patch("causeway.patch.gemini.api_key_from_env",
                            return_value="fake-test-key"), \
                 mock.patch.object(GeminiPatchPlanner, "_post", transport), \
                 mock.patch("causeway.repository.clone", _cloning_from(source)):
                cls.events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=False,
                    instruction="Fix the bug in this repository and explain the changes",
                    mode="diagnose_and_fix"))
            cls.transport = transport

    def _of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]

    # -- the repository was accepted, not rejected for lacking a manifest --

    def test_no_rejection_event_is_emitted(self):
        self.assertEqual(self._of("repository_rejected"), [])

    def test_the_repository_is_loaded_as_a_standard_contract(self):
        loaded = self._of("repository_loaded")[0]
        self.assertEqual(loaded["contract"], "standard")
        self.assertIsNone(loaded["database"])
        self.assertIsNone(loaded["workload"])
        self.assertEqual(loaded["sources"], ["test.py"])

    # -- the user's instruction actually reached the engine ----------------

    def test_the_instruction_is_carried_into_the_intent_event(self):
        intent_event = self._of("intent")[0]
        self.assertEqual(intent_event["raw_instruction"],
                         "Fix the bug in this repository and explain the changes")
        self.assertNotEqual(intent_event["mode"], "needs_clarification")

    # -- real repository content reached Gemini -----------------------------

    def test_gemini_was_actually_called(self):
        self.assertEqual(self.transport.call_count, 1)

    def test_the_real_broken_source_reached_the_gemini_prompt(self):
        body = self.transport.call_args.args[2]
        prompt = body["contents"][0]["parts"][0]["text"]
        self.assertIn("for i in apple", prompt)
        self.assertIn("test.py", prompt)

    def test_the_prompt_never_reveals_a_verdict_the_engine_has_not_reached(self):
        body = self.transport.call_args.args[2]
        prompt = body["contents"][0]["parts"][0]["text"].upper()
        for word in ("VERIFIED", "IMPLEMENTED_VERIFICATION_INCOMPLETE"):
            self.assertNotIn(word, prompt)

    # -- Gemini's proposal produced a real, validated patch ------------------

    def test_a_patch_plan_event_carries_geminis_proposal(self):
        plans = self._of("patch_plan")
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["provenance"]["kind"], "gemini")
        self.assertFalse(plans[0]["provenance"]["used_fallback"])

    def test_the_patch_validator_accepted_it(self):
        validations = self._of("patch_validation")
        self.assertEqual(len(validations), 1)
        self.assertTrue(validations[0]["accepted"])

    def test_the_actual_diff_is_shown_against_the_real_file(self):
        applied = self._of("patch_apply")
        self.assertEqual(len(applied), 1)
        diff = applied[0]["diff"]
        self.assertIn("--- a/test.py", diff)
        self.assertIn("+++ b/test.py", diff)
        self.assertIn("+apple = list(range(10))", diff)
        self.assertIn("+    pass", diff)

    # -- verification is honest: never claims more than a syntax check ------

    def test_the_verdict_is_implemented_verification_incomplete_not_verified(self):
        verdicts = self._of("requested_change_verdict")
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], "IMPLEMENTED_VERIFICATION_INCOMPLETE")
        self.assertNotEqual(verdicts[0]["verdict"], "VERIFIED")

    def test_a_syntax_check_actually_ran_against_the_patched_copy(self):
        checks = self._of("syntax_check")
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["file"], "test.py")
        self.assertTrue(checks[0]["passed"])

    def test_the_run_ends_cleanly(self):
        self.assertEqual(self.events[-1]["type"], "done")


class BrokenPatchIsCaughtByTheSyntaxCheckTests(unittest.TestCase):
    """If Gemini's proposal does not actually parse, the verdict must say
    FAILED - never IMPLEMENTED, since nothing was in fact implemented."""

    def test_a_patch_that_does_not_compile_is_reported_failed(self):
        with local_repo({"test.py": BROKEN_SOURCE}) as source:
            still_broken = "import random\nfor i in apple\n    pass\n"   # missing ':'
            patch = {
                "summary": "botched fix",
                "files": [{"path": "test.py",
                          "hunks": [{"before": BROKEN_SOURCE, "after": still_broken}]}],
                "reasoning_summary": "attempted a fix",
            }
            transport = mock.Mock(return_value=_envelope(patch))
            with mock.patch("causeway.patch.gemini.api_key_from_env",
                            return_value="fake-test-key"), \
                 mock.patch.object(GeminiPatchPlanner, "_post", transport), \
                 mock.patch("causeway.repository.clone", _cloning_from(source)):
                events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=False,
                    instruction="fix the syntax error", mode="requested_change"))
        verdicts = [e for e in events if e["type"] == "requested_change_verdict"]
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], "FAILED")


class DiagnoseOnlyGeneratesNoPatchTests(unittest.TestCase):
    def test_diagnose_only_never_calls_gemini_or_applies_anything(self):
        with local_repo({"test.py": BROKEN_SOURCE}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=True,
                    instruction="just tell me what's wrong, do not fix it",
                    mode=None))
        types = [e["type"] for e in events]
        self.assertIn("patch_rejected", types)
        for forbidden in ("patch_plan", "patch_apply", "requested_change_verdict"):
            self.assertNotIn(forbidden, types)


if __name__ == "__main__":
    unittest.main()
