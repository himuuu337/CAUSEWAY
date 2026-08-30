"""causeway.repository_monitor: real live telemetry for a user-provided
causeway.json repository. No live GitHub anywhere in this file -
causeway.repository.clone is redirected at a local git repository (the
real bundled demo-repo/ for the happy path, tests.repo_fixtures.local_repo's
own fixtures for the refusal path), the same seam
tests/test_repository_end_to_end.py already uses. Only the outbound
POST /api/telemetry call is mocked; the clone, the sandbox launch, and the
workload replay are all real.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from causeway import repository_monitor
from causeway.repository import git as repogit
from tests.repo_fixtures import local_repo

REPO_URL = "https://github.com/o/n"


def _cloning_from(local_source: str):
    def _clone(ref, timeout=repogit.CLONE_TIMEOUT, source=None):
        return repogit.clone(ref, timeout=timeout, source=local_source)
    return _clone


class AggregateSampleTests(unittest.TestCase):
    def test_percentiles_and_rates_are_computed_from_real_samples(self):
        samples = [(10.0, True), (20.0, True), (30.0, True), (40.0, True), (1000.0, False)]
        sample = repository_monitor.aggregate_sample("svc", samples, window_seconds=5.0)
        self.assertEqual(sample["service"], "svc")
        self.assertEqual(sample["request_rate"], 1.0)      # 5 requests / 5s
        self.assertEqual(sample["error_rate"], 0.2)         # 1 of 5 failed
        self.assertIn("p50_ms", sample)
        self.assertIn("p95_ms", sample)
        self.assertIn("p99_ms", sample)

    def test_an_empty_window_is_still_a_well_formed_sample(self):
        sample = repository_monitor.aggregate_sample("svc", [], window_seconds=5.0)
        self.assertEqual(sample["request_rate"], 0.0)
        self.assertEqual(sample["error_rate"], 0.0)
        self.assertNotIn("p50_ms", sample)

    def test_a_zero_window_never_divides_by_zero(self):
        sample = repository_monitor.aggregate_sample("svc", [(10.0, True)], window_seconds=0.0)
        self.assertEqual(sample["request_rate"], 0.0)

    def test_pool_fields_are_included_only_when_health_exposes_them(self):
        sample = repository_monitor.aggregate_sample(
            "svc", [(10.0, True)], window_seconds=1.0,
            health_body={"pool": {"used": 9, "capacity": 12, "waiting": 3}})
        self.assertEqual(sample["db_pool_used"], 9.0)
        self.assertEqual(sample["db_pool_capacity"], 12.0)
        self.assertEqual(sample["db_waiting_requests"], 3.0)

    def test_no_pool_fields_are_fabricated_when_health_has_none(self):
        sample = repository_monitor.aggregate_sample(
            "svc", [(10.0, True)], window_seconds=1.0, health_body={"status": "ok"})
        self.assertNotIn("db_pool_used", sample)
        self.assertNotIn("db_pool_capacity", sample)
        self.assertNotIn("db_waiting_requests", sample)

    def test_no_pool_fields_when_health_body_is_none(self):
        sample = repository_monitor.aggregate_sample("svc", [(10.0, True)], window_seconds=1.0,
                                                      health_body=None)
        self.assertNotIn("db_pool_used", sample)


class RealDemoRepositoryEndToEndTests(unittest.TestCase):
    """The bundled demo-repo/ is a real, working causeway.json repository -
    real entrypoint, real workload, real schema. Nothing about the clone,
    sandbox launch, or workload replay is mocked; only the outbound
    POST /api/telemetry call is, so this proves the whole pipeline for real
    without a network dependency."""

    @classmethod
    def _hash(cls, path: str) -> str:
        import hashlib
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    @classmethod
    def setUpClass(cls):
        cls.events = []
        cls.samples = []
        with local_repo(copy_demo=True) as source:
            db_py_before = cls._hash(os.path.join(source, "db.py"))
            with mock.patch("causeway.repository.clone", _cloning_from(source)), \
                 mock.patch("causeway.repository_monitor.post_telemetry",
                            return_value=True) as posted:
                repository_monitor.run(
                    repository_url=REPO_URL, service_name="test-service",
                    duration_seconds=0.1, interval_seconds=0.0,
                    on_event=cls.events.append, on_sample=lambda s, ok: cls.samples.append(s))
            cls.posted_mock = posted
            cls.db_py_before = db_py_before
            cls.db_py_after = cls._hash(os.path.join(source, "db.py"))

    def test_the_repository_lifecycle_events_arrive_in_order(self):
        types = [e["type"] for e in self.events]
        self.assertEqual(types, ["repository_validating", "repository_cloning",
                                 "repository_loaded"])

    def test_at_least_one_real_sample_was_measured_and_posted(self):
        self.assertGreaterEqual(len(self.samples), 1)
        sample = self.samples[0]
        self.assertEqual(sample["service"], "test-service")
        self.assertGreater(sample["p95_ms"], 0)
        self.assertGreater(sample["request_rate"], 0)

    def test_post_telemetry_was_actually_called_with_the_measured_sample(self):
        self.posted_mock.assert_called()
        posted_sample = self.posted_mock.call_args.args[1]
        self.assertEqual(posted_sample["service"], "test-service")

    def test_the_original_clone_was_never_modified(self):
        """materialise() copies the workspace before launching - the
        original clone this test redirected clone() to must be byte-for-byte
        unchanged after the run."""
        self.assertEqual(self.db_py_before, self.db_py_after)


class ManifestlessRepositoryRefusalTests(unittest.TestCase):
    """No causeway.json means no declared workload and no reliable way to
    start the repository - refused plainly, never guessed at."""

    def test_a_manifest_less_repository_is_refused_with_a_clear_reason(self):
        with local_repo({"app.py": "print(1)\n"}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                with self.assertRaises(RuntimeError) as caught:
                    repository_monitor.run(repository_url=REPO_URL, service_name="svc",
                                           duration_seconds=0.1)
        self.assertIn("causeway.json", str(caught.exception))

    def test_nothing_is_attempted_before_the_refusal(self):
        """No sandbox, no replay, no telemetry post - the refusal happens
        before anything is launched."""
        with local_repo({"app.py": "print(1)\n"}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)), \
                 mock.patch("causeway.repository_monitor.post_telemetry") as posted:
                with self.assertRaises(RuntimeError):
                    repository_monitor.run(repository_url=REPO_URL, service_name="svc",
                                           duration_seconds=0.1)
        posted.assert_not_called()


class StatePersistsAcrossSamplesTests(unittest.TestCase):
    """The one behavioural difference from the causal-experiment path this
    whole design hinges on: no per-sample restore-and-remeasure. Sandbox.
    measure() is the only method that resets the database between
    repetitions - proving it is never called (while Sandbox.start() still
    runs for real, including its own legitimate one-time initial restore)
    is what confirms this loop never falls into the controlled-experiment
    pattern. State accumulates naturally across samples, because sustained
    drift is the signal prediction looks for."""

    def test_sandbox_measure_is_never_used_during_a_monitoring_session(self):
        with local_repo(copy_demo=True) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)), \
                 mock.patch("causeway.repository_monitor.post_telemetry", return_value=True), \
                 mock.patch.object(repository_monitor.Sandbox, "measure") as measure:
                repository_monitor.run(repository_url=REPO_URL, service_name="svc",
                                       duration_seconds=0.3, interval_seconds=0.0)
        measure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
