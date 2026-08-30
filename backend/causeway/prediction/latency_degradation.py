"""Detector 3: sustained latency / error degradation.

The generic fallback: when nothing more specific (the pool detector, the
memory detector) explains what is happening, this one still catches a
service that is simply getting slower and erroring more than its own
recent baseline - comparing the tail of the window against its own head
rather than any hardcoded millisecond figure, so it means the same thing
for a service whose healthy p95 is 20ms as one whose healthy p95 is 400ms.
"""
from __future__ import annotations

from typing import Sequence

from causeway.prediction import trends
from causeway.prediction.base import MIN_SAMPLES, Detector
from causeway.prediction.schema import HIGH, LOW, MEDIUM, NoAssessment, RiskAssessment
from causeway.telemetry.schema import TelemetrySample

ID = "latency_degradation"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _window_ratio(points):
    third = max(1, len(points) // 3)
    baseline = [v for _, v in points[:third]]
    recent = [v for _, v in points[-third:]]
    return trends.baseline_ratio(recent, baseline)


class LatencyDegradationDetector(Detector):
    id = ID
    display_name = "Latency / error degradation"

    def evaluate(self, service: str, samples: Sequence[TelemetrySample]):
        p95_points = trends.points_for(samples, "p95_ms")
        error_points = trends.points_for(samples, "error_rate")
        if len(p95_points) < MIN_SAMPLES and len(error_points) < MIN_SAMPLES:
            return NoAssessment(service, ID,
                                "fewer than %d samples with p95_ms or error_rate"
                                % MIN_SAMPLES)

        latency_ratio = _window_ratio(p95_points) if len(p95_points) >= MIN_SAMPLES else None
        latency_component = _clamp(((latency_ratio or 1.0) - 1.0) / 3.0)

        error_now = error_points[-1][1] if error_points else None
        error_baseline_ratio = _window_ratio(error_points) if len(error_points) >= MIN_SAMPLES \
            else None
        error_component = _clamp((error_now or 0.0) / 0.10)

        # Weighted evenly enough that either signal can reach MEDIUM on its
        # own - a service that is erroring heavily with unchanged latency
        # is still a real degradation, not something only a latency rise
        # is allowed to report.
        score = 0.5 * latency_component + 0.5 * error_component
        signal_present = latency_component >= 0.3 or error_component >= 0.3

        if not signal_present:
            level = LOW
        elif score >= 0.75:
            level = HIGH
        elif score >= 0.40:
            level = MEDIUM
        else:
            level = LOW

        evidence = []
        if latency_ratio is not None:
            evidence.append("p95 latency %.1fx its recent baseline" % latency_ratio)
        if error_now is not None:
            evidence.append("error rate at %.1f%%%s" % (
                error_now * 100,
                " (rising)" if error_baseline_ratio and error_baseline_ratio > 1.2 else ""))
        if not evidence:
            evidence.append("latency and error rate both within their recent baseline")

        current_values = {}
        if p95_points:
            current_values["p95_ms"] = p95_points[-1][1]
        if error_now is not None:
            current_values["error_rate"] = error_now

        return RiskAssessment(
            service=service, detector=ID, level=level, score=round(score, 3),
            predicted_failure="latency/error degradation",
            evidence=tuple(evidence), current_values=current_values,
            trends={}, eta_seconds=None,
            sample_count=max(len(p95_points), len(error_points)),
        )
