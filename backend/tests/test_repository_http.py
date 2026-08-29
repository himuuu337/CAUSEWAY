"""The full HTTP stack for the repository path: a real POST to
/api/investigation with a repository_url and an instruction, the real
SSE-buffered event stream, the real RunManager thread, and the real
orchestrator - the only thing not real is where the clone points, redirected
at a local git repository exactly as tests.test_repository_end_to_end does.
If this passes, a browser hitting this same API would see the same thing.
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

try:
    from fastapi.testclient import TestClient

    from causeway import api
except ImportError:                                   # pragma: no cover
    api = None

from causeway.repository import git as repogit
from causeway.runs import RunManager
from tests.repo_fixtures import local_repo

SKIP = "fastapi is not installed - run: pip install -r requirements.txt"
REPO_URL = "https://github.com/causeway-demo/causeway-demo"


def _cloning_from(local_source: str):
    def _clone(ref, timeout=repogit.CLONE_TIMEOUT, source=None):
        return repogit.clone(ref, timeout=timeout, source=local_source)
    return _clone


@unittest.skipIf(api is None, SKIP)
class RepositoryOverHTTPTests(unittest.TestCase):
    def setUp(self):
        self.original_manager = api.manager
        api.manager = RunManager()
        self.client = TestClient(api.app)

    def tearDown(self):
        api.manager.join(timeout=120)
        api.manager = self.original_manager

    def _drain(self, run_id, timeout=180):
        deadline = time.time() + timeout
        payload = {"state": "running", "events": []}
        while time.time() < deadline:
            payload = self.client.get(
                "/api/investigation/%s/events" % run_id).json()
            if payload["state"] != "running":
                break
            time.sleep(0.2)
        return payload

    def test_a_repository_investigation_runs_end_to_end_over_real_http(self):
        with local_repo(copy_demo=True) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                response = self.client.post(
                    "/api/investigation",
                    json={"repository_url": REPO_URL,
                          "instruction": "Find why it is slow and fix it"})
                self.assertEqual(response.status_code, 202)
                events = self._drain(response.json()["run_id"])["events"]

        types = [e["type"] for e in events]
        self.assertIn("intent", types)
        self.assertIn("repository_loaded", types)
        self.assertIn("hypotheses", types)
        self.assertIn("end", types)
        # the A/B fixture never appears on this path
        self.assertNotIn("candidates", types)
        self.assertNotIn("observational", types)

        verdicts = {e["hypothesis"]: e["verdict"]
                    for e in events if e["type"] == "verdict"}
        self.assertEqual(sorted(verdicts.values()), ["PROVEN", "REFUTED"])

        fix_verdicts = [e for e in events if e["type"] == "fix_verdict"]
        self.assertEqual(len(fix_verdicts), 1)
        self.assertEqual(fix_verdicts[0]["verdict"], "VERIFIED")

        final = events[-1]
        self.assertEqual(final["type"], "end")
        self.assertEqual(final["state"], "completed")

    def test_a_diagnose_only_instruction_is_honoured_over_http(self):
        with local_repo(copy_demo=True) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                response = self.client.post(
                    "/api/investigation",
                    json={"repository_url": REPO_URL,
                          "instruction": "Find why it is slow",
                          "mode": "diagnose_only"})
                events = self._drain(response.json()["run_id"])["events"]

        types = [e["type"] for e in events]
        self.assertIn("verdict", types)
        self.assertIn("fix_skipped", types)
        self.assertNotIn("fix_apply", types)

    def test_an_unsupported_repository_ends_the_run_as_failed_over_http(self):
        with local_repo({"README.md": "no manifest"}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                response = self.client.post(
                    "/api/investigation",
                    json={"repository_url": REPO_URL, "instruction": "why slow"})
                payload = self._drain(response.json()["run_id"], timeout=60)

        self.assertEqual(payload["state"], "failed")
        types = [e["type"] for e in payload["events"]]
        self.assertNotIn("incident", types)
        self.assertNotIn("hypotheses", types)
        self.assertIn("repository_rejected", types)

    def test_a_malformed_repository_url_type_is_a_400_over_http(self):
        response = self.client.post("/api/investigation", json={"repository_url": 5})
        self.assertEqual(response.status_code, 400)

    def test_a_malformed_instruction_type_is_a_400_over_http(self):
        response = self.client.post(
            "/api/investigation", json={"repository_url": REPO_URL, "instruction": 5})
        self.assertEqual(response.status_code, 400)

    def test_an_unknown_mode_is_a_400_rather_than_a_reinterpretation(self):
        response = self.client.post(
            "/api/investigation",
            json={"repository_url": REPO_URL, "mode": "do_whatever_you_like"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
