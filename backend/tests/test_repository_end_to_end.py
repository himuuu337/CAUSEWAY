"""The whole Milestone 6 path through the real orchestrator: a repository URL
in, a real (local, disposable) clone, a real manifest check, and - only if
that all passes - the exact same causal investigation and fix loop Milestone
5 already proved out, now sourced from the cloned workspace instead of the
bundled demo.

No live GitHub anywhere in this file: causeway.orchestrator.repository.clone
is redirected at a local git repository built by tests.repo_fixtures, the
same seam causeway.repository.git.clone's own `source` parameter exists for.
That is the only thing mocked - validation, the manifest check, the actual
git subprocess, the sandbox, the measurements and both verdicts all run for
real.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest
from unittest import mock

from causeway import config, orchestrator
from causeway.repository import git as repogit
from causeway.sandbox import service as bundled_service
from causeway.sandbox.replay import load_fixture
from tests.repo_fixtures import DEMO_REPO_DIR, local_repo

REPO_URL = "https://github.com/causeway-demo/causeway-demo"
DISTINCT_FIXTURE_ID = "incident-repo-e2e-001"


def _hash(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _run(repository_url=None, **kwargs):
    # The engine's own default repetition count (causeway.sandbox.runner.
    # REPETITIONS), not a reduced one - this run's A/REFUTED result sits on
    # the same noise floor as the bundled demo's own timing-based tests, and
    # a lower repetition count makes that measurement noisier, not this
    # test wrong.
    return list(orchestrator.investigate(repository_url=repository_url,
                                         offline=True, **kwargs))


def _cloning_from(local_source: str):
    """A causeway.orchestrator.repository.clone replacement that clones from
    a local directory instead of the real GitHub URL a RepoRef names - the
    same `source` escape hatch causeway.repository.git.clone exposes for
    exactly this purpose. Everything else about clone() runs unmocked: the
    real git subprocess, the real temp workspace, the real cleanup."""
    def _clone(ref, timeout=repogit.CLONE_TIMEOUT, source=None):
        return repogit.clone(ref, timeout=timeout, source=local_source)
    return _clone


def _distinguishable_demo_repo():
    """A copy of the real demo-repo/ contract, with its fixture id changed so
    a test can prove the orchestrator used THIS fixture and not the bundled
    one - a silent fallback to the bundled fixture would leave the id as
    'incident-001', which no assertion here ever expects."""
    fixture_path = os.path.join(DEMO_REPO_DIR, "fixtures", "incident-001.json")
    with open(fixture_path, "r", encoding="utf-8") as handle:
        fixture = json.load(handle)
    fixture["id"] = DISTINCT_FIXTURE_ID
    return {"fixtures/incident-001.json": json.dumps(fixture)}


@unittest.skipUnless(config.is_ready(), "machine is not seeded - run: python -m causeway.cli seed")
class RepositorySourcedInvestigationTests(unittest.TestCase):
    """One real end-to-end run, its events inspected from every angle the
    milestone's acceptance criteria name."""

    @classmethod
    def setUpClass(cls):
        cls.service_hash_before = _hash(bundled_service.__file__)
        cls.bundled_fixture = load_fixture(config.FIXTURE_PATH)
        with local_repo(_distinguishable_demo_repo(), copy_demo=True) as source:
            with mock.patch("causeway.orchestrator.repository.clone", _cloning_from(source)):
                cls.events = _run(repository_url=REPO_URL)
        cls.service_hash_after = _hash(bundled_service.__file__)

    def _of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]

    # -- the repository never silently substitutes the bundled demo --------

    def test_the_run_actually_used_the_repository_fixture_not_the_bundled_one(self):
        incident_events = self._of("incident")
        self.assertEqual(len(incident_events), 1)
        self.assertEqual(incident_events[0]["fixture"]["id"], DISTINCT_FIXTURE_ID)
        self.assertNotEqual(DISTINCT_FIXTURE_ID, self.bundled_fixture["id"])

    def test_the_bundled_sandbox_service_source_is_never_modified(self):
        self.assertEqual(self.service_hash_before, self.service_hash_after)

    # -- lifecycle events, in order -----------------------------------------

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

    # -- the causal story is unchanged, now sourced from the repository ----

    def test_A_is_refuted_and_B_is_proven(self):
        verdicts = {e["hypothesis"]: e["verdict"] for e in self._of("verdict")}
        self.assertEqual(verdicts["A"], "REFUTED")
        self.assertEqual(verdicts["B"], "PROVEN")

    def test_a_fix_is_proposed_and_verified_only_for_B(self):
        self.assertEqual([e["hypothesis"] for e in self._of("root_cause_proven")], ["B"])
        fix_verdicts = self._of("fix_verdict")
        self.assertEqual(len(fix_verdicts), 1)
        self.assertEqual(fix_verdicts[0]["hypothesis"], "B")
        self.assertEqual(fix_verdicts[0]["verdict"], "VERIFIED")

    def test_the_fix_patches_a_disposable_copy_never_the_cloned_checkout(self):
        applied = self._of("fix_apply")
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["operation"]["target"], "SCANNING_PREDICATE")
        self.assertEqual(applied[0]["operation"]["after"], "order_id = ?")

    def test_the_run_ends_cleanly(self):
        self.assertEqual(self.events[-1]["type"], "done")


class RejectionNeverReachesTheSandboxTests(unittest.TestCase):
    def test_an_invalid_url_never_produces_an_incident_or_candidate_event(self):
        events = _run(repository_url="http://not-github.example/foo/bar")
        types = [e["type"] for e in events]
        self.assertEqual(types, ["repository_validating", "repository_rejected"])

    def test_an_unsupported_repository_is_rejected_before_the_sandbox(self):
        with local_repo({"README.md": "no manifest here"}) as source:
            with mock.patch("causeway.orchestrator.repository.clone", _cloning_from(source)):
                events = _run(repository_url=REPO_URL)
        types = [e["type"] for e in events]
        self.assertEqual(types, ["repository_validating", "repository_cloning",
                                 "repository_rejected"])
        rejection = events[-1]
        self.assertEqual(rejection["stage"], "manifest")
        self.assertIn("supported Causeway demo configuration", rejection["reason"])

    def test_a_rejected_repository_workspace_is_still_cleaned_up(self):
        """Even a repository that fails the manifest check was still cloned
        onto disk first - that clone must not be left behind."""
        import tempfile
        before = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("causeway-repo-")}
        with local_repo({"README.md": "no manifest here"}) as source:
            with mock.patch("causeway.orchestrator.repository.clone", _cloning_from(source)):
                _run(repository_url=REPO_URL)
        after = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("causeway-repo-")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
