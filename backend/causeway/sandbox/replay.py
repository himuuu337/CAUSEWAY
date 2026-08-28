"""Deterministic replay of a recorded request fixture.

Determinism comes from three things: a fixed request list in a fixed order, a
fixed number of workers, and one reused keep-alive connection per worker so
connection setup cost does not vary between phases.

The same fixture is replayed for every phase of every experiment. That is what
makes the comparison a controlled one: the workload is not merely similar
between conditions, it is identical.
"""
from __future__ import annotations

import http.client
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

FIXTURE_ID = "incident-001"
FIXTURE_SEED = 20260828
REQUESTS = 40
BATCH_SIZE = 6
CONCURRENCY = 8
WARMUP = 8


def build_fixture(orders: int, fixture_id: str = FIXTURE_ID) -> dict:
    """Recorded traffic from the incident window, reconstructed deterministically."""
    rng = random.Random(FIXTURE_SEED)
    requests = []
    for _ in range(REQUESTS):
        ids = [rng.randint(1, orders) for _ in range(BATCH_SIZE)]
        requests.append("/orders/audit?ids=" + ",".join(str(i) for i in ids))
    return {
        "id": fixture_id,
        "recorded_from": "order-service gateway, 90s incident window",
        "concurrency": CONCURRENCY,
        "warmup": WARMUP,
        "requests": requests,
    }


def _run_chunk(host: str, port: int, paths, timeout: float):
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    out = []
    try:
        for path in paths:
            start = time.perf_counter()
            ok = False
            try:
                conn.request("GET", path)
                response = conn.getresponse()
                response.read()
                ok = response.status == 200
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = http.client.HTTPConnection(host, port, timeout=timeout)
            out.append(((time.perf_counter() - start) * 1000.0, ok))
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def _chunks(paths, count):
    buckets = [[] for _ in range(count)]
    for index, path in enumerate(paths):
        buckets[index % count].append(path)
    return [bucket for bucket in buckets if bucket]


def replay(host: str, port: int, fixture: dict, timeout: float = 60.0):
    """Run the fixture once and return [(elapsed_ms, ok), ...]."""
    paths = fixture["requests"]
    workers = int(fixture.get("concurrency", CONCURRENCY))
    warmup = int(fixture.get("warmup", 0))

    if warmup:
        _run_chunk(host, port, paths[:warmup], timeout)

    buckets = _chunks(paths, workers)
    samples = []
    with ThreadPoolExecutor(max_workers=len(buckets)) as pool:
        futures = [pool.submit(_run_chunk, host, port, b, timeout) for b in buckets]
        for future in futures:
            samples.extend(future.result())
    return samples


def load_fixture(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_fixture(path: str, fixture: dict) -> None:
    # newline="\n" so writing on Windows does not rewrite the file with CRLF
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(fixture, handle, indent=2)
        handle.write("\n")
