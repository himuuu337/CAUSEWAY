"""Deterministic failure-risk prediction from telemetry.

No machine learning, no model call: "prediction" here means a detector
reads a bounded, deterministic window of recent samples and reports
whether the signals it looks at are showing sustained movement toward a
named, specific failure condition - never a general claim about crashes
this prototype cannot see coming. See causeway.prediction.base for the
Detector contract and causeway.prediction.engine for the hysteresis that
turns a raw per-sample opinion into a `confirmed` one.
"""
from __future__ import annotations

from causeway.prediction.base import MIN_SAMPLES, Detector
from causeway.prediction.engine import (CONFIRM_AFTER, RECENT_WINDOW, RECOVER_AFTER,
                                        PredictionEngine, engine)
from causeway.prediction.registry import DETECTORS
from causeway.prediction.schema import (HIGH, LEVELS, LOW, MEDIUM, NoAssessment,
                                        RiskAssessment)

__all__ = ["Detector", "MIN_SAMPLES", "PredictionEngine", "engine", "CONFIRM_AFTER",
          "RECOVER_AFTER", "RECENT_WINDOW", "DETECTORS", "RiskAssessment", "NoAssessment",
          "LOW", "MEDIUM", "HIGH", "LEVELS"]
