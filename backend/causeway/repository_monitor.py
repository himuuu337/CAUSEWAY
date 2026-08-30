"""Real live telemetry for a user-provided `causeway.json` repository - the
same real-measurement discipline `causeway/demo/production_service.py`
already applies to the bundled `demo-repo-pool/`, generalized to any
repository that declares the same contract.

    GitHub URL (with causeway.json)
      -> validate, clone, load (causeway.repository.acquire - the same
         front door a manual investigation uses)
      -> a Sandbox launched against the repository's OWN entrypoint and OWN
         database (causeway.sandbox.runner.Sandbox, service_path pointed at
         the clone - the identical primitive causeway.sandbox.actuator.
         SourceActuator already uses for this same repository's causal
         experiment)
      -> the repository's OWN declared workload replayed against it
         (causeway.sandbox.replay.replay) - real HTTP requests, real
         latencies, real response statuses
      -> a real telemetry sample built from what was actually observed, and
         posted to Causeway's own /api/telemetry

A repository with no causeway.json is refused, plainly, rather than
guessed at: nothing here knows how to start it or what to send it, the same
reason causeway/standard_investigation.py never executes one either.

Nothing here is a new trust boundary. Every repository this can target is
already executed today, by the causal-experiment path, through the exact
same Sandbox class, against the exact same database and entrypoint
contract - this only runs it for longer, continuously, instead of in seven
short measured phases. It is a best-effort subprocess boundary the same way
every other sandbox use in this codebase is (argv only, its own disposable
clone, its own disposable database, real health-checking) - not OS-level
isolation, and nothing here claims otherwise.

One real difference from the causal-experiment path, deliberately: this
never calls Sandbox.restore() or Sandbox.measure() mid-session. A controlled
experiment resets between phases on purpose; live monitoring wants the
database and process state to accumulate naturally across samples, because
sustained drift is exactly the signal causeway.prediction looks for.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence, Tuple

from causeway import repository as repository_module
from causeway.demo.production_service import post_telemetry
from causeway.repository import RepositoryContext
from causeway.repository.standard import StandardRepositoryContext
from causeway.sandbox.replay import replay
from causeway.sandbox.runner import Sandbox
from causeway.sandbox.variant import materialise

DEFAULT_CAUSEWAY_URL = "http://127.0.0.1:8000"
TELEMETRY_INTERVAL_SECONDS = 2.0


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(pct / 100.0 * (len(ordered) - 1)))))
    return ordered[index]


def aggregate_sample(service_name: str, replay_samples: Sequence[Tuple[float, bool]],
                     window_seconds: float, health_body: Optional[dict] = None) -> dict:
    """A real telemetry sample from one real replay batch. Every field is
    either a real measurement of `replay_samples` (the elapsed_ms/ok pairs
    causeway.sandbox.replay.replay actually observed) or a real value read
    from `health_body` - never invented. Mirrors causeway.demo.
    production_service.build_sample's own arithmetic and its exact
    opportunistic-inclusion rule for db_pool_* fields: included only when
    the repository's own /health response happens to expose a `pool`
    object, in the same shape, never fabricated when it does not."""
    latencies = [elapsed for elapsed, _ok in replay_samples]
    failed = sum(1 for _elapsed, ok in replay_samples if not ok)
    request_rate = len(replay_samples) / window_seconds if window_seconds > 0 else 0.0
    error_rate = (failed / len(replay_samples)) if replay_samples else 0.0

    sample = {"service": service_name, "request_rate": round(request_rate, 2),
             "error_rate": round(error_rate, 4)}
    if latencies:
        sample["p50_ms"] = round(_percentile(latencies, 50), 1)
        sample["p95_ms"] = round(_percentile(latencies, 95), 1)
        sample["p99_ms"] = round(_percentile(latencies, 99), 1)

    pool = (health_body or {}).get("pool", {}) if health_body else {}
    if "used" in pool and "capacity" in pool:
        sample["db_pool_used"] = float(pool["used"])
        sample["db_pool_capacity"] = float(pool["capacity"])
    if "waiting" in pool:
        sample["db_waiting_requests"] = float(pool["waiting"])
    return sample


def _health(sandbox: Sandbox) -> Optional[dict]:
    """One real /health read - the same endpoint Sandbox.start() already
    polled to confirm the service was up. None on any failure; a health
    read is a nice-to-have for opportunistic pool fields, never something
    this loop should stop over."""
    import http.client
    import json
    try:
        conn = http.client.HTTPConnection(sandbox.host, sandbox.port, timeout=3.0)
        try:
            conn.request("GET", "/health")
            response = conn.getresponse()
            raw = response.read()
            return json.loads(raw) if raw else None
        finally:
            conn.close()
    except Exception:
        return None


def run(repository_url: str, service_name: str, instruction: str = "",
       causeway_url: str = DEFAULT_CAUSEWAY_URL, duration_seconds: float = None,
       interval_seconds: float = TELEMETRY_INTERVAL_SECONDS,
       on_sample: Optional[Callable[[dict, bool], None]] = None,
       on_event: Optional[Callable[[dict], None]] = None) -> None:
    """Clone `repository_url`, run its own entrypoint against its own
    database, replay its own declared workload on a loop, and post what was
    actually measured to `causeway_url`/api/telemetry as `service_name` -
    until `duration_seconds` elapses or the caller raises KeyboardInterrupt.

    Raises RuntimeError, before anything is launched, for a repository with
    no causeway.json (nothing here guesses how to start or load one) or one
    `causeway.repository.acquire` itself rejects.
    """
    context = None
    for item in repository_module.acquire(repository_url, instruction=instruction):
        if isinstance(item, (RepositoryContext, StandardRepositoryContext)):
            context = item
        elif item is not None:
            if on_event is not None:
                on_event(item)
            if item.get("type") == "repository_rejected":
                raise RuntimeError("repository rejected: %s" % item.get("reason"))

    if context is None:
        raise RuntimeError("the repository could not be loaded")
    if isinstance(context, StandardRepositoryContext):
        context.cleanup()
        raise RuntimeError(
            "%s has no causeway.json, so there is no declared workload and no reliable "
            "way to start it - live monitoring needs the same contract the causal "
            "experiment needs (an entrypoint, a workload, a database built from the "
            "repository's own schema)" % repository_url)

    try:
        # A disposable copy, launched once for the whole session - never the
        # clone itself. No edits are ever applied (there is no counterfactual
        # here, just "run it as it is"), but the invariant this codebase
        # holds everywhere else - the clone is never written to - is kept
        # absolute rather than "true because nothing happens to write to it".
        variant = materialise(context.workspace, context.entrypoint, edits=())
        try:
            sandbox = Sandbox(context.database_path, context.work_db,
                              service_path=variant.service_path).start()
            try:
                started = time.time()
                while duration_seconds is None or (time.time() - started) < duration_seconds:
                    batch_started = time.perf_counter()
                    samples = replay(sandbox.host, sandbox.port, context.workload)
                    window = time.perf_counter() - batch_started
                    health_body = _health(sandbox)
                    sample = aggregate_sample(service_name, samples, window, health_body)
                    posted = post_telemetry(causeway_url, sample)
                    if on_sample is not None:
                        on_sample(sample, posted)
                    time.sleep(interval_seconds)
            finally:
                sandbox.stop()
        finally:
            variant.cleanup()
    finally:
        context.cleanup()
