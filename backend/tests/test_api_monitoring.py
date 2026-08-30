"""The telemetry/prediction/service HTTP surface in causeway/api.py.

Mirrors tests/test_api.py: handlers are called directly, no socket. Every
test resets the process-wide singletons api.py wires together
(causeway.telemetry.store.store, causeway.prediction.engine.engine,
causeway.incidents.manager, causeway.services.registry) so one test's
telemetry can never leak into another's.
"""
from __future__ import annotations

import json
import unittest

try:
    from causeway import api
    from fastapi import HTTPException
except ImportError:                                   # pragma: no cover
    api = None

from causeway import monitor
from causeway.incidents import manager as incident_manager
from causeway.prediction.engine import engine as prediction_engine
from causeway.services import registry as service_registry
from causeway.telemetry.store import store as telemetry_store

SKIP = "fastapi is not installed - run: pip install -r requirements.txt"


def body_of(response) -> dict:
    return json.loads(response.body)


@unittest.skipIf(api is None, SKIP)
class MonitoringRoutesTests(unittest.TestCase):
    def setUp(self):
        telemetry_store.reset()
        prediction_engine.reset()
        incident_manager.reset()
        service_registry.reset()
        monitor.manager.reset()

    tearDown = setUp

    def test_the_monitoring_endpoints_are_registered(self):
        paths = {route.path for route in api.app.routes}
        for path in ("/api/telemetry", "/api/prediction/status",
                     "/api/services/register", "/api/services", "/api/monitor/stream"):
            self.assertIn(path, paths)

    def test_a_well_formed_sample_is_accepted_and_evaluated(self):
        response = api.post_telemetry({"service": "s", "cpu_percent": 20.0})
        self.assertEqual(response.status_code, 200)
        body = body_of(response)
        self.assertEqual(body["service"], "s")
        self.assertEqual(telemetry_store.sample_count("s"), 1)

    def test_a_malformed_sample_is_rejected_with_400(self):
        with self.assertRaises(HTTPException) as caught:
            api.post_telemetry({"service": "s", "cpu_percent": "not a number"})
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(telemetry_store.sample_count("s"), 0)

    def test_prediction_status_for_one_service(self):
        for i in range(6):
            api.post_telemetry({"service": "s", "db_pool_used": 10 + i,
                               "db_pool_capacity": 100})
        status = api.prediction_status("s")
        self.assertEqual(status["service"], "s")
        self.assertIsInstance(status["assessments"], list)

    def test_prediction_status_without_a_service_lists_every_known_service(self):
        api.post_telemetry({"service": "a", "cpu_percent": 1.0})
        api.post_telemetry({"service": "b", "cpu_percent": 1.0})
        status = api.prediction_status(None)
        names = {entry["service"] for entry in status["services"]}
        self.assertEqual(names, {"a", "b"})

    def test_registering_a_service_returns_the_target(self):
        response = api.register_service({"service": "s", "repository_url":
                                         "https://github.com/o/n"})
        self.assertEqual(response.status_code, 200)
        body = body_of(response)
        self.assertEqual(body["service"], "s")
        self.assertEqual(body["repository_url"], "https://github.com/o/n")

    def test_registering_without_a_repository_url_is_400(self):
        with self.assertRaises(HTTPException) as caught:
            api.register_service({"service": "s"})
        self.assertEqual(caught.exception.status_code, 400)

    def test_registering_an_invalid_github_url_is_400(self):
        with self.assertRaises(HTTPException) as caught:
            api.register_service({"service": "s", "repository_url":
                                  "http://not-github.example/o/n"})
        self.assertEqual(caught.exception.status_code, 400)

    def test_list_services_reflects_registrations(self):
        api.register_service({"service": "s", "repository_url": "https://github.com/o/n"})
        body = api.list_services()
        self.assertEqual(len(body["services"]), 1)
        self.assertEqual(body["services"][0]["service"], "s")

    def test_telemetry_ingestion_publishes_to_the_monitor_feed(self):
        api.post_telemetry({"service": "s", "cpu_percent": 20.0})
        events = monitor.manager.events_from(0)
        self.assertTrue(any(e["type"] == "telemetry_received" for e in events))

    def test_a_confirmed_incident_reaches_the_monitor_feed_end_to_end(self):
        """The full HTTP pipeline: repeated POSTs describing a sustained
        connection-pool incident eventually produce incident_created and
        investigation_handoff events on the monitor feed - no service
        registered, so the handoff waits for repository context rather
        than cloning anything."""
        pool = [55, 64, 73, 82, 90, 96, 97, 98, 99]
        waiting = [0, 3, 8, 15, 27, 43, 50, 55, 60]
        p95 = [100, 130, 180, 270, 450, 760, 800, 820, 830]
        for i in range(9):
            api.post_telemetry({
                "service": "order-service", "db_pool_used": pool[i],
                "db_pool_capacity": 100, "db_waiting_requests": waiting[i],
                "p95_ms": p95[i],
            })
        events = monitor.manager.events_from(0)
        types = [e["type"] for e in events]
        self.assertIn("incident_created", types)
        self.assertIn("investigation_handoff", types)
        handoff = next(e for e in events if e["type"] == "investigation_handoff")
        self.assertEqual(handoff["status"], "AWAITING_REPOSITORY_CONTEXT")


if __name__ == "__main__":
    unittest.main()
