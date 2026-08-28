"""The demo order-service.

Standard library only, on purpose: the sandbox starts and stops this process
repeatedly during an investigation, and every dependency is one more thing
that can fail on stage.

The two deployed changes are represented as runtime flags. That is the one
place this demo simplifies reality, and it should be said out loud: a real
Causeway would rebuild from a reverted commit. What is NOT simplified is the
intervention itself - exactly one flag moves per experiment and every other
flag is held fixed, which is what makes it an intervention rather than a
before/after comparison.

  Flag B  chooses the predicate used to look up audit rows.
  Flag A  chooses whether the batch is walked directly or through the
          batching helper the refactor introduced.

A and B are deliberately orthogonal: A must be latency-neutral whether or not
B is on, because A is the decoy.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB_PATH = None

# Uses the index on order_audit(order_id).
INDEXED_PREDICATE = "order_id = ?"
# Change B: wrapping the column in an expression makes the index unusable, so
# every lookup degrades into a full scan of the audit table. Three lines in
# the diff; it is the actual cause of the incident.
SCANNING_PREDICATE = "order_id + 0 = ?"

SUMMARY_SQL = ("SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)), 0) "
               "FROM order_audit WHERE {pred}")

_lock = threading.Lock()
_flags = {"A": False, "B": False}


def get_flags() -> dict:
    with _lock:
        return dict(_flags)


def set_flags(new: dict) -> dict:
    with _lock:
        for key, value in new.items():
            if key not in _flags:
                raise KeyError("unknown flag: %r" % (key,))
            _flags[key] = bool(value)
        return dict(_flags)


# ---------------------------------------------------------------- code paths

def _summary(conn, order_id: int, flags: dict) -> dict:
    predicate = SCANNING_PREDICATE if flags.get("B") else INDEXED_PREDICATE
    events, payload_bytes = conn.execute(
        SUMMARY_SQL.format(pred=predicate), (order_id,)).fetchone()
    return {"order_id": order_id, "events": events, "payload_bytes": payload_bytes}


def _fetch_direct(conn, order_ids, flags):
    """The code path as it stood before the refactor."""
    return [_summary(conn, oid, flags) for oid in order_ids]


def _fetch_batched(conn, order_ids, flags):
    """Change A: the same lookups, routed through a batching helper.

    The refactor dedupes the batch, materialises each summary into a dict and
    flattens the results back into request order. Nine files, 412 lines - and
    it issues exactly the same queries as the code it replaced, which is
    precisely what makes it a convincing decoy.
    """
    seen, ordered = set(), []
    for oid in order_ids:
        if oid not in seen:
            seen.add(oid)
            ordered.append(oid)
    materialised = {oid: _summary(conn, oid, flags) for oid in ordered}
    return [materialised[oid] for oid in order_ids]


def fetch_summaries(conn, order_ids, flags):
    if flags.get("A"):
        return _fetch_batched(conn, order_ids, flags)
    return _fetch_direct(conn, order_ids, flags)


# ------------------------------------------------------------------- handler

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "causeway-order-service"
    # Without this, small keep-alive responses hit the delayed-ACK / Nagle
    # interaction and every request picks up a ~40 ms stall, which would swamp
    # the signal the experiment is trying to measure.
    disable_nagle_algorithm = True

    def log_message(self, *args):   # keep the replay output clean
        return

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._json(200, {"ok": True})
        if path == "/control/flags":
            return self._json(200, get_flags())
        if path == "/orders/audit":
            raw = (parse_qs(urlparse(self.path).query).get("ids") or [""])[0]
            try:
                order_ids = [int(x) for x in raw.split(",") if x]
            except ValueError:
                return self._json(400, {"error": "bad order ids"})
            if not order_ids:
                return self._json(400, {"error": "no order ids"})
            conn = sqlite3.connect(DB_PATH)
            try:
                summaries = fetch_summaries(conn, order_ids, get_flags())
            except sqlite3.Error as exc:
                return self._json(500, {"error": str(exc)})
            finally:
                conn.close()
            return self._json(200, {"summaries": summaries})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if path == "/control/flags":
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            try:
                return self._json(200, set_flags(payload))
            except KeyError as exc:
                return self._json(400, {"error": str(exc)})
        return self._json(404, {"error": "not found"})


def main():
    global DB_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--port", type=int, default=8801)
    args = parser.parse_args()
    DB_PATH = args.db
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print("order-service listening on 127.0.0.1:%d" % args.port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
