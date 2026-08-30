"""The order-service, with a real connection pool bug.

    python app.py --db <path> --port <port>

`_do_work` acquires a pool slot, computes a summary, and releases the slot
- as three ordinary, sequential statements. `compute_summary` sometimes
raises (a stand-in for a downstream call that is not always reliable, which
is not a bug in itself); when it does, `pool.release()` - the next
statement in the same function - is never reached, and that slot leaks for
the rest of this process's life. Causeway's resource_release_not_guaranteed
detector finds exactly this shape.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pool import ConnectionPool, PoolExhausted

DB_PATH = None
POOL_CAPACITY = 12
pool = ConnectionPool(POOL_CAPACITY)

# A second resource with the exact same acquire-then-release shape, sized so
# large it is never actually exhausted by the same failure path - a decoy,
# on purpose. It exists so Causeway's detector has two statically identical
# suspects to discriminate between, the same way demo-repo's SQL-predicate
# detector always finds two indistinguishable wrapped predicates: nothing
# about reading the source can tell these two apart, and that is the point
# - only measuring which one actually causes the incident can.
METRICS_POOL_CAPACITY = 100_000
metrics_pool = ConnectionPool(METRICS_POOL_CAPACITY)


def compute_summary(order_id: int) -> dict:
    """Simulates a downstream call that sometimes fails - a timeout talking
    to another service, a bad row, anything a real handler cannot always
    avoid. One in five ids fails, deterministically, so this demo is
    reproducible rather than flaky."""
    if order_id % 5 == 0:
        raise RuntimeError("downstream summary lookup failed for order %d" % order_id)
    return {"order_id": order_id, "status": "ok"}


def _touch_metric(order_id: int) -> None:
    return None


def record_metric(order_id: int) -> None:
    metrics_pool.acquire()
    _touch_metric(order_id)
    metrics_pool.release()


def _do_work(order_id: int) -> dict:
    record_metric(order_id)
    pool.acquire()
    summary = compute_summary(order_id)
    pool.release()
    return {"summary": summary, "pool": pool.snapshot()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "order-service"
    disable_nagle_algorithm = True

    def log_message(self, *args):
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
            return self._json(200, {"ok": True, "pool": pool.snapshot()})
        if path == "/work":
            raw = (parse_qs(urlparse(self.path).query).get("id") or ["0"])[0]
            try:
                order_id = int(raw)
            except ValueError:
                return self._json(400, {"error": "id must be an integer"})
            try:
                result = _do_work(order_id)
            except PoolExhausted as exc:
                return self._json(503, {"error": str(exc), "pool": pool.snapshot()})
            except RuntimeError as exc:
                return self._json(500, {"error": str(exc), "pool": pool.snapshot()})
            return self._json(200, result)
        return self._json(404, {"error": "not found"})


def main():
    global DB_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--port", type=int, default=8802)
    args = parser.parse_args()
    DB_PATH = args.db
    # Proves the seeded database is real and reachable. This demo's
    # incident lives in the pool, not in a query, so nothing else here
    # reads from it.
    sqlite3.connect(DB_PATH).close()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print("order-service (pool demo) listening on 127.0.0.1:%d" % args.port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
