"""Build the sandbox database, and size it to this machine.

Determinism first: fixed row count, fixed RNG seed, fixed payload width,
ANALYZE at the end. Every phase of every experiment starts from a
byte-identical copy of what this produces.

Then calibration. The demo needs the incident state to be roughly 14x the
healthy state - big enough to be unmistakable, small enough that a full
investigation finishes while a judge is still watching. How many audit rows
that takes depends entirely on the machine: its disk, its cache, whether an
antivirus is reading along. So seeding MEASURES this machine and sizes the
table for it, rather than shipping a row count that was right on somebody
else's laptop.

Nothing measured here ever reaches a verdict. The numbers below are setup
diagnostics; every experiment measures its own controls while it runs.
"""
from __future__ import annotations

import os
import random
import sqlite3
import string

from causeway import verdict
from causeway.sandbox.replay import build_fixture
from causeway.sandbox.runner import Sandbox

ORDERS = 5_000
PAYLOAD_WIDTH = 120
RNG_SEED = 20260828
ACTIONS = ("created", "priced", "reserved", "packed", "shipped", "settled")

# Where calibration is aiming, expressed as incident p95 / healthy p95.
TARGET_RATIO = 14.0
# Narrow on purpose. A ratio of 9x would technically clear the 4x the verdict
# needs, but the margin between "the failure is present" and the noise is the
# spine of the whole demo, so calibration keeps converging until it is close
# to the target rather than merely acceptable.
ACCEPT_RATIO = (11.5, 17.5)
PROBE_ROWS = 24_000
MIN_ROWS, MAX_ROWS = 4_000, 400_000
MAX_ROUNDS = 4

SCHEMA = """
DROP TABLE IF EXISTS order_audit;
CREATE TABLE order_audit (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL,
    action     TEXT    NOT NULL,
    payload    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
"""


def build(db_path: str, audit_rows: int) -> dict:
    if os.path.exists(db_path):
        os.remove(db_path)
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    rng = random.Random(RNG_SEED)
    alphabet = string.ascii_lowercase + string.digits
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.executescript(SCHEMA)
        chunk = []
        for row_id in range(1, audit_rows + 1):
            chunk.append((
                row_id,
                (row_id % ORDERS) + 1,
                ACTIONS[row_id % len(ACTIONS)],
                "".join(rng.choice(alphabet) for _ in range(PAYLOAD_WIDTH)),
                "2026-08-%02dT%02d:%02d:00Z" % ((row_id % 28) + 1, row_id % 24,
                                                row_id % 60),
            ))
            if len(chunk) >= 20_000:
                conn.executemany("INSERT INTO order_audit VALUES (?,?,?,?,?)", chunk)
                chunk.clear()
        if chunk:
            conn.executemany("INSERT INTO order_audit VALUES (?,?,?,?,?)", chunk)
        conn.execute("CREATE INDEX idx_audit_order ON order_audit(order_id)")
        conn.execute("ANALYZE")
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()

    return {"db_path": db_path, "orders": ORDERS, "audit_rows": audit_rows,
            "bytes": os.path.getsize(db_path)}


def _measure_states(template_db: str, work_db: str, fixture: dict,
                    repetitions: int) -> tuple:
    with Sandbox(template_db, work_db) as sandbox:
        healthy = sandbox.measure(fixture, {"A": False, "B": False}, repetitions)
        incident = sandbox.measure(fixture, {"A": True, "B": True}, repetitions)
    return healthy, incident


def _next_row_count(rows: int, healthy_ms: float, incident_ms: float) -> int:
    """The scan cost is roughly linear in the number of rows, so the marginal
    cost per row tells us how many rows the target ratio needs."""
    per_row = (incident_ms - healthy_ms) / max(rows, 1)
    if per_row <= 0:
        return min(MAX_ROWS, rows * 4)
    wanted = healthy_ms * (TARGET_RATIO - 1.0) / per_row
    return int(max(MIN_ROWS, min(MAX_ROWS, round(wanted))))


def calibrate(template_db: str, work_db: str, repetitions: int = 2,
              on_round=None) -> dict:
    """Size the audit table so this machine shows the incident at ~14x healthy.

    Returns the accepted sizing plus every round it took to get there, so the
    setup is auditable rather than magic.
    """
    fixture = build_fixture(ORDERS)
    rows = PROBE_ROWS
    rounds = []
    best = None

    for attempt in range(1, MAX_ROUNDS + 1):
        info = build(template_db, rows)
        healthy, incident = _measure_states(template_db, work_db, fixture,
                                            repetitions)
        ratio = incident["p95_ms"] / max(healthy["p95_ms"], 1e-9)
        record = {
            "round": attempt,
            "audit_rows": rows,
            "bytes": info["bytes"],
            "healthy_p95_ms": healthy["p95_ms"],
            "incident_p95_ms": incident["p95_ms"],
            "ratio": round(ratio, 2),
            "accepted": ACCEPT_RATIO[0] <= ratio <= ACCEPT_RATIO[1],
        }
        rounds.append(record)
        if on_round:
            on_round(record)
        if best is None or abs(ratio - TARGET_RATIO) < abs(best["ratio"] - TARGET_RATIO):
            best = record
        if record["accepted"]:
            break
        rows = _next_row_count(rows, healthy["p95_ms"], incident["p95_ms"])
        if rows == record["audit_rows"]:
            break

    final = rounds[-1]
    if not final["accepted"] and best is not final:
        # rebuild at the closest size we saw, so the shipped database is the
        # best one measured rather than the last one tried
        build(template_db, best["audit_rows"])
        final = best

    return {
        "orders": ORDERS,
        "audit_rows": final["audit_rows"],
        "bytes": final["bytes"],
        "healthy_p95_ms": final["healthy_p95_ms"],
        "incident_p95_ms": final["incident_p95_ms"],
        "ratio": final["ratio"],
        "separable": verdict.separates(final["healthy_p95_ms"],
                                       final["incident_p95_ms"]),
        "rounds": rounds,
        "note": ("setup diagnostics only - every experiment measures its own "
                 "controls while it runs, and no number here reaches a verdict"),
    }
