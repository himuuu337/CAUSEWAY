"""Detector 1: connection pool exhaustion.

Pool utilization climbing alone is not enough - a burst of traffic can push
utilization up and back down inside a second. What this detector actually
requires is utilization AND waiting requests AND request latency moving
together, which is what a pool that is not giving connections back looks
like from the outside. `_elevated_signal_count` is the "no HIGH from one
spike" rule: fewer than two signals elevated caps the level at LOW no
matter how extreme the one signal that IS elevated looks.
"""
from __future__ import annotations

from typing import Sequence

from causeway.prediction import trends
from causeway.prediction.base import MIN_SAMPLES, Detector
from causeway.prediction.schema import HIGH, LOW, MEDIUM, NoAssessment, RiskAssessment
from causeway.telemetry.schema import TelemetrySample

ID = "connection_pool_exhaustion"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _utilization_points(samples: Sequence[TelemetrySample]):
    points = []
    for s in samples:
        used, capacity = s.values.get("db_pool_used"), s.values.get("db_pool_capacity")
        if used is None or capacity is None or capacity <= 0:
            continue
        points.append((s.timestamp, (used / capacity) * 100.0))
    return points


class ConnectionPoolDetector(Detector):
    id = ID
    display_name = "Connection pool exhaustion"

    def evaluate(self, service: str, samples: Sequence[TelemetrySample]):
        util_points = _utilization_points(samples)
        if len(util_points) < MIN_SAMPLES:
            return NoAssessment(service, ID,
                                "fewer than %d samples with db_pool_used/db_pool_capacity"
                                % MIN_SAMPLES)

        waiting_points = trends.points_for(samples, "db_waiting_requests")
        p95_points = trends.points_for(samples, "p95_ms")
        error_points = trends.points_for(samples, "error_rate")

        util_now = util_points[-1][1]
        util_slope = trends.slope(util_points)
        util_rising = util_slope is not None and util_slope > 0
        util_component = _clamp((util_now - 50.0) / 45.0)
        if not util_rising:
            util_component *= 0.4

        waiting_now = waiting_points[-1][1] if waiting_points else None
        waiting_slope = trends.slope(waiting_points) if waiting_points else None
        waiting_rising = waiting_slope is not None and waiting_slope > 0
        waiting_component = _clamp((waiting_now or 0.0) / 30.0)
        if not waiting_rising:
            waiting_component *= 0.4

        third = max(1, len(p95_points) // 3)
        baseline_p95 = [v for _, v in p95_points[:third]]
        recent_p95 = [v for _, v in p95_points[-third:]]
        latency_ratio = trends.baseline_ratio(recent_p95, baseline_p95)
        latency_component = _clamp(((latency_ratio or 1.0) - 1.0) / 3.0)

        error_now = error_points[-1][1] if error_points else 0.0
        error_component = _clamp(error_now / 0.10)

        components = {
            "utilization": util_component, "waiting": waiting_component,
            "latency": latency_component, "error": error_component,
        }
        elevated = [name for name, value in components.items() if value >= 0.3]

        score = (0.40 * util_component + 0.30 * waiting_component
                + 0.20 * latency_component + 0.10 * error_component)

        if len(elevated) < 2:
            level = LOW
        elif score >= 0.75:
            level = HIGH
        elif score >= 0.40:
            level = MEDIUM
        else:
            level = LOW

        evidence = ["db pool utilization at %.0f%%%s"
                   % (util_now, " and rising" if util_rising else "")]
        if waiting_now is not None:
            evidence.append("%d request(s) waiting for a connection%s"
                            % (waiting_now, " and growing" if waiting_rising else ""))
        if latency_ratio is not None:
            evidence.append("p95 latency %.1fx its recent baseline" % latency_ratio)
        if error_now:
            evidence.append("error rate at %.1f%%" % (error_now * 100))

        current_values = {"db_pool_utilization_percent": round(util_now, 1)}
        if waiting_now is not None:
            current_values["db_waiting_requests"] = waiting_now
        if p95_points:
            current_values["p95_ms"] = p95_points[-1][1]
        if error_points:
            current_values["error_rate"] = error_points[-1][1]

        trend_values = {"db_pool_utilization_percent_per_s": round(util_slope, 4)} \
            if util_slope is not None else {}
        if waiting_slope is not None:
            trend_values["db_waiting_requests_per_s"] = round(waiting_slope, 4)

        eta = trends.eta_seconds(util_now, 100.0, util_slope) if level != LOW else None

        return RiskAssessment(
            service=service, detector=ID, level=level, score=round(score, 3),
            predicted_failure="connection pool exhaustion",
            evidence=tuple(evidence), current_values=current_values,
            trends=trend_values, eta_seconds=eta, sample_count=len(util_points),
        )
