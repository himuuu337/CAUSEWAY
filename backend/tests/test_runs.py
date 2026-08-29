"""One investigation at a time, and a buffer a dropped client can catch up on.

None of this touches the sandbox: the run manager is driven with a stand-in
generator so the awkward cases - a second start, an engine error, an
unhandled exception - can be provoked in milliseconds instead of by breaking a
real experiment.
"""
from __future__ import annotations

import threading
import unittest

from causeway.runs import (AlreadyRunning, END, STATE_COMPLETED, STATE_FAILED,
                           STATE_RUNNING, RunManager)


def finishes():
    yield {"type": "stage", "stage": "localization", "status": "done"}
    yield {"type": "verdict", "hypothesis": "B", "verdict": "PROVEN"}
    yield {"type": "done", "elapsed_s": 0.01}


def reports_an_error():
    yield {"type": "stage", "stage": "incident_detected", "status": "done"}
    yield {"type": "error", "message": "this machine is not seeded yet"}


def explodes():
    yield {"type": "stage", "stage": "experiment", "status": "running"}
    raise RuntimeError("the sandbox service did not become healthy")


def blocks(gate):
    def source():
        yield {"type": "stage", "stage": "experiment", "status": "running"}
        gate.wait(5)
        yield {"type": "done", "elapsed_s": 0.0}
    return source


class LifecycleTests(unittest.TestCase):
    def test_a_completed_run_ends_with_a_terminal_event(self):
        manager = RunManager(source=finishes)
        run = manager.start()
        self.assertTrue(manager.join(5))
        types = [e["type"] for e in manager.events_from(run.id, 0)]
        self.assertEqual(types, ["stage", "verdict", "done", END])
        self.assertEqual(manager.status()["state"], STATE_COMPLETED)

    def test_the_terminal_event_carries_the_settled_state(self):
        """A client acting on `end` must not be acting on a stale state."""
        manager = RunManager(source=finishes)
        run = manager.start()
        manager.join(5)
        end = manager.events_from(run.id, 0)[-1]
        self.assertEqual(end["state"], STATE_COMPLETED)
        self.assertEqual(end["run_id"], run.id)
        self.assertEqual(end["event_count"], 3)

    def test_an_engine_error_marks_the_run_failed_without_losing_events(self):
        manager = RunManager(source=reports_an_error)
        run = manager.start()
        manager.join(5)
        self.assertEqual(manager.status()["state"], STATE_FAILED)
        self.assertIn("not seeded", manager.status()["error"])
        self.assertEqual([e["type"] for e in manager.events_from(run.id, 0)],
                         ["stage", "error", END])

    def test_an_unhandled_exception_becomes_an_error_event_not_a_hang(self):
        """The browser is waiting on a stream. A crash that produced silence
        would leave it waiting forever."""
        manager = RunManager(source=explodes)
        run = manager.start()
        self.assertTrue(manager.join(5))
        events = manager.events_from(run.id, 0)
        self.assertEqual([e["type"] for e in events], ["stage", "error", END])
        self.assertIn("did not become healthy", events[1]["message"])
        self.assertEqual(manager.status()["state"], STATE_FAILED)

    def test_a_run_is_only_closed_once_the_terminal_event_is_appended(self):
        manager = RunManager(source=finishes)
        run = manager.start()
        manager.join(5)
        self.assertTrue(manager.is_closed(run.id))
        self.assertFalse(manager.is_closed("some-other-run"))


class ConcurrencyTests(unittest.TestCase):
    """The sandbox is a real process measuring real latency. Two investigations
    sharing a machine would measure each other."""

    def test_a_second_start_is_refused_and_names_the_run_in_progress(self):
        gate = threading.Event()
        manager = RunManager(source=blocks(gate))
        first = manager.start()
        try:
            with self.assertRaises(AlreadyRunning) as caught:
                manager.start()
            self.assertEqual(caught.exception.run_id, first.id)
            self.assertTrue(manager.is_running())
        finally:
            gate.set()
            manager.join(5)

    def test_a_new_run_is_allowed_once_the_previous_one_finished(self):
        manager = RunManager(source=finishes)
        first = manager.start()
        manager.join(5)
        second = manager.start()
        manager.join(5)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(manager.status()["state"], STATE_COMPLETED)

    def test_status_before_anything_has_run(self):
        self.assertEqual(RunManager(source=finishes).status()["state"], "idle")

    def test_a_run_in_progress_reports_running(self):
        gate = threading.Event()
        manager = RunManager(source=blocks(gate))
        manager.start()
        try:
            self.assertEqual(manager.status()["state"], STATE_RUNNING)
        finally:
            gate.set()
            manager.join(5)


class BufferTests(unittest.TestCase):
    def test_events_can_be_read_from_any_point(self):
        manager = RunManager(source=finishes)
        run = manager.start()
        manager.join(5)
        self.assertEqual(len(manager.events_from(run.id, 0)), 4)
        self.assertEqual(len(manager.events_from(run.id, 2)), 2)
        self.assertEqual(manager.events_from(run.id, 99), [])

    def test_a_negative_index_reads_from_the_start_rather_than_the_end(self):
        """Slicing with a negative index would silently hand back the tail."""
        manager = RunManager(source=finishes)
        run = manager.start()
        manager.join(5)
        self.assertEqual(len(manager.events_from(run.id, -5)), 4)

    def test_an_unknown_run_yields_nothing(self):
        manager = RunManager(source=finishes)
        manager.start()
        manager.join(5)
        self.assertEqual(manager.events_from("not-a-run", 0), [])
        self.assertIsNone(manager.get("not-a-run"))

    def test_a_reader_never_sees_a_half_written_buffer(self):
        """events_from takes its snapshot under the same lock the writer uses."""
        manager = RunManager(source=finishes)
        run = manager.start()
        seen = []
        while not manager.is_closed(run.id):
            seen.append(len(manager.events_from(run.id, 0)))
        manager.join(5)
        self.assertEqual(sorted(seen), seen, "buffer length went backwards")


if __name__ == "__main__":
    unittest.main()
