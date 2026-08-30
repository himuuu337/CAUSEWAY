"""Runs demo-repo-pool/app.py - a real service with a real connection-pool
bug - drives real load against it, and reports what is REALLY happening
inside it to Causeway's own telemetry API.

    python -m causeway.cli telemetry-demo

This module plays the "telemetry exporter" role: it does not compute risk
or decide anything - it only measures (the service's own /health endpoint,
and its own client-observed request latencies) and reports what it
measured. causeway.prediction, reading the same numbers back out of
causeway.telemetry's store, is what decides whether they look like
sustained movement toward a known failure condition. Nothing here is a
fabricated number: every field posted came from a real HTTP response.
"""
from __future__ import annotations

import collections
import http.client
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, List, Optional, Tuple

DEMO_REPO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "..", "demo-repo-pool")
DEMO_REPO = os.path.normpath(DEMO_REPO)

DEFAULT_CAUSEWAY_URL = "http://127.0.0.1:8000"
DEFAULT_SERVICE_NAME = "order-service-pool"
WINDOW_SECONDS = 15.0
TELEMETRY_INTERVAL_SECONDS = 2.0
# Deliberately modest: demo-repo-pool's pool (capacity 12, a 1-in-5 leak
# rate) exhausts within a handful of seconds at full speed, which makes a
# fine, strong signal for the one-shot causal experiment's fixed 40-request
# workload but far too abrupt for a LIVE demo to visibly move through
# LOW -> MEDIUM -> HIGH rather than jumping straight there. Slowing the
# client down - not the pool, not the bug - is what stretches that out
# without touching the repository the causal experiment already measures
# against.
LOAD_WORKERS = 3
REQUEST_INTERVAL_SECONDS = 0.6


def _free_port(start: int = 8802, attempts: int = 60) -> int:
    import socket
    for offset in range(attempts):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port found for the demo service")


class DemoService:
    """Launches demo-repo-pool/app.py as a real subprocess. Nothing here
    reads its source or reaches into its process directly - only its own
    HTTP surface, the same way a real exporter would have to."""

    def __init__(self, port: int = None, db_path: str = None, repo_dir: str = None):
        self.port = port or _free_port()
        self.host = "127.0.0.1"
        self._repo_dir = repo_dir or DEMO_REPO
        self._db_path = db_path or os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMPDIR") or ".",
            "causeway-telemetry-demo-%d.db" % self.port)
        self._process: Optional[subprocess.Popen] = None

    def start(self, timeout: float = 15.0) -> "DemoService":
        entrypoint = os.path.join(self._repo_dir, "app.py")
        self._process = subprocess.Popen(
            [sys.executable, entrypoint, "--db", self._db_path, "--port", str(self.port)],
            cwd=self._repo_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._process.poll() is not None:
                output = self._process.stdout.read().decode(errors="replace")
                raise RuntimeError("demo service exited early:\n" + output)
            ok, _elapsed, _body = self.request("/health")
            if ok:
                return self
            time.sleep(0.1)
        raise RuntimeError("demo service did not become healthy in time")

    def stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        if self._process.stdout is not None:
            self._process.stdout.close()
        self._process = None

    def request(self, path: str, timeout: float = 3.0) -> Tuple[bool, float, Optional[dict]]:
        """One real request. Returns (ok, elapsed_ms, body-or-None)."""
        started = time.perf_counter()
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            raw = response.read()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            ok = response.status < 400
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                body = None
            return ok, elapsed_ms, body
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return False, elapsed_ms, None
        finally:
            try:
                conn.close()
            except Exception:
                pass


class LoadGenerator:
    """Continuous, real client traffic against the demo service - a rolling
    record of what actually happened, never a fabricated number."""

    def __init__(self, service: DemoService, workers: int = LOAD_WORKERS):
        self._service = service
        self._workers = workers
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()
        self._samples = collections.deque(maxlen=20000)   # (timestamp, elapsed_ms, ok)
        self._next_id = 0

    def _next_order_id(self) -> int:
        with self._lock:
            self._next_id += 1
            return self._next_id

    def _worker(self) -> None:
        while not self._stop.is_set():
            order_id = self._next_order_id()
            ok, elapsed_ms, _body = self._service.request("/work?id=%d" % order_id)
            with self._lock:
                self._samples.append((time.time(), elapsed_ms, ok))
            time.sleep(REQUEST_INTERVAL_SECONDS)

    def start(self) -> None:
        for _ in range(self._workers):
            thread = threading.Thread(target=self._worker, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)

    def window(self, seconds: float = WINDOW_SECONDS):
        cutoff = time.time() - seconds
        with self._lock:
            return [s for s in self._samples if s[0] >= cutoff]


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(pct / 100.0 * (len(ordered) - 1)))))
    return ordered[index]


def build_sample(service_name: str, service: DemoService, load: LoadGenerator) -> dict:
    """Every field here is either a real measurement the load generator
    took over its own rolling window, or a real value read from the
    service's own /health response - never invented."""
    window = load.window()
    latencies = [elapsed for _t, elapsed, _ok in window]
    failed = sum(1 for _t, _e, ok in window if not ok)
    request_rate = len(window) / WINDOW_SECONDS if window else 0.0
    error_rate = (failed / len(window)) if window else 0.0

    ok, _elapsed, body = service.request("/health")
    pool = (body or {}).get("pool", {}) if body else {}

    sample = {"service": service_name, "request_rate": round(request_rate, 2),
             "error_rate": round(error_rate, 4)}
    if latencies:
        sample["p50_ms"] = round(_percentile(latencies, 50), 1)
        sample["p95_ms"] = round(_percentile(latencies, 95), 1)
        sample["p99_ms"] = round(_percentile(latencies, 99), 1)
    if "used" in pool and "capacity" in pool:
        sample["db_pool_used"] = float(pool["used"])
        sample["db_pool_capacity"] = float(pool["capacity"])
    if "waiting" in pool:
        sample["db_waiting_requests"] = float(pool["waiting"])
    return sample


def post_telemetry(causeway_url: str, sample: dict, timeout: float = 5.0) -> bool:
    body = json.dumps(sample).encode("utf-8")
    request = urllib.request.Request(
        causeway_url.rstrip("/") + "/api/telemetry", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except urllib.error.URLError:
        return False


def run(causeway_url: str = DEFAULT_CAUSEWAY_URL, service_name: str = DEFAULT_SERVICE_NAME,
       duration_seconds: float = None,
       on_sample: Callable[[dict, bool], None] = None) -> None:
    """Start the demo service, drive real load against it, and post real
    telemetry to `causeway_url` every TELEMETRY_INTERVAL_SECONDS until
    interrupted (Ctrl-C) or `duration_seconds` elapses."""
    service = DemoService().start()
    load = LoadGenerator(service)
    load.start()
    started = time.time()
    try:
        while duration_seconds is None or (time.time() - started) < duration_seconds:
            time.sleep(TELEMETRY_INTERVAL_SECONDS)
            sample = build_sample(service_name, service, load)
            posted = post_telemetry(causeway_url, sample)
            if on_sample is not None:
                on_sample(sample, posted)
    finally:
        load.stop()
        service.stop()
