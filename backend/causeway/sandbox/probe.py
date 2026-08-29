"""A single real HTTP request against a running sandbox, for requested-change
verification.

causeway.sandbox.replay measures a fixed workload for a latency comparison;
this sends one named request and reports what actually came back. Gemini does
not decide whether a requested change worked - this module is what does:
it is the thing that actually talks to the process a disposable, patched copy
of the repository is running.
"""
from __future__ import annotations

import http.client
import json


def send(host: str, port: int, method: str, path: str, body=None,
        timeout: float = 10.0) -> dict:
    """POST/GET one request and report status, parsed JSON body (if any), and
    whatever went wrong instead, if anything did."""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = ({"Content-Type": "application/json",
                    "Content-Length": str(len(payload))} if payload is not None else {})
        conn.request(method, path, payload, headers)
        response = conn.getresponse()
        raw = response.read()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return {"status": response.status, "body": parsed, "error": None}
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        return {"status": None, "body": None, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        try:
            conn.close()
        except Exception:
            pass
