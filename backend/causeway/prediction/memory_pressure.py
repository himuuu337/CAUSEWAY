"""Detector 2: memory pressure / possible exhaustion.

This detector never claims a leak is proven - "leak" is a source-level
explanation, and all this module has is a number climbing over time. What
it reports is the observable pattern: memory rising steadily while the
workload driving it (request_rate, when available) is not, which is the
external symptom a leak, an unbounded cache, or a slow release path would
all produce identically. Causeway's causal investigation is what would
distinguish between those, not this detector.
"""
from __future__ import annotations

from typing import Sequence

from causeway.prediction import trends
from causeway.prediction.base import MIN_SAMPLES, Detector
from causeway.prediction.schema import HIGH, LOW, MEDIUM, NoAssessment, RiskAssessment
from causeway.telemetry.schema import TelemetrySample

ID = "memory_pressure"
DANGEROUS_THRESHOLD_PERCENT = 95.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class MemoryPressureDetector(Detector):
    id = ID
    display_name = "Memory pressure"

    def evaluate(self, service: str, samples: Sequence[TelemetrySample]):
        mem_points = trends.points_for(samples, "memory_percent")
        if len(mem_points) < MIN_SAMPLES:
            return NoAssessment(service, ID,
                                "fewer than %d samples with memory_percent" % MIN_SAMPLES)

        mem_now = mem_points[-1][1]
        mem_slope = trends.slope(mem_points)
        rising = mem_slope is not None and mem_slope > 0

        rate_points = trends.points_for(samples, "request_rate")
        workload_stable = None
        if len(rate_points) >= 2:
            rate_slope = trends.slope(rate_points)
            baseline_rate = max(trends.median([v for _, v in rate_points]) or 0.0, 1.0)
            workload_stable = (rate_slope is not None
                              and abs(rate_slope) < 0.05 * baseline_rate)

        base_component = _clamp((mem_now - 60.0) / 35.0)
        if not rising:
            base_component *= 0.3
        stability_bonus = 0.15 if (rising and workload_stable) else 0.0
        score = _clamp(base_component + stability_bonus)

        if not rising:
            level = LOW
        elif score >= 0.75:
            level = HIGH
        elif score >= 0.40:
            level = MEDIUM
        else:
            level = LOW

        evidence = ["memory at %.0f%%%s"
                   % (mem_now, " and rising" if rising else " and stable or falling")]
        if workload_stable is True:
            evidence.append("request rate is stable, so the rise is not just more traffic")
        elif workload_stable is False:
            evidence.append("request rate is also moving, which may explain some of the rise")

        eta = trends.eta_seconds(mem_now, DANGEROUS_THRESHOLD_PERCENT, mem_slope) \
            if level != LOW else None

        return RiskAssessment(
            service=service, detector=ID, level=level, score=round(score, 3),
            predicted_failure="memory exhaustion risk",
            evidence=tuple(evidence),
            current_values={"memory_percent": round(mem_now, 1)},
            trends={"memory_percent_per_s": round(mem_slope, 5)} if mem_slope else {},
            eta_seconds=eta, sample_count=len(mem_points),
        )
