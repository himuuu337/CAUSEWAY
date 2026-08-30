"""One telemetry sample, and the validation that stands between a POST body
and anything Causeway keeps.

A sample is data, never code: every field is a bounded number or a short
string, checked against an explicit allow-list of names and types before it
is stored. There is nothing here a payload could use to execute anything -
no eval, no exec, no format-string expansion of untrusted content, no
field whose value is ever passed to a shell or a query.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

# The only fields a sample may carry. Each is optional except `service` -
# a real exporter will not always have every number available, and Causeway
# must not invent the ones it lacks.
NUMERIC_FIELDS = (
    "cpu_percent", "memory_percent", "request_rate", "p50_ms", "p95_ms",
    "p99_ms", "error_rate", "db_pool_used", "db_pool_capacity",
    "db_waiting_requests", "db_query_p95_ms", "rate_limit_429_rate",
    "rate_limit_remaining",
)
REQUIRED_FIELDS = ("service",)

# Sane bounds per field - not a business rule, a sanity floor. A percent
# above 1000 or a latency below zero is a malformed sample, not a data
# point, and is rejected rather than silently clamped: clamping a bad
# number into range would let a broken exporter masquerade as a healthy
# one.
_BOUNDS = {
    "cpu_percent": (0.0, 100.0),
    "memory_percent": (0.0, 100.0),
    "request_rate": (0.0, 1_000_000.0),
    "p50_ms": (0.0, 3_600_000.0),
    "p95_ms": (0.0, 3_600_000.0),
    "p99_ms": (0.0, 3_600_000.0),
    "error_rate": (0.0, 1.0),
    "db_pool_used": (0.0, 1_000_000.0),
    "db_pool_capacity": (0.0, 1_000_000.0),
    "db_waiting_requests": (0.0, 1_000_000.0),
    "db_query_p95_ms": (0.0, 3_600_000.0),
    "rate_limit_429_rate": (0.0, 1.0),
    "rate_limit_remaining": (0.0, 1_000_000.0),
}

_SERVICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class TelemetryRejected(ValueError):
    """A telemetry payload failed validation. Nothing was stored."""


@dataclass(frozen=True)
class TelemetrySample:
    service: str
    timestamp: float                       # unix seconds, server-assigned if absent
    values: Mapping[str, float]             # only NUMERIC_FIELDS keys, only the ones present

    def get(self, field: str) -> Optional[float]:
        return self.values.get(field)

    def as_dict(self) -> dict:
        return {"service": self.service, "timestamp": self.timestamp, **self.values}


def _coerce_number(field: str, raw: Any) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TelemetryRejected("%s must be a number" % field)
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")):
        raise TelemetryRejected("%s must be a finite number" % field)
    low, high = _BOUNDS[field]
    if not (low <= value <= high):
        raise TelemetryRejected(
            "%s=%r is outside the plausible range [%s, %s]" % (field, raw, low, high))
    return value


def validate_sample(raw: Any, now: float) -> TelemetrySample:
    """Validate a POST /api/telemetry body. Raises TelemetryRejected with a
    human-readable reason; never raises anything else, and never stores a
    partially-validated sample."""
    if not isinstance(raw, dict):
        raise TelemetryRejected("telemetry payload must be a JSON object")

    service = raw.get("service")
    if not isinstance(service, str) or not _SERVICE_NAME.match(service):
        raise TelemetryRejected(
            "service must be a short identifier (letters, digits, '.', '_', '-')")

    timestamp = raw.get("timestamp", now)
    if isinstance(timestamp, str):
        # Accept an ISO-8601 string (what most exporters send) without
        # pulling in a dependency: only the one format Causeway itself
        # emits and the one the example in the spec uses.
        import datetime
        try:
            parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            timestamp = parsed.timestamp()
        except ValueError:
            raise TelemetryRejected("timestamp must be a number or an ISO-8601 string")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise TelemetryRejected("timestamp must be a number or an ISO-8601 string")
    timestamp = float(timestamp)

    values = {}
    for field in NUMERIC_FIELDS:
        if field not in raw or raw[field] is None:
            continue
        values[field] = _coerce_number(field, raw[field])

    unknown = set(raw) - set(NUMERIC_FIELDS) - {"service", "timestamp"}
    if unknown:
        raise TelemetryRejected("unrecognised field(s): %s" % ", ".join(sorted(unknown)))

    return TelemetrySample(service=service, timestamp=timestamp, values=values)
