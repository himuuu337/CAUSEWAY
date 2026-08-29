"""The SSE contract, tested without a web server.

The behaviour that is expensive to debug through a browser - resuming after a
drop, closing on a finished run instead of hanging, not reconnecting forever -
is all in causeway/stream.py, and all of it is exercised here directly.
"""
from __future__ import annotations

import asyncio
import json
import unittest

from causeway import stream


class FakeSource:
    """Stands in for the run manager, with the same three methods the stream
    actually uses."""

    def __init__(self, events=None, run_id="run-1", closed=False, exists=True):
        self.events = list(events or [])
        self.run_id = run_id
        self.closed = closed
        self.exists = exists

    def events_from(self, run_id, index):
        if run_id != self.run_id:
            return []
        return list(self.events[max(0, index):])

    def get(self, run_id):
        return object() if (self.exists and run_id == self.run_id) else None

    def is_closed(self, run_id):
        return self.closed and run_id == self.run_id


async def collect(source, **kwargs):
    kwargs.setdefault("sleep", _no_wait)
    return [frame async for frame in stream.event_stream(source, source.run_id, **kwargs)]


async def _no_wait(_seconds):
    return None


def frames_to_events(frames):
    out = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: "):]))
    return out


def frame_ids(frames):
    return [int(line[len("id: "):])
            for frame in frames for line in frame.splitlines()
            if line.startswith("id: ")]


DONE = [{"type": "stage", "stage": "localization", "status": "done"},
        {"type": "verdict", "hypothesis": "B", "verdict": "PROVEN"},
        {"type": "end", "state": "completed"}]


class FrameTests(unittest.TestCase):
    def test_a_frame_carries_its_index_and_a_json_payload(self):
        frame = stream.sse_frame(7, {"type": "verdict", "verdict": "PROVEN"})
        self.assertEqual(frame,
                         'id: 7\ndata: {"type": "verdict", "verdict": "PROVEN"}\n\n')

    def test_frames_are_not_named_events(self):
        """Named events would be dropped by any client that had not registered
        a listener for that exact name, which is a silent failure."""
        self.assertNotIn("event:", stream.sse_frame(0, {"type": "stage"}))

    def test_a_frame_never_contains_a_bare_newline_in_its_payload(self):
        frame = stream.sse_frame(0, {"type": "error", "message": "line\nbreak"})
        self.assertEqual(len([l for l in frame.split("\n") if l]), 2)

    def test_a_comment_is_a_valid_keep_alive(self):
        self.assertTrue(stream.sse_comment().startswith(":"))


class ResumeIndexTests(unittest.TestCase):
    def test_defaults_to_the_beginning(self):
        self.assertEqual(stream.resume_index(), 0)

    def test_the_query_parameter_is_honoured(self):
        self.assertEqual(stream.resume_index(12), 12)

    def test_last_event_id_wins_and_resumes_after_that_event(self):
        """The browser sets it from the last frame it actually received, which
        is a more recent fact than anything the page asked for on load."""
        self.assertEqual(stream.resume_index(0, "41"), 42)
        self.assertEqual(stream.resume_index(99, "3"), 4)

    def test_a_nonsense_last_event_id_is_ignored_rather_than_fatal(self):
        self.assertEqual(stream.resume_index(5, "not-a-number"), 5)

    def test_negatives_are_clamped(self):
        self.assertEqual(stream.resume_index(-3), 0)
        self.assertEqual(stream.resume_index(0, "-9"), 0)


class StreamTests(unittest.TestCase):
    def test_it_opens_with_a_retry_hint(self):
        frames = asyncio.run(collect(FakeSource(DONE)))
        self.assertTrue(frames[0].startswith("retry: "))

    def test_it_sends_every_event_in_order_and_stops_at_the_end(self):
        frames = asyncio.run(collect(FakeSource(DONE)))
        events = frames_to_events(frames)
        self.assertEqual([e["type"] for e in events],
                         ["stage", "verdict", "end"])

    def test_frame_ids_are_the_buffer_indices(self):
        frames = asyncio.run(collect(FakeSource(DONE)))
        self.assertEqual(frame_ids(frames), [0, 1, 2])

    def test_resuming_replays_only_what_was_missed(self):
        frames = asyncio.run(collect(FakeSource(DONE), start_index=2))
        self.assertEqual(frame_ids(frames), [2])
        self.assertEqual([e["type"] for e in frames_to_events(frames)], ["end"])

    def test_a_resumed_stream_keeps_the_original_indices(self):
        """So a second reconnect resumes from the right place too."""
        frames = asyncio.run(collect(FakeSource(DONE), start_index=1))
        self.assertEqual(frame_ids(frames), [1, 2])

    def test_a_finished_run_with_nothing_left_closes_instead_of_hanging(self):
        source = FakeSource(DONE[:2], closed=True)
        frames = asyncio.run(collect(source, start_index=2))
        self.assertEqual(frames_to_events(frames), [])

    def test_a_run_that_no_longer_exists_reports_an_error_and_closes(self):
        source = FakeSource([], exists=False)
        events = frames_to_events(asyncio.run(collect(source)))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("no longer available", events[0]["message"])

    def test_a_disconnected_client_stops_the_stream(self):
        """The retry hint is already on its way out when the check happens, so
        what matters is that no investigation event is written to a socket
        nobody is reading."""
        async def gone():
            return True
        frames = asyncio.run(collect(FakeSource(DONE), is_disconnected=gone))
        self.assertEqual(frames_to_events(frames), [])

    def test_a_quiet_stream_sends_a_keep_alive(self):
        """A phase can take several seconds. Without a comment frame in the
        gap, a proxy is entitled to decide the connection died."""
        import itertools
        source = FakeSource([], closed=False)
        polls = itertools.count(1)

        async def poll(_seconds):
            if next(polls) >= 2:
                source.events = DONE

        frames = asyncio.run(collect(
            source, sleep=poll, heartbeat_seconds=10.0,
            clock=itertools.count(0.0, 50.0).__next__))
        self.assertTrue(any(f.startswith(":") for f in frames),
                        "expected a keep-alive comment, got %r" % frames)
        self.assertEqual([e["type"] for e in frames_to_events(frames)],
                         ["stage", "verdict", "end"])

    def test_events_arriving_late_are_still_delivered(self):
        """The buffer grows while the stream is attached - that is the whole
        point of it."""
        source = FakeSource([{"type": "stage", "stage": "planning", "status": "running"}])

        async def add_the_rest():
            if len(source.events) < 3:
                source.events = DONE
            return False

        events = frames_to_events(asyncio.run(
            collect(source, is_disconnected=add_the_rest)))
        self.assertEqual([e["type"] for e in events][-1], "end")


if __name__ == "__main__":
    unittest.main()
