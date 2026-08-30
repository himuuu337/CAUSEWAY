"""The HTTP layer's own behaviour.

Skipped when FastAPI is not installed, so a checkout without dependencies can
still run the engine's tests. The handlers are called directly rather than
through a client: everything asserted here is a decision api.py makes about
what to put in a response, and none of it needs a socket.
"""
from __future__ import annotations

import json
import threading
import unittest

try:
    from causeway import api
    from fastapi import HTTPException
except ImportError:                                   # pragma: no cover
    api = None

from causeway import verdict
from causeway.runs import AlreadyRunning, RunManager

SKIP = "fastapi is not installed - run: pip install -r requirements.txt"


def body_of(response) -> dict:
    return json.loads(response.body)


class BlockingRun:
    """A run source that stays in STATE_RUNNING until explicitly released.

    The fixture it replaces (`never_finishes`) was a generator that yielded
    once and returned - the background thread could complete the run before
    the test's second request landed, racing the very thing under test. This
    blocks the generator on a threading.Event instead, so the run is
    deterministically still running for as long as the test needs it to be,
    on any machine or scheduler. `release()` must be called - in a `finally`
    or `tearDown` - or the background thread blocks forever.
    """

    def __init__(self):
        self._release = threading.Event()

    def release(self) -> None:
        self._release.set()

    def __call__(self):
        yield {"type": "stage", "stage": "experiment", "status": "running"}
        self._release.wait()


@unittest.skipIf(api is None, SKIP)
class RouteTests(unittest.TestCase):
    def test_the_documented_endpoints_are_registered(self):
        paths = {route.path for route in api.app.routes}
        for path in ("/api/health", "/api/status", "/api/investigation",
                     "/api/investigation/stream",
                     "/api/investigation/{run_id}/events"):
            self.assertIn(path, paths)

    def test_starting_an_investigation_is_a_post(self):
        route = next(r for r in api.app.routes if r.path == "/api/investigation")
        self.assertEqual(set(route.methods) & {"GET", "POST"}, {"POST"})


@unittest.skipIf(api is None, SKIP)
class HealthTests(unittest.TestCase):
    def test_health_reports_the_engine_the_interface_is_talking_to(self):
        payload = api.health()
        self.assertEqual(payload["engine"]["phases"], list(verdict.PHASES))
        self.assertEqual(payload["engine"]["failure_factor"], verdict.FAILURE_FACTOR)
        self.assertEqual(payload["engine"]["recovery_factor"], verdict.RECOVERY_FACTOR)
        self.assertIn(verdict.PROVEN, payload["engine"]["verdicts"])

    def test_an_unseeded_machine_says_so_and_says_what_to_do(self):
        payload = api.health()
        if payload["seeded"]:
            self.assertIsNone(payload["hint"])
        else:
            self.assertEqual(payload["status"], "not-seeded")
            self.assertIn("seed", payload["hint"])


@unittest.skipIf(api is None, SKIP)
class ConflictTests(unittest.TestCase):
    """A second investigation while one is in flight. The client attaches to
    the run in progress, so the 409 has to carry which run that is."""

    def setUp(self):
        self.original = api.manager
        self.blocking = BlockingRun()
        api.manager = RunManager(source=self.blocking)
        self.run = api.manager.start()

    def tearDown(self):
        # Unblock the worker thread and wait for it before restoring the
        # manager, so a failed assertion above can never leave a thread
        # running against a RunManager the rest of the suite no longer sees.
        self.blocking.release()
        api.manager.join(timeout=5)
        api.manager = self.original

    def test_a_second_start_is_a_409(self):
        response = api.start_investigation()
        self.assertEqual(response.status_code, 409)

    def test_the_conflict_names_the_run_already_in_progress(self):
        self.assertEqual(body_of(api.start_investigation())["run_id"], self.run.id)

    def test_the_conflict_explains_itself(self):
        """Regression: the run summary used to be spread over these keys, and
        status()'s empty `error` field overwrote the reason for the conflict."""
        payload = body_of(api.start_investigation())
        self.assertEqual(payload["reason"], "already-running")
        self.assertIn("already running", payload["message"])

    def test_the_conflict_still_carries_the_run_status(self):
        payload = body_of(api.start_investigation())
        self.assertEqual(payload["state"], "running")
        self.assertIn("event_count", payload)


@unittest.skipIf(api is None, SKIP)
class GraphEndpointTests(unittest.TestCase):
    """GET /api/investigation/{run_id}/graph - wiring only. The graph's own
    construction rules are covered exhaustively by tests/test_graph.py; this
    just proves the route exists, 404s correctly, and hands build_graph the
    right run's own events."""

    def setUp(self):
        self.original = api.manager
        self.blocking = BlockingRun()
        api.manager = RunManager(source=self.blocking)
        self.run = api.manager.start()

    def tearDown(self):
        self.blocking.release()
        api.manager.join(timeout=5)
        api.manager = self.original

    def test_the_route_is_registered(self):
        paths = {route.path for route in api.app.routes}
        self.assertIn("/api/investigation/{run_id}/graph", paths)

    def test_an_unknown_run_id_is_a_404(self):
        with self.assertRaises(HTTPException) as ctx:
            api.investigation_graph("no-such-run")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_a_known_run_returns_a_graph_shaped_response(self):
        payload = api.investigation_graph(self.run.id)
        self.assertIn("nodes", payload)
        self.assertIn("edges", payload)


@unittest.skipIf(api is None, SKIP)
class StreamingHeaderTests(unittest.TestCase):
    def test_buffering_is_disabled_on_the_stream(self):
        """A proxy that buffers text/event-stream turns a live investigation
        into one lump at the end."""
        self.assertEqual(api.SSE_HEADERS["X-Accel-Buffering"], "no")
        self.assertIn("no-transform", api.SSE_HEADERS["Cache-Control"])


@unittest.skipIf(api is None, SKIP)
class RepositoryRequestBodyTests(unittest.TestCase):
    """start_investigation's optional {"repository_url": "..."} body. Called
    directly (as every other test in this file does), FastAPI's own body
    parsing never runs, so `payload` arrives exactly as a direct caller would
    pass it - which is also what proves a call with no body at all (today's
    frontend, and every other test here) is completely unaffected."""

    def setUp(self):
        self.original = api.manager
        api.manager = RunManager(source=lambda repository_url=None: iter(
            [{"type": "done", "elapsed_s": 0.0}]))

    def tearDown(self):
        api.manager.join(timeout=5)
        api.manager = self.original

    def test_no_payload_starts_the_bundled_demo(self):
        response = api.start_investigation(payload=None)
        self.assertEqual(response.status_code, 202)
        api.manager.join(5)
        self.assertEqual(api.manager.run.repository_url, "")

    def test_an_empty_payload_starts_the_bundled_demo(self):
        response = api.start_investigation(payload={})
        self.assertEqual(response.status_code, 202)
        api.manager.join(5)
        self.assertEqual(api.manager.run.repository_url, "")

    def test_a_repository_url_is_threaded_through_to_the_run(self):
        response = api.start_investigation(payload={"repository_url": "https://github.com/foo/bar"})
        self.assertEqual(response.status_code, 202)
        api.manager.join(5)
        self.assertEqual(api.manager.run.repository_url, "https://github.com/foo/bar")

    def test_a_non_string_repository_url_is_a_400(self):
        with self.assertRaises(HTTPException) as caught:
            api.start_investigation(payload={"repository_url": 12345})
        self.assertEqual(caught.exception.status_code, 400)

    def test_a_non_dict_payload_is_treated_as_empty(self):
        """Only reachable directly, never through real FastAPI routing (which
        would already have rejected non-object JSON) - still must not crash."""
        response = api.start_investigation(payload="not-a-dict")
        self.assertEqual(response.status_code, 202)


@unittest.skipIf(api is None, SKIP)
class BoundaryTests(unittest.TestCase):
    def test_the_http_layer_cannot_reach_a_verdict_of_its_own(self):
        """api.py may read verdict constants for /api/health. It must not be
        able to decide anything - the import graph test covers the other
        direction, and this covers the intent."""
        import inspect
        source = inspect.getsource(api)
        for forbidden in ("verdict.decide", "verdict.explain", "verdict.reason",
                          "failure_present", "recovered("):
            self.assertNotIn(forbidden, source,
                             "the API must not compute results: %r" % forbidden)


if __name__ == "__main__":
    unittest.main()
