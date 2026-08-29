"""Server-Sent Events: framing, resumption, and the stream itself.

Deliberately independent of any web framework. The generator below takes a run
source and a disconnect probe and yields strings; FastAPI's job in api.py is
to hand it a request and put the result in a response. Keeping it separable is
not architecture for its own sake - it means the part with the awkward
behaviour (resuming, closing, not hanging on a finished run) can be executed
and tested on its own, which is exactly the part that is expensive to debug
through a browser at 2am.

Resumability is the point. An investigation is tens of seconds of real
measurement; a dropped connection must never mean running the sandbox again.
Every frame carries its buffer index as `id:`, so a browser that reconnects
with Last-Event-ID gets precisely the events it missed.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Awaitable, Callable, Optional

# How often the stream checks the buffer. The investigation spends its time
# inside a replayed workload, so this only decides how promptly a finished
# phase reaches the screen.
POLL_SECONDS = 0.1
# A comment frame on a quiet stream, so proxies do not decide the connection
# died. A single phase can take several seconds.
HEARTBEAT_SECONDS = 10.0

# Appended by the run manager after the final engine event.
END = "end"


def sse_frame(index: int, event: dict) -> str:
    """One SSE frame. The index doubles as the resume cursor.

    Deliberately NOT a named SSE event. Naming them would mean a browser only
    receives types somebody remembered to register a listener for, and a new
    event type would be dropped in silence rather than shown. Every frame
    arrives as a default `message` and carries its type inside the payload,
    where the client and `curl` can both see it.
    """
    return "id: %d\ndata: %s\n\n" % (index, json.dumps(event))


def sse_comment(text: str = "keep-alive") -> str:
    return ": %s\n\n" % text


def resume_index(from_index: int = 0, last_event_id: Optional[str] = None) -> int:
    """Where to resume from.

    Last-Event-ID wins over the query parameter: the browser sets it from the
    last frame it actually received, which is a more recent fact about what the
    client has than anything it asked for when the page loaded.
    """
    index = max(0, int(from_index or 0))
    if last_event_id is not None:
        try:
            index = max(0, int(last_event_id) + 1)
        except (TypeError, ValueError):
            pass
    return index


async def event_stream(
    source,
    run_id: str,
    start_index: int = 0,
    is_disconnected: Callable[[], Awaitable[bool]] = None,
    sleep: Callable[[float], Awaitable[None]] = None,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    clock: Callable[[], float] = None,
) -> AsyncIterator[str]:
    """Yield SSE frames for one investigation until it has none left.

    `source` is duck-typed on the run manager: events_from, get, is_closed.
    The clock and sleep are injectable so the timing behaviour can be tested
    without waiting for it in real time.
    """
    import time as _time

    now = clock or _time.monotonic
    pause = sleep or asyncio.sleep
    disconnected = is_disconnected or (lambda: _never())

    yield "retry: 2000\n\n"
    index = max(0, start_index)
    last_frame = now()

    while True:
        if await disconnected():
            return

        batch = source.events_from(run_id, index)
        if batch:
            for event in batch:
                yield sse_frame(index, event)
                index += 1
                if event.get("type") == END:
                    # The buffer is complete. Closing here is what stops a
                    # browser reconnecting to a finished run forever, since
                    # EventSource retries on any close, clean ones included.
                    return
            last_frame = now()
            continue

        if source.get(run_id) is None:
            yield sse_frame(index, {
                "type": "error",
                "message": "investigation %s is no longer available" % run_id})
            return

        if source.is_closed(run_id):
            # Caught up on a finished run: there will never be more. Returning
            # rather than heartbeating forever lets a late reconnect close
            # cleanly instead of hanging.
            return

        if now() - last_frame >= heartbeat_seconds:
            yield sse_comment()
            last_frame = now()
        await pause(poll_seconds)


async def _never() -> bool:
    return False
