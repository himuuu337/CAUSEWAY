"""Telemetry ingestion: what a running service reports about itself.

This package has one job - accept a sample, validate it, and remember a
bounded rolling history per service. It computes nothing about risk; that is
causeway.prediction's job, reading the same store. Keeping the two separate
is what lets the prediction engine be tested against synthetic histories
without a live service, and what keeps this module simple enough to audit:
every field is a number or a short string, nothing here is ever executed.
"""
from __future__ import annotations

from causeway.telemetry.schema import (REQUIRED_FIELDS, TelemetryRejected,
                                       TelemetrySample, validate_sample)
from causeway.telemetry.store import TelemetryStore, store

__all__ = ["TelemetrySample", "TelemetryRejected", "REQUIRED_FIELDS",
          "validate_sample", "TelemetryStore", "store"]
