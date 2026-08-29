"""The order service.

Standard library only, on purpose: Causeway launches this as a subprocess,
never imports it, and never installs a dependency on its behalf.

    python app.py --db <path> --port <port>
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import db

DB_PATH = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "order-service"
    # Without this, small keep-alive responses hit the delayed-ACK / Nagle
    # interaction and every request picks up a ~40ms stall.
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
            return self._json(200, {"ok": True})
        if path == "/orders/audit":
            raw = (parse_qs(urlparse(self.path).query).get("ids") or [""])[0]
            try:
                order_ids = [int(x) for x in raw.split(",") if x]
            except ValueError:
                return self._json(400, {"error": "bad order ids"})
            if not order_ids:
                return self._json(400, {"error": "no order ids"})
            conn = db.connect(DB_PATH)
            try:
                page = db.audit_page(conn, order_ids)
            finally:
                conn.close()
            return self._json(200, {"summaries": page})
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
