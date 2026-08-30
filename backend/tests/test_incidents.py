"""causeway.incidents: edge-triggered incident creation from confirmed risk
assessments, and the handoff to a repository investigation - never a real
investigation here (that is causeway.production's / the full orchestrator's
job), just a fake `run_starter` that records what it was asked to start.
"""
from __future__ import annotations

import unittest

from causeway.incidents import (AWAITING_REPOSITORY_CONTEXT, INVESTIGATION_ALREADY_RUNNING,
                                INVESTIGATION_STARTED, IncidentManager)
from causeway.prediction.schema import RiskAssessment
from causeway.services import ServiceRegistry


def _assessment(service="s", detector="connection_pool_exhaustion", level="HIGH",
                confirmed=False, score=0.9):
    return RiskAssessment(
        service=service, detector=detector, level=level, score=score,
        predicted_failure="connection pool exhaustion",
        evidence=("db pool utilization at 96% and rising",),
        current_values={"db_pool_utilization_percent": 96.0}, trends={},
        eta_seconds=2.0, sample_count=6, confirmed=confirmed)


class FakeRun:
    def __init__(self, run_id="run-1"):
        self.id = run_id


class EdgeTriggeredCreationTests(unittest.TestCase):
    def test_a_non_confirmed_assessment_creates_nothing(self):
        manager = IncidentManager(run_starter=lambda **kw: FakeRun(),
                                  service_registry=ServiceRegistry())
        created = manager.observe([_assessment(confirmed=False)])
        self.assertEqual(created, [])
        self.assertEqual(manager.all(), [])

    def test_a_confirmed_assessment_creates_exactly_one_incident(self):
        manager = IncidentManager(run_starter=lambda **kw: FakeRun(),
                                  service_registry=ServiceRegistry())
        created = manager.observe([_assessment(confirmed=True)])
        self.assertEqual(len(created), 1)
        self.assertEqual(len(manager.all()), 1)

    def test_repeated_confirmed_evaluations_create_no_duplicate(self):
        manager = IncidentManager(run_starter=lambda **kw: FakeRun(),
                                  service_registry=ServiceRegistry())
        manager.observe([_assessment(confirmed=True)])
        second = manager.observe([_assessment(confirmed=True)])
        third = manager.observe([_assessment(confirmed=True)])
        self.assertEqual(second, [])
        self.assertEqual(third, [])
        self.assertEqual(len(manager.all()), 1)

    def test_recovery_then_a_new_confirmation_creates_a_second_incident(self):
        manager = IncidentManager(run_starter=lambda **kw: FakeRun(),
                                  service_registry=ServiceRegistry())
        manager.observe([_assessment(confirmed=True)])
        manager.observe([_assessment(confirmed=False)])   # recovered
        created = manager.observe([_assessment(confirmed=True)])   # new episode
        self.assertEqual(len(created), 1)
        self.assertEqual(len(manager.all()), 2)

    def test_the_incident_carries_only_real_evidence_from_the_assessment(self):
        manager = IncidentManager(run_starter=lambda **kw: FakeRun(),
                                  service_registry=ServiceRegistry())
        assessment = _assessment(confirmed=True)
        created = manager.observe([assessment])[0]
        self.assertEqual(created.service, assessment.service)
        self.assertEqual(created.predicted_failure, assessment.predicted_failure)
        self.assertEqual(created.evidence, assessment.evidence)
        self.assertEqual(created.eta_seconds, assessment.eta_seconds)


class HandoffTests(unittest.TestCase):
    def test_an_unregistered_service_waits_for_repository_context(self):
        calls = []
        manager = IncidentManager(run_starter=lambda **kw: calls.append(kw) or FakeRun(),
                                  service_registry=ServiceRegistry())
        created = manager.observe([_assessment(confirmed=True)])[0]
        self.assertEqual(created.status, AWAITING_REPOSITORY_CONTEXT)
        self.assertIsNone(created.run_id)
        self.assertEqual(calls, [])

    def test_a_registered_target_triggers_a_real_looking_handoff(self):
        calls = []
        registry = ServiceRegistry()
        registry.register("s", "https://github.com/o/n")
        manager = IncidentManager(
            run_starter=lambda **kw: calls.append(kw) or FakeRun("run-42"),
            service_registry=registry)
        created = manager.observe([_assessment(confirmed=True)])[0]
        self.assertEqual(created.status, INVESTIGATION_STARTED)
        self.assertEqual(created.run_id, "run-42")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["repository_url"], "https://github.com/o/n")
        self.assertIn("connection pool exhaustion", calls[0]["instruction"])
        self.assertIn("db pool utilization at 96%", calls[0]["instruction"])

    def test_a_busy_investigation_does_not_start_a_duplicate(self):
        class AlreadyRunningStub(RuntimeError):
            pass

        def busy_starter(**kw):
            raise busy_starter.exc

        import causeway.runs as runs_module
        busy_starter.exc = runs_module.AlreadyRunning("run-1")

        registry = ServiceRegistry()
        registry.register("s", "https://github.com/o/n")
        manager = IncidentManager(run_starter=busy_starter, service_registry=registry)
        created = manager.observe([_assessment(confirmed=True)])[0]
        self.assertEqual(created.status, INVESTIGATION_ALREADY_RUNNING)
        # still recorded as ONE incident - a busy investigation does not
        # mean the incident itself is silently dropped
        self.assertEqual(len(manager.all()), 1)

    def test_no_investigation_is_ever_started_for_a_service_with_no_target(self):
        """The non-negotiable rule: Causeway never clones a repository
        nobody registered."""
        started = []
        manager = IncidentManager(run_starter=lambda **kw: started.append(kw) or FakeRun(),
                                  service_registry=ServiceRegistry())
        for _ in range(3):
            manager.observe([_assessment(confirmed=True)])
            manager.observe([_assessment(confirmed=False)])
        self.assertEqual(started, [])


if __name__ == "__main__":
    unittest.main()
