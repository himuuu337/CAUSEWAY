"""causeway.telemetry: validation and the bounded, per-service rolling
store. No FastAPI here - see tests/test_api_monitoring.py for the HTTP
surface.
"""
from __future__ import annotations

import time
import unittest

from causeway.telemetry.schema import TelemetryRejected, validate_sample
from causeway.telemetry.store import TelemetryStore


class ValidationTests(unittest.TestCase):
    def test_a_well_formed_sample_is_accepted(self):
        sample = validate_sample({"service": "order-service", "cpu_percent": 42.0,
                                  "p95_ms": 120.5}, now=1000.0)
        self.assertEqual(sample.service, "order-service")
        self.assertEqual(sample.values["cpu_percent"], 42.0)
        self.assertEqual(sample.values["p95_ms"], 120.5)

    def test_a_non_object_payload_is_rejected(self):
        with self.assertRaises(TelemetryRejected):
            validate_sample([1, 2, 3], now=1000.0)

    def test_a_missing_service_is_rejected(self):
        with self.assertRaises(TelemetryRejected):
            validate_sample({"cpu_percent": 1.0}, now=1000.0)

    def test_an_invalid_service_name_is_rejected(self):
        for bad in ("", "has spaces", "semi;colon", "a" * 100):
            with self.subTest(bad=bad):
                with self.assertRaises(TelemetryRejected):
                    validate_sample({"service": bad}, now=1000.0)

    def test_an_unrecognised_field_is_rejected(self):
        with self.assertRaises(TelemetryRejected):
            validate_sample({"service": "s", "shell_command": "rm -rf /"}, now=1000.0)

    def test_a_non_numeric_value_is_rejected(self):
        with self.assertRaises(TelemetryRejected):
            validate_sample({"service": "s", "cpu_percent": "high"}, now=1000.0)

    def test_a_boolean_is_not_accepted_as_a_number(self):
        with self.assertRaises(TelemetryRejected):
            validate_sample({"service": "s", "cpu_percent": True}, now=1000.0)

    def test_nan_and_infinity_are_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(TelemetryRejected):
                    validate_sample({"service": "s", "cpu_percent": bad}, now=1000.0)

    def test_a_value_outside_its_plausible_bound_is_rejected(self):
        with self.assertRaises(TelemetryRejected):
            validate_sample({"service": "s", "cpu_percent": 250.0}, now=1000.0)
        with self.assertRaises(TelemetryRejected):
            validate_sample({"service": "s", "error_rate": 5.0}, now=1000.0)

    def test_an_absent_timestamp_defaults_to_now(self):
        sample = validate_sample({"service": "s"}, now=1234.5)
        self.assertEqual(sample.timestamp, 1234.5)

    def test_an_iso8601_timestamp_string_is_accepted(self):
        sample = validate_sample(
            {"service": "s", "timestamp": "2026-08-30T10:00:00Z"}, now=0.0)
        self.assertGreater(sample.timestamp, 0.0)

    def test_a_malformed_timestamp_is_rejected(self):
        with self.assertRaises(TelemetryRejected):
            validate_sample({"service": "s", "timestamp": "not a date"}, now=0.0)

    def test_the_payload_can_never_carry_executable_content(self):
        """Every accepted field is one of a closed, numeric allow-list -
        there is no field whose value is ever a string long enough to be a
        command, a script, or a template that gets evaluated."""
        from causeway.telemetry.schema import NUMERIC_FIELDS
        for field in NUMERIC_FIELDS:
            self.assertIsInstance(field, str)
        sample = validate_sample({"service": "s", "cpu_percent": 1.0}, now=0.0)
        for value in sample.values.values():
            self.assertIsInstance(value, float)


class StoreTests(unittest.TestCase):
    def test_append_and_latest(self):
        store = TelemetryStore()
        sample = validate_sample({"service": "a", "cpu_percent": 1.0}, now=1.0)
        store.append(sample)
        self.assertEqual(store.latest("a").values["cpu_percent"], 1.0)

    def test_an_unknown_service_has_no_latest(self):
        store = TelemetryStore()
        self.assertIsNone(store.latest("nobody"))

    def test_multiple_services_are_isolated(self):
        store = TelemetryStore()
        store.append(validate_sample({"service": "a", "cpu_percent": 1.0}, now=1.0))
        store.append(validate_sample({"service": "b", "cpu_percent": 99.0}, now=1.0))
        self.assertEqual(store.latest("a").values["cpu_percent"], 1.0)
        self.assertEqual(store.latest("b").values["cpu_percent"], 99.0)
        self.assertEqual(sorted(store.services()), ["a", "b"])

    def test_the_window_is_bounded(self):
        store = TelemetryStore(max_samples=5)
        for i in range(20):
            store.append(validate_sample({"service": "a", "cpu_percent": float(i)}, now=float(i)))
        self.assertEqual(store.sample_count("a"), 5)
        recent = store.recent("a")
        self.assertEqual([s.values["cpu_percent"] for s in recent], [15.0, 16.0, 17.0, 18.0, 19.0])

    def test_out_of_order_samples_are_still_kept_not_reordered_or_dropped(self):
        store = TelemetryStore()
        store.append(validate_sample({"service": "a", "cpu_percent": 5.0}, now=5.0))
        store.append(validate_sample({"service": "a", "cpu_percent": 1.0}, now=1.0))
        self.assertEqual(store.sample_count("a"), 2)

    def test_reset_one_service_leaves_others_alone(self):
        store = TelemetryStore()
        store.append(validate_sample({"service": "a", "cpu_percent": 1.0}, now=1.0))
        store.append(validate_sample({"service": "b", "cpu_percent": 1.0}, now=1.0))
        store.reset("a")
        self.assertIsNone(store.latest("a"))
        self.assertIsNotNone(store.latest("b"))

    def test_reset_all(self):
        store = TelemetryStore()
        store.append(validate_sample({"service": "a", "cpu_percent": 1.0}, now=1.0))
        store.reset()
        self.assertEqual(store.services(), [])


if __name__ == "__main__":
    unittest.main()
