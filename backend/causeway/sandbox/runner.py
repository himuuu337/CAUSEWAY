"""Sandbox lifecycle.

One order-service process against a disposable copy of the template database.
Between every phase the database is restored and the flag state is set, so
each phase starts from identical conditions and the only thing that differs
between two phases is the intervention under test.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time

from causeway import measurement
from causeway.sandbox.replay import replay

# How many times a phase is replayed before its median is taken. A measurement
# decision, not a threshold: it changes how well a phase is estimated and never
# what the estimate has to clear, so it cannot reach the verdict.
REPETITIONS = 3


def free_port(start: int = 8801, attempts: int = 60) -> int:
    for offset in range(attempts):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port found for the sandbox")


class Sandbox:
    def __init__(self, template_db: str, work_db: str, port: int = None,
                 repo_root: str = None, service_path: str = None):
        self.template_db = template_db
        self.work_db = work_db
        self.port = port or free_port()
        self.host = "127.0.0.1"
        self.repo_root = repo_root or os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # None runs the real `causeway.sandbox.service` module, exactly as
        # before. A path here (Milestone 5's fix loop) runs a disposable,
        # already-patched COPY of it instead - the developer's checkout is
        # never the thing this process executes.
        self.service_path = service_path
        self._process = None
        self._template_print = None
        self.restores_copied = 0
        self.restores_verified = 0

    # -- lifecycle ---------------------------------------------------------
    def start(self, timeout: float = 25.0):
        self.restore()
        launch = ([sys.executable, "-m", "causeway.sandbox.service"]
                  if self.service_path is None
                  else [sys.executable, self.service_path])
        self._process = subprocess.Popen(
            launch + ["--db", self.work_db, "--port", str(self.port)],
            cwd=self.repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._process.poll() is not None:
                output = self._process.stdout.read().decode(errors="replace")
                raise RuntimeError("sandbox service exited early:\n" + output)
            try:
                conn = http.client.HTTPConnection(self.host, self.port, timeout=1.0)
                conn.request("GET", "/health")
                healthy = conn.getresponse().status == 200
                conn.close()
                if healthy:
                    return self
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("sandbox service did not become healthy")

    def stop(self):
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

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    # -- environment -------------------------------------------------------
    @staticmethod
    def _fingerprint(path: str) -> str:
        """Size plus the head and tail of the file. Cheap enough to run before
        every phase, specific enough to catch anything that wrote to it."""
        size = os.path.getsize(path)
        digest = hashlib.sha256(str(size).encode())
        with open(path, "rb") as handle:
            digest.update(handle.read(65536))
            if size > 131072:
                handle.seek(-65536, os.SEEK_END)
                digest.update(handle.read(65536))
        return digest.hexdigest()

    def restore(self, attempts: int = 10) -> str:
        """Put the database back to its template state.

        The replay workload is read-only, so the working copy is usually
        already pristine and the copy can be skipped - verified, not assumed.
        That matters on Windows, where copying tens of megabytes before every
        phase invites a real-time scanner into the measurement.

        Retried because on Windows a lingering file handle can briefly block
        the replace.
        """
        os.makedirs(os.path.dirname(self.work_db) or ".", exist_ok=True)
        if self._template_print is None:
            self._template_print = self._fingerprint(self.template_db)
        if os.path.exists(self.work_db):
            try:
                if self._fingerprint(self.work_db) == self._template_print:
                    self.restores_verified += 1
                    return "verified"
            except OSError:
                pass

        staging = self.work_db + ".staging"
        shutil.copyfile(self.template_db, staging)
        last_error = None
        for _ in range(attempts):
            try:
                os.replace(staging, self.work_db)
                self.restores_copied += 1
                return "copied"
            except OSError as exc:
                last_error = exc
                time.sleep(0.1)
        raise RuntimeError("could not restore the sandbox database: %s" % last_error)

    def set_flags(self, flags: dict) -> dict:
        body = json.dumps(flags).encode()
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10.0)
        try:
            conn.request("POST", "/control/flags", body,
                         {"Content-Type": "application/json",
                          "Content-Length": str(len(body))})
            response = conn.getresponse()
            payload = json.loads(response.read())
            if response.status != 200:
                raise RuntimeError("setting flags failed: %s" % payload)
            return payload
        finally:
            conn.close()

    def get_flags(self) -> dict:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10.0)
        try:
            conn.request("GET", "/control/flags")
            return json.loads(conn.getresponse().read())
        finally:
            conn.close()

    # -- measurement -------------------------------------------------------
    def replay_once(self, fixture: dict):
        return replay(self.host, self.port, fixture)

    def measure(self, fixture: dict, flags: dict, repetitions: int = None) -> dict:
        """Measure one state: restore, set the flags, replay, repeat, take the
        median. Every phase in an experiment goes through this one function,
        so no phase is measured more carefully than any other."""
        reps = max(1, int(REPETITIONS if repetitions is None else repetitions))
        signatures = []
        for _ in range(reps):
            self.restore()
            self.set_flags(flags)
            signatures.append(measurement.compute(self.replay_once(fixture)))
        return measurement.aggregate(signatures)
