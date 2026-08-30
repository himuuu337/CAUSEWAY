"""causeway.prediction: the trend math, each detector, and the engine's
hysteresis - the deterministic risk engine, with no telemetry HTTP surface
involved at all.
"""
from __future__ import annotations

import unittest

from causeway.prediction import trends
from causeway.prediction.connection_pool import ConnectionPoolDetector
from causeway.prediction.engine import PredictionEngine
from causeway.prediction.latency_degradation import LatencyDegradationDetector
from causeway.prediction.memory_pressure import MemoryPressureDetector
from causeway.prediction.schema import HIGH, LOW, MEDIUM, NoAssessment
from causeway.telemetry.schema import TelemetrySample
from causeway.telemetry.store import TelemetryStore

T0 = 1_700_000_000.0


def _samples(service: str, fields_by_step, start: float = T0, step: float = 2.0):
    """fields_by_step: list of dicts, one per sample."""
    out = []
    for i, fields in enumerate(fields_by_step):
        out.append(TelemetrySample(service=service, timestamp=start + i * step,
                                   values={k: float(v) for k, v in fields.items()}))
    return out


# ------------------------------------------------------------------- trends

class TrendsTests(unittest.TestCase):
    def test_slope_of_a_single_point_is_none(self):
        self.assertIsNone(trends.slope([(0.0, 1.0)]))

    def test_slope_of_no_points_is_none(self):
        self.assertIsNone(trends.slope([]))

    def test_slope_with_duplicate_timestamps_is_none_not_a_crash(self):
        self.assertIsNone(trends.slope([(1.0, 5.0), (1.0, 9.0)]))

    def test_slope_of_a_flat_line_is_zero(self):
        points = [(0.0, 10.0), (1.0, 10.0), (2.0, 10.0)]
        self.assertAlmostEqual(trends.slope(points), 0.0)

    def test_slope_of_a_rising_line(self):
        points = [(0.0, 0.0), (1.0, 2.0), (2.0, 4.0)]
        self.assertAlmostEqual(trends.slope(points), 2.0)

    def test_slope_of_a_falling_line_is_negative(self):
        points = [(0.0, 10.0), (1.0, 5.0), (2.0, 0.0)]
        self.assertAlmostEqual(trends.slope(points), -5.0)

    def test_median_handles_even_and_odd_counts(self):
        self.assertEqual(trends.median([1.0, 2.0, 3.0]), 2.0)
        self.assertEqual(trends.median([1.0, 2.0, 3.0, 4.0]), 2.5)

    def test_median_of_empty_is_none(self):
        self.assertIsNone(trends.median([]))

    def test_baseline_ratio_against_zero_baseline_is_none(self):
        self.assertIsNone(trends.baseline_ratio([10.0], [0.0]))

    def test_baseline_ratio_with_an_empty_window_is_none(self):
        self.assertIsNone(trends.baseline_ratio([], [1.0]))

    def test_persistence_counts_only_the_trailing_run(self):
        self.assertEqual(trends.persistence([True, False, True, True, True]), 3)
        self.assertEqual(trends.persistence([False, False]), 0)
        self.assertEqual(trends.persistence([]), 0)
        self.assertEqual(trends.persistence([True, True]), 2)

    def test_eta_with_no_rate_is_none(self):
        self.assertIsNone(trends.eta_seconds(50.0, 100.0, None))

    def test_eta_with_a_falling_rate_is_none(self):
        self.assertIsNone(trends.eta_seconds(50.0, 100.0, -1.0))

    def test_eta_with_zero_rate_is_none(self):
        self.assertIsNone(trends.eta_seconds(50.0, 100.0, 0.0))

    def test_eta_past_the_target_is_zero(self):
        self.assertEqual(trends.eta_seconds(120.0, 100.0, 1.0), 0.0)

    def test_eta_is_computed_correctly(self):
        self.assertAlmostEqual(trends.eta_seconds(50.0, 100.0, 5.0), 10.0)


# ------------------------------------------------------- connection pool detector

class ConnectionPoolDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = ConnectionPoolDetector()

    def test_too_few_samples_is_no_assessment_not_a_crash(self):
        samples = _samples("s", [{"db_pool_used": 5, "db_pool_capacity": 10}])
        result = self.detector.evaluate("s", samples)
        self.assertIsInstance(result, NoAssessment)

    def test_stable_healthy_telemetry_is_low(self):
        steps = [{"db_pool_used": 5, "db_pool_capacity": 20, "db_waiting_requests": 0,
                 "p95_ms": 90 + (i % 3)} for i in range(8)]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertEqual(result.level, LOW)

    def test_a_single_spike_alone_does_not_reach_high(self):
        """One sample of extreme utilization, surrounded by healthy ones -
        not enough history for a real rising trend, and not enough
        concurrent signals either."""
        steps = [{"db_pool_used": 5, "db_pool_capacity": 20, "p95_ms": 90},
                 {"db_pool_used": 5, "db_pool_capacity": 20, "p95_ms": 92},
                 {"db_pool_used": 19, "db_pool_capacity": 20, "p95_ms": 95}]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertIn(result.level, (LOW, MEDIUM))
        self.assertNotEqual(result.level, HIGH)

    def test_sustained_multi_signal_growth_is_high(self):
        pool = [55, 64, 73, 82, 90, 96]
        waiting = [0, 3, 8, 15, 27, 43]
        p95 = [100, 130, 180, 270, 450, 760]
        steps = [{"db_pool_used": pool[i], "db_pool_capacity": 100,
                 "db_waiting_requests": waiting[i], "p95_ms": p95[i]}
                for i in range(6)]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertEqual(result.level, HIGH)
        self.assertGreaterEqual(len(result.evidence), 2)

    def test_high_utilization_alone_without_waiting_or_latency_stays_below_high(self):
        """The 'no HIGH from one spike' rule, stated as: fewer than two
        elevated signals caps the level, even if utilization itself is
        already severe."""
        steps = [{"db_pool_used": 90 + i, "db_pool_capacity": 100} for i in range(6)]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertNotEqual(result.level, HIGH)

    def test_eta_is_present_when_rising_and_absent_when_healthy(self):
        pool = [55, 64, 73, 82, 90, 96]
        waiting = [0, 3, 8, 15, 27, 43]
        p95 = [100, 130, 180, 270, 450, 760]
        steps = [{"db_pool_used": pool[i], "db_pool_capacity": 100,
                 "db_waiting_requests": waiting[i], "p95_ms": p95[i]}
                for i in range(6)]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertIsNotNone(result.eta_seconds)
        self.assertGreater(result.eta_seconds, 0)

        healthy_steps = [{"db_pool_used": 5, "db_pool_capacity": 20} for _ in range(6)]
        healthy = self.detector.evaluate("s", _samples("s", healthy_steps))
        self.assertIsNone(healthy.eta_seconds)

    def test_a_flat_trend_has_no_eta(self):
        steps = [{"db_pool_used": 50, "db_pool_capacity": 100,
                 "db_waiting_requests": 10, "p95_ms": 200} for _ in range(6)]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertIsNone(result.eta_seconds)

    def test_a_falling_trend_has_no_eta(self):
        pool = [96, 90, 82, 73, 64, 55]
        steps = [{"db_pool_used": v, "db_pool_capacity": 100} for v in pool]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertIsNone(result.eta_seconds)


# ---------------------------------------------------------------- memory pressure

class MemoryPressureDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = MemoryPressureDetector()

    def test_stable_memory_does_not_trigger(self):
        steps = [{"memory_percent": 50 + (i % 2)} for i in range(8)]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertEqual(result.level, LOW)

    def test_persistent_rising_memory_with_stable_workload_is_risky(self):
        steps = [{"memory_percent": 60 + i * 4, "request_rate": 100} for i in range(8)]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertIn(result.level, (MEDIUM, HIGH))
        self.assertGreater(result.score, 0.3)

    def test_falling_memory_is_low_regardless_of_level(self):
        steps = [{"memory_percent": 90 - i * 5} for i in range(8)]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertEqual(result.level, LOW)


# --------------------------------------------------------------- latency/error

class LatencyDegradationDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = LatencyDegradationDetector()

    def test_a_single_slow_request_amid_a_stable_baseline_does_not_incident(self):
        steps = [{"p95_ms": 20} for _ in range(5)] + [{"p95_ms": 22}]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertEqual(result.level, LOW)

    def test_sustained_baseline_degradation_is_flagged(self):
        steps = [{"p95_ms": 20}, {"p95_ms": 20}, {"p95_ms": 90}, {"p95_ms": 95},
                {"p95_ms": 100}, {"p95_ms": 100}]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertIn(result.level, (MEDIUM, HIGH))

    def test_error_rate_alone_can_also_trigger(self):
        steps = [{"error_rate": 0.01}, {"error_rate": 0.01}, {"error_rate": 0.15},
                {"error_rate": 0.2}]
        result = self.detector.evaluate("s", _samples("s", steps))
        self.assertNotEqual(result.level, LOW)


# ---------------------------------------------------------------- the engine

class HysteresisTests(unittest.TestCase):
    def _dangerous_step(self, i):
        pool = [55, 64, 73, 82, 90, 96, 97, 98, 99]
        waiting = [0, 3, 8, 15, 27, 43, 50, 55, 60]
        p95 = [100, 130, 180, 270, 450, 760, 800, 820, 830]
        idx = min(i, len(pool) - 1)
        return {"db_pool_used": pool[idx], "db_pool_capacity": 100,
               "db_waiting_requests": waiting[idx], "p95_ms": p95[idx]}

    def test_a_single_high_evaluation_is_not_confirmed(self):
        store = TelemetryStore()
        engine = PredictionEngine(store, detectors=(ConnectionPoolDetector(),))
        for i in range(5):
            store.append(TelemetrySample(service="s", timestamp=T0 + i * 2,
                                         values={k: float(v) for k, v in
                                                self._dangerous_step(i).items()}))
        results = engine.evaluate("s")
        pool_result = next(r for r in results if r.detector == "connection_pool_exhaustion")
        # by sample index 4 the level should already be HIGH, but only one
        # or two consecutive HIGH evaluations have happened - not enough.
        self.assertFalse(pool_result.confirmed)

    def test_three_consecutive_high_evaluations_confirm(self):
        store = TelemetryStore()
        engine = PredictionEngine(store, detectors=(ConnectionPoolDetector(),))
        confirmed_at = None
        for i in range(9):
            store.append(TelemetrySample(service="s", timestamp=T0 + i * 2,
                                         values={k: float(v) for k, v in
                                                self._dangerous_step(i).items()}))
            results = engine.evaluate("s")
            pool_result = next((r for r in results if r.detector == "connection_pool_exhaustion"),
                              None)
            if pool_result is not None and pool_result.confirmed and confirmed_at is None:
                confirmed_at = i
        self.assertIsNotNone(confirmed_at, "the sustained HIGH run never confirmed")

    def test_recovery_requires_persistence_then_a_new_incident_can_confirm_again(self):
        store = TelemetryStore()
        detector = ConnectionPoolDetector()
        # A small recent_window on purpose: this test packs two separate
        # incident episodes into a handful of synthetic samples, and a
        # detector that (correctly, for a real deployment) looks back
        # further than that would still see the first episode's already-
        # elevated baseline while judging the second one. In production,
        # telemetry arrives every couple of seconds and the default window
        # covers under a minute - two real episodes are naturally further
        # apart in the window than this compressed test can afford to be.
        engine = PredictionEngine(store, detectors=(detector,), confirm_after=2,
                                  recover_after=2, recent_window=6)

        def push(i, used, waiting, p95):
            store.append(TelemetrySample(service="s", timestamp=T0 + i * 2, values={
                "db_pool_used": float(used), "db_pool_capacity": 100.0,
                "db_waiting_requests": float(waiting), "p95_ms": float(p95)}))
            results = engine.evaluate("s")
            return next((r for r in results if r.detector == "connection_pool_exhaustion"),
                       None)

        i = 0
        for used, waiting, p95 in [(55, 0, 100), (82, 15, 270), (96, 43, 760), (97, 50, 800)]:
            result = push(i, used, waiting, p95)
            i += 1
        self.assertTrue(result.confirmed)

        # sustained recovery
        for _ in range(3):
            result = push(i, 20, 0, 90)
            i += 1
        self.assertFalse(result.confirmed)

        # a brand new episode can confirm again - two consecutive HIGH
        # evaluations are needed again, exactly as the first episode needed
        for used, waiting, p95 in [(70, 8, 180), (82, 15, 270), (96, 43, 760),
                                   (97, 50, 800), (98, 55, 810)]:
            result = push(i, used, waiting, p95)
            i += 1
        self.assertTrue(result.confirmed)

    def test_repeated_high_does_not_reconfirm_every_sample(self):
        """confirmed stays true across a sustained incident - the engine
        itself makes no claim about how many TIMES it has been confirmed;
        that de-duplication is causeway.incidents' job (see
        tests/test_incidents.py), which this test does not duplicate."""
        store = TelemetryStore()
        engine = PredictionEngine(store, detectors=(ConnectionPoolDetector(),),
                                  confirm_after=2, recover_after=2)
        for i, (used, waiting, p95) in enumerate(
                [(82, 15, 270), (96, 43, 760), (97, 50, 800), (98, 55, 810)]):
            store.append(TelemetrySample(service="s", timestamp=T0 + i * 2, values={
                "db_pool_used": float(used), "db_pool_capacity": 100.0,
                "db_waiting_requests": float(waiting), "p95_ms": float(p95)}))
            results = engine.evaluate("s")
        pool_result = next(r for r in results if r.detector == "connection_pool_exhaustion")
        self.assertTrue(pool_result.confirmed)


if __name__ == "__main__":
    unittest.main()
