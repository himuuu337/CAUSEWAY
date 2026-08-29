"""The requested-change path through the real orchestrator: an instruction
that is not a diagnosis, a real (local, disposable) clone, a Gemini-shaped
CodePatch from the deterministic fallback, the patch validator, and real HTTP
requests against disposable, patched and unpatched copies of the service.

No live GitHub and no live Gemini anywhere in this file - `offline=True`
forces the deterministic planner, exactly as tests/test_repository_end_to_end.py
does for the diagnose/diagnose-and-fix path.
"""
from __future__ import annotations

import hashlib
import os
import unittest
from unittest import mock

from causeway import orchestrator
from causeway.repository import git as repogit
from tests.repo_fixtures import local_repo

REPO_URL = "https://github.com/causeway-demo/causeway-demo"
INSTRUCTION = "Reject orders with a quantity of zero or less."


def _hash(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _cloning_from(local_source: str):
    def _clone(ref, timeout=repogit.CLONE_TIMEOUT, source=None):
        return repogit.clone(ref, timeout=timeout, source=local_source)
    return _clone


def _run(instruction=INSTRUCTION, mode="requested_change"):
    with local_repo(copy_demo=True) as source:
        app_py_before = _hash(os.path.join(source, "app.py"))
        with mock.patch("causeway.repository.clone", _cloning_from(source)):
            events = list(orchestrator.investigate(
                repository_url=REPO_URL, offline=True,
                instruction=instruction, mode=mode))
        app_py_after = _hash(os.path.join(source, "app.py"))
    return events, app_py_before, app_py_after


class RequestedChangeEndToEndTests(unittest.TestCase):
    """One real run: the deterministic fallback patch, applied to a
    disposable copy, verified by real requests before and after."""

    @classmethod
    def setUpClass(cls):
        cls.events, cls.app_py_before, cls.app_py_after = _run()

    def _of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]

    def test_the_repository_being_investigated_is_never_modified(self):
        self.assertEqual(self.app_py_before, self.app_py_after)

    def test_no_ab_candidate_event_is_ever_emitted(self):
        types = [e["type"] for e in self.events]
        self.assertNotIn("candidates", types)
        self.assertNotIn("observational", types)

    def test_this_mode_never_runs_the_hypothesis_investigation(self):
        """A requested change is not a diagnosis of the latency incident -
        nothing about it should be measured for a change unrelated to it."""
        types = [e["type"] for e in self.events]
        for forbidden in ("phase_start", "phase_result", "verdict", "root_cause_proven"):
            self.assertNotIn(forbidden, types)

    def test_a_patch_is_proposed_and_validated(self):
        plans = self._of("patch_plan")
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["provenance"]["kind"], "deterministic")
        validations = self._of("patch_validation")
        self.assertEqual(len(validations), 1)
        self.assertTrue(validations[0]["accepted"])

    def test_the_patch_is_shown_as_a_diff_against_a_patchable_file(self):
        applied = self._of("patch_apply")
        self.assertEqual(len(applied), 1)
        self.assertIn("app.py", applied[0]["files"])
        self.assertIn("--- a/app.py", applied[0]["diff"])
        self.assertIn("+++ b/app.py", applied[0]["diff"])
        self.assertIn("quantity <= 0", applied[0]["diff"])

    def test_before_and_after_are_both_probed_with_real_requests(self):
        cases = self._of("verification_case")
        before = [c for c in cases if c["phase"] == "before"]
        after = [c for c in cases if c["phase"] == "after"]
        self.assertEqual(len(before), 3)
        self.assertEqual(len(after), 3)

    def test_the_bug_reproduces_against_the_unpatched_copy(self):
        before = [c for c in self._of("verification_case") if c["phase"] == "before"]
        zero = next(c for c in before if c["case"] == "zero_quantity_is_rejected")
        self.assertEqual(zero["status"], 201)          # the bug: accepted, not rejected
        self.assertFalse(zero["passed"])

    def test_the_fix_holds_against_the_patched_copy(self):
        after = [c for c in self._of("verification_case") if c["phase"] == "after"]
        for case in after:
            with self.subTest(case=case["case"]):
                self.assertTrue(case["passed"], case)

    def test_the_verdict_is_verified(self):
        verdicts = self._of("requested_change_verdict")
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], "VERIFIED")

    def test_the_run_ends_cleanly(self):
        self.assertEqual(self.events[-1]["type"], "done")


class DiagnoseOnlyStillWorksAlongsideRequestedChangeTests(unittest.TestCase):
    """The mode switch must not have broken the other two modes."""

    def test_diagnose_only_still_reaches_a_verdict(self):
        events, _before, _after = _run(
            instruction="Find why it is slow. Do not modify anything.",
            mode="diagnose_only")
        types = [e["type"] for e in events]
        self.assertIn("verdict", types)
        self.assertIn("fix_skipped", types)
        for forbidden in ("patch_plan", "patch_apply", "requested_change_verdict"):
            self.assertNotIn(forbidden, types)


class NoDeclaredProbesTests(unittest.TestCase):
    """A repository with no probes cannot have a requested change verified
    against it - Causeway says so rather than faking a result."""

    def test_a_repository_without_probes_is_told_it_cannot_be_verified(self):
        import json

        demo_manifest_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "demo-repo", "causeway.json")
        with open(demo_manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        del manifest["probes"]

        with local_repo(copy_demo=True,
                        files={"causeway.json": json.dumps(manifest)}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=True,
                    instruction=INSTRUCTION, mode="requested_change"))
        types = [e["type"] for e in events]
        self.assertIn("patch_rejected", types)
        self.assertNotIn("patch_apply", types)


if __name__ == "__main__":
    unittest.main()
