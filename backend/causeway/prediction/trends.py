"""Deterministic time-series arithmetic, shared by every detector.

Every function here is pure - given the same samples it returns the same
number, no state, no clock read except what is passed in - and every one of
them is written to survive the inputs real telemetry actually produces:
a single sample, duplicate timestamps, samples that arrived out of order,
a field that is simply absent from some samples and not others. None of
that is a crash; it is a `None` (not enough information) or a `0.0`
(no observed change), and callers are expected to check for `None`.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from causeway.telemetry.schema import TelemetrySample

Point = Tuple[float, float]   # (timestamp, value)


def points_for(samples: Sequence[TelemetrySample], field: str) -> List[Point]:
    """(timestamp, value) pairs for one field, samples that lack it dropped,
    sorted by timestamp so an out-of-order stream is handled the same as an
    in-order one. Duplicate timestamps are kept - deduping would be
    inventing an ordering the exporter did not provide."""
    points = [(s.timestamp, s.values[field]) for s in samples if field in s.values]
    return sorted(points, key=lambda p: p[0])


def latest_value(samples: Sequence[TelemetrySample], field: str) -> Optional[float]:
    points = points_for(samples, field)
    return points[-1][1] if points else None


def median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def moving_average(points: Sequence[Point], window: int) -> Optional[float]:
    if not points or window <= 0:
        return None
    tail = [v for _, v in points[-window:]]
    return sum(tail) / len(tail)


def delta(points: Sequence[Point]) -> Optional[float]:
    """Last value minus first value. None with fewer than two points."""
    if len(points) < 2:
        return None
    return points[-1][1] - points[0][1]


def slope(points: Sequence[Point]) -> Optional[float]:
    """Units per second, by ordinary least squares over every point given -
    robust to noisy individual samples in a way "last minus first over
    elapsed time" is not. None with fewer than two points, or when every
    point shares one timestamp (no time actually elapsed to have a slope
    over)."""
    n = len(points)
    if n < 2:
        return None
    mean_t = sum(t for t, _ in points) / n
    mean_v = sum(v for _, v in points) / n
    numerator = sum((t - mean_t) * (v - mean_v) for t, v in points)
    denominator = sum((t - mean_t) ** 2 for t, _ in points)
    if denominator == 0:
        return None
    return numerator / denominator


def baseline_ratio(recent: Sequence[float], baseline: Sequence[float]
                   ) -> Optional[float]:
    """How many times larger the recent window's median is than the
    baseline window's - None if either window is empty or the baseline is
    zero (a ratio against zero is not a ratio, it is a division error
    wearing a number's clothes)."""
    recent_med = median(recent)
    baseline_med = median(baseline)
    if recent_med is None or baseline_med is None or baseline_med == 0:
        return None
    return recent_med / baseline_med


def persistence(flags: Sequence[bool]) -> int:
    """How many of the most recent evaluations, counting back from the
    end, were all True. Stops at the first False. `[]` -> 0."""
    count = 0
    for flag in reversed(flags):
        if not flag:
            break
        count += 1
    return count


def eta_seconds(current: float, target: float, rate_per_second: Optional[float]
               ) -> Optional[float]:
    """Seconds until `current` reaches `target` at a constant `rate_per_
    second`, or None when that is not a sensible question: no rate, a rate
    moving the wrong way, or a target already passed. Never negative,
    never fabricated from fewer than two real samples - callers pass None
    for `rate_per_second` whenever `slope()` itself returned None."""
    if rate_per_second is None or rate_per_second <= 0:
        return None
    remaining = target - current
    if remaining <= 0:
        return 0.0
    return remaining / rate_per_second
