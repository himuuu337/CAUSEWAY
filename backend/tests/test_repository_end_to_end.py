"""The whole repository path through the real orchestrator: a repository URL
and a user instruction in, a real (local, disposable) clone, a real manifest
check, a database built from the repository's own schema, hypotheses read out
of its own source, real source-variant experiments and real verdicts.

No live GitHub anywhere in this file: causeway.repository.clone is redirected
at a local git repository built by tests.repo_fixtures, the same seam
causeway.repository.git.clone's own `source` parameter exists for. That is
the only thing mocked - validation, the manifest check, the actual git
subprocess, the database build, the detectors, the sandbox, the measurements
and both verdicts all run for real.
"""
from __future__ import annotations

import hashlib
import os
import unittest
from unittest import mock

from causeway import config, orchestrator
from causeway.repository import git as repogit
from causeway.sandbox import service as bundled_service
from tests.repo_fixtures import local_repo

REPO_URL = "https://github.com/causeway-demo/causeway-demo"


def _hash(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _run(repository_url=None, **kwargs):
    return list(orchestrator.investigate(repository_url=repository_url,
                                         offline=True, **kwargs))


def _cloning_from(local_source: str):
    """A causeway.repository.clone replacement that clones from a local
    directory instead of the real GitHub URL a RepoRef names - the same
    `source` escape hatch causeway.repository.git.clone exposes for exactly
    this purpose. Everything else about clone() runs unmocked: the real git
    subprocess, the real temp workspace, the real cleanup."""
    def _clone(ref, timeout=repogit.CLONE_TIMEOUT, source=None):
        return repogit.clone(ref, timeout=timeout, source=local_source)
    return _clone


class RepositorySourcedInvestigationTests(unittest.TestCase):
    """One real end-to-end run, its events inspected from every angle."""

    @classmethod
    def setUpClass(cls):
        cls.service_hash_before = _hash(bundled_service.__file__)
        with local_repo(copy_demo=True) as source:
            cls.source_root = source
            cls.db_py_before = _hash(os.path.join(source, "db.py"))
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                cls.events = _run(repository_url=REPO_URL,
                                  instruction="Find why it is slow and fix it")
            cls.db_py_after = _hash(os.path.join(source, "db.py"))
        cls.service_hash_after = _hash(bundled_service.__file__)

    def _of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]

    # -- nothing outside a disposable copy is ever written to ---------------

    def test_the_bundled_sandbox_service_source_is_never_modified(self):
        self.assertEqual(self.service_hash_before, self.service_hash_after)

    def test_the_repository_being_investigated_is_never_modified(self):
        self.assertEqual(self.db_py_before, self.db_py_after)

    # -- the bundled A/B fixture is never substituted -----------------------

    def test_no_ab_candidate_event_is_ever_emitted(self):
        types = [e["type"] for e in self.events]
        self.assertNotIn("candidates", types)
        self.assertNotIn("observational", types)

    def test_no_hypothesis_is_called_A_or_B(self):
        for event in self._of("verdict"):
            self.assertNotIn(event["hypothesis"], ("A", "B"))

    def test_the_incident_event_names_the_repositorys_own_database(self):
        incident = self._of("incident")[0]
        self.assertGreater(incident["database"]["tables"]["order_audit"], 0)
        self.assertEqual(incident["workload"]["id"], "order-audit-latency")

    # -- lifecycle events, in order -----------------------------------------

    def test_the_intent_is_emitted_before_anything_is_cloned(self):
        types = [e["type"] for e in self.events]
        self.assertEqual(types[0], "intent")
        self.assertLess(types.index("intent"), types.index("repository_cloning"))

    def test_repository_lifecycle_events_precede_the_incident(self):
        types = [e["type"] for e in self.events]
        self.assertIn("repository_validating", types)
        self.assertIn("repository_cloning", types)
        self.assertIn("repository_loaded", types)
        self.assertLess(types.index("repository_loaded"), types.index("incident"))

    def test_repository_loaded_names_the_owner_and_commit(self):
        loaded = self._of("repository_loaded")[0]
        self.assertEqual(loaded["owner"], "causeway-demo")
        self.assertEqual(loaded["name"], "causeway-demo")
        self.assertEqual(len(loaded["commit_sha"]), 40)
        self.assertEqual(loaded["service"], "order-service")
        self.assertEqual(loaded["runtime"], "python")

    # -- the hypotheses came from the source --------------------------------

    def test_at_least_two_hypotheses_are_reported_and_testable(self):
        found = self._of("hypotheses")[0]
        self.assertGreaterEqual(len(found["hypotheses"]), 2)
        self.assertGreaterEqual(len(found["testable"]), 2)
        for hypothesis in found["hypotheses"]:
            self.assertIn(hypothesis["file"], found["sources"])
            self.assertTrue(hypothesis["reason"])

    def test_every_experiment_intervention_is_a_source_edit(self):
        starts = self._of("phase_start")
        self.assertTrue(starts)
        for start in starts:
            self.assertEqual(start["intervention"]["kind"], "source_variant")

    def test_the_phase_that_reproduces_the_incident_applies_no_edit_at_all(self):
        """The incident state IS the repository as cloned. If reproducing it
        needed an edit, the thing being measured would not be the repository."""
        for start in self._of("phase_start"):
            if start["phase"] in ("reproduce", "restore"):
                self.assertEqual(start["intervention"]["edits"], [])
                self.assertTrue(start["intervention"]["unmodified"])

    # -- exactly one of the two suspects is the cause -----------------------

    def test_exactly_one_hypothesis_is_proven_and_one_is_refuted(self):
        verdicts = {e["hypothesis"]: e["verdict"] for e in self._of("verdict")}
        self.assertEqual(sorted(verdicts.values()), ["PROVEN", "REFUTED"])

    def test_the_proven_hypothesis_is_the_one_on_the_large_table(self):
        """Not asserted from any manifest - asserted from where the experiment
        landed. The decoy is the six-row lookup; the cause is the 40,000-row
        audit table, and only the measurement can tell them apart."""
        proven = [e["hypothesis"] for e in self._of("verdict")
                  if e["verdict"] == "PROVEN"]
        self.assertEqual(len(proven), 1)
        self.assertIn("lookup_order_audit", proven[0])

    def test_a_fix_is_proposed_and_verified_only_for_the_proven_hypothesis(self):
        proven = [e["hypothesis"] for e in self._of("verdict")
                  if e["verdict"] == "PROVEN"]
        self.assertEqual([e["hypothesis"] for e in self._of("root_cause_proven")],
                         proven)
        fix_verdicts = self._of("fix_verdict")
        self.assertEqual(len(fix_verdicts), 1)
        self.assertEqual(fix_verdicts[0]["hypothesis"], proven[0])
        self.assertEqual(fix_verdicts[0]["verdict"], "VERIFIED")

    def test_the_fix_is_shown_as_a_diff_against_a_patchable_file(self):
        applied = self._of("fix_apply")
        self.assertEqual(len(applied), 1)
        loaded = self._of("repository_loaded")[0]
        self.assertIn(applied[0]["file"], loaded["patchable"])
        self.assertIn("--- a/", applied[0]["diff"])
        self.assertIn("+++ b/", applied[0]["diff"])

    def test_the_run_ends_cleanly(self):
        self.assertEqual(self.events[-1]["type"], "done")


class IntentGovernsWhetherAnythingIsFixedTests(unittest.TestCase):
    """DIAGNOSE_ONLY is not a label on the report. It is a gate: the fix
    planner is never asked, and nothing is ever patched."""

    def _run_with(self, **kwargs):
        with local_repo(copy_demo=True) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                return _run(repository_url=REPO_URL, **kwargs)

    def test_diagnose_only_reaches_a_verdict_and_generates_no_fix(self):
        events = self._run_with(instruction="Find why it is slow. Do not modify anything.")
        types = [e["type"] for e in events]
        self.assertIn("verdict", types)
        self.assertIn("fix_skipped", types)
        for forbidden in ("fix_plan", "fix_apply", "fix_verdict", "fix_phase_result"):
            self.assertNotIn(forbidden, types)

    def test_an_absent_instruction_defaults_to_diagnosing_and_changing_nothing(self):
        events = self._run_with()
        intent_event = [e for e in events if e["type"] == "intent"][0]
        self.assertEqual(intent_event["mode"], "diagnose_only")
        self.assertEqual(intent_event["source"], "default")
        self.assertNotIn("fix_apply", [e["type"] for e in events])

    def test_a_scope_constraint_blocks_a_fix_to_a_file_outside_it(self):
        events = self._run_with(
            instruction="Fix the slow query but only modify app.py")
        types = [e["type"] for e in events]
        self.assertIn("root_cause_proven", types)
        self.assertIn("fix_blocked", types)
        self.assertNotIn("fix_apply", types)
        blocked = [e for e in events if e["type"] == "fix_blocked"][0]
        self.assertEqual(blocked["scope"], "intent")

    def test_an_ambiguous_instruction_asks_rather_than_guessing(self):
        events = _run(repository_url=REPO_URL, instruction="do something")
        types = [e["type"] for e in events]
        self.assertEqual(types, ["intent", "needs_clarification"])
        self.assertTrue(events[-1]["question"])


class RejectionNeverReachesTheSandboxTests(unittest.TestCase):
    INSTRUCTION = "Find why it is slow"

    def test_an_invalid_url_never_produces_an_incident_or_hypothesis_event(self):
        events = _run(repository_url="http://not-github.example/foo/bar",
                      instruction=self.INSTRUCTION)
        types = [e["type"] for e in events]
        self.assertEqual(types, ["intent", "repository_validating",
                                 "repository_rejected"])

    def test_an_unsupported_repository_is_rejected_before_the_sandbox(self):
        with local_repo({"README.md": "no manifest here"}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                events = _run(repository_url=REPO_URL, instruction=self.INSTRUCTION)
        types = [e["type"] for e in events]
        self.assertEqual(types, ["intent", "repository_validating",
                                 "repository_cloning", "repository_rejected"])
        rejection = events[-1]
        self.assertEqual(rejection["stage"], "manifest")

    def test_a_rejected_repository_workspace_is_still_cleaned_up(self):
        """Even a repository that fails the manifest check was still cloned
        onto disk first - that clone must not be left behind."""
        import tempfile
        before = {n for n in os.listdir(tempfile.gettempdir())
                  if n.startswith("causeway-repo-")}
        with local_repo({"README.md": "no manifest here"}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                _run(repository_url=REPO_URL, instruction=self.INSTRUCTION)
        after = {n for n in os.listdir(tempfile.gettempdir())
                 if n.startswith("causeway-repo-")}
        self.assertEqual(before, after)

    def test_no_source_variant_is_left_behind_after_a_full_run(self):
        import tempfile
        before = {n for n in os.listdir(tempfile.gettempdir())
                  if n.startswith("causeway-variant-")}
        with local_repo(copy_demo=True) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                _run(repository_url=REPO_URL, instruction=self.INSTRUCTION)
        after = {n for n in os.listdir(tempfile.gettempdir())
                 if n.startswith("causeway-variant-")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
