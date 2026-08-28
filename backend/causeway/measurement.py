"""Turning a replay into a comparable latency signature.

A signature is a small dict of measured numbers. An expectation is a small
dict of comparisons against those numbers. Nothing in this module knows what
an incident is; it only knows how to measure and how to compare.

Standard library only, and deliberately so - this module sits on the path to
the verdict, and everything on that path has to be inspectable.
"""
from __future__ import annotations

import math
import statistics

OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def percentile(values, fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def compute(samples) -> dict:
    """samples: iterable of (elapsed_ms, ok) pairs from one replay."""
    samples = list(samples)
    latencies = [ms for ms, _ in samples]
    errors = sum(0 if ok else 1 for _, ok in samples)
    n = len(samples)
    return {
        "n": n,
        "reps": 1,
        "p50_ms": round(percentile(latencies, 0.50), 3),
        "p95_ms": round(percentile(latencies, 0.95), 3),
        "max_ms": round(max(latencies), 3) if latencies else 0.0,
        "mean_ms": round(sum(latencies) / n, 3) if n else 0.0,
        "error_rate": round(errors / n, 4) if n else 0.0,
    }


def aggregate(signatures) -> dict:
    """Combine repeated replays of the SAME state into one signature.

    The median across repetitions, metric by metric.

    This exists because p95 over a few dozen requests is a tail statistic - at
    n=40 it is the second-slowest request in the replay. One antivirus scan,
    one scheduler hiccup, one garbage collection lands squarely in that tail
    and moves the whole phase. Measuring a phase more than once and taking the
    median outvotes the unlucky replay.

    Note what this is: a more robust ESTIMATOR. It does not change what a
    measurement has to clear, so it cannot influence a verdict.
    """
    measurements = [dict(item) for item in signatures if item]
    if not measurements:
        return compute([])
    if len(measurements) == 1:
        return measurements[0]

    out = {}
    for key in measurements[0]:
        values = [m[key] for m in measurements if key in m]
        if not values:
            continue
        median = statistics.median(values)
        if key in ("n", "reps"):
            out[key] = int(median)
        elif key == "error_rate":
            out[key] = round(median, 4)
        else:
            out[key] = round(median, 3)
    out["reps"] = len(measurements)
    return out


def matches(observed: dict, expected: dict):
    """Return (passed, [detail strings]). Pure comparison, no interpretation."""
    details = []
    passed = True
    for metric, rule in expected.items():
        if metric not in observed:
            details.append("%s: not measured" % metric)
            passed = False
            continue
        actual, op, threshold = observed[metric], rule["op"], rule["value"]
        ok = OPS[op](actual, threshold)
        details.append("%s %.3f %s %.3f -> %s"
                       % (metric, actual, op, threshold, "ok" if ok else "no"))
        passed = passed and ok
    return passed, details
