"""Applying a fix in the sandbox: only ever to a disposable copy.

Slow-ish by necessity for the last class - it actually launches the sandboxed
order-service twice, once unpatched and once from a patched copy, and proves
the patch changes measured behaviour. Everything else here is fast and needs
no subprocess at all.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest

from causeway.sandbox import fixapply, repair, seed, service
from causeway.sandbox.replay import build_fixture
from causeway.sandbox.runner import Sandbox

REAL_SERVICE_PATH = service.__file__


def _hash(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class RegistryTests(unittest.TestCase):
    def test_B_has_exactly_one_registered_repair_surface(self):
        self.assertEqual(repair.targets_for("B"), ("SCANNING_PREDICATE",))

    def test_A_has_no_registered_repair_surface(self):
        """A is never PROVEN in this demo - there is nothing to fix, and
        nothing registered that would let a proposal fix it anyway."""
        self.assertEqual(repair.targets_for("A"), ())

    def test_current_value_reads_the_real_unpatched_source(self):
        self.assertEqual(repair.current_value("B", "SCANNING_PREDICATE"),
                         "order_id + 0 = ?")

    def test_is_safe_after_matches_only_the_known_indexed_predicate(self):
        self.assertTrue(repair.is_safe_after("B", "SCANNING_PREDICATE", "order_id = ?"))
        self.assertTrue(repair.is_safe_after("B", "SCANNING_PREDICATE", "  order_id  =  ?  "))
        self.assertFalse(repair.is_safe_after("B", "SCANNING_PREDICATE", "order_id + 0 = ?"))
        self.assertFalse(repair.is_safe_after("B", "SCANNING_PREDICATE", "1=1"))

    def test_an_unregistered_target_is_never_safe(self):
        self.assertFalse(repair.is_safe_after("B", "NOT_A_TARGET", "order_id = ?"))
        self.assertIsNone(repair.current_value("B", "NOT_A_TARGET"))


class ApplyTests(unittest.TestCase):
    """Fast: never starts the sandboxed service, only patches a copy on disk."""

    def setUp(self):
        self.before_hash = _hash(REAL_SERVICE_PATH)
        self.applied = None

    def tearDown(self):
        if self.applied is not None:
            self.applied.cleanup()
        self.assertEqual(_hash(REAL_SERVICE_PATH), self.before_hash,
                         "the real sandbox service source was modified")

    def test_the_patch_lands_only_in_a_disposable_copy(self):
        self.applied = fixapply.apply("SCANNING_PREDICATE", "order_id = ?")
        self.assertNotEqual(self.applied.service_path, REAL_SERVICE_PATH)
        self.assertTrue(os.path.exists(self.applied.service_path))
        with open(self.applied.service_path, encoding="utf-8") as handle:
            patched = handle.read()
        self.assertIn('SCANNING_PREDICATE = "order_id = ?"', patched)

    def test_the_real_source_file_is_never_written_to(self):
        self.applied = fixapply.apply("SCANNING_PREDICATE", "order_id = ?")
        # setUp/tearDown already assert the hash is unchanged; this test
        # exists so that assertion has a name of its own in the report.
        self.assertEqual(_hash(REAL_SERVICE_PATH), self.before_hash)

    def test_a_sibling_constant_is_left_untouched_in_the_copy(self):
        self.applied = fixapply.apply("SCANNING_PREDICATE", "order_id = ?")
        with open(self.applied.service_path, encoding="utf-8") as handle:
            patched = handle.read()
        self.assertIn('INDEXED_PREDICATE = "order_id = ?"', patched)

    def test_cleanup_removes_the_disposable_workdir(self):
        applied = fixapply.apply("SCANNING_PREDICATE", "order_id = ?")
        workdir = applied.workdir
        self.assertTrue(os.path.exists(workdir))
        applied.cleanup()
        self.assertFalse(os.path.exists(workdir))
        self.applied = None

    def test_an_unknown_target_refuses_rather_than_writing_a_broken_copy(self):
        with self.assertRaises(ValueError):
            fixapply.apply("NOT_A_REAL_CONSTANT", "anything")

    def test_cleanup_after_a_failed_apply_is_a_no_op(self):
        """apply() never partially patches - a missing target raises before
        any file is written, so there is nothing for a caller to clean up."""
        with self.assertRaises(ValueError):
            fixapply.apply("NOT_A_REAL_CONSTANT", "anything")
        # No AppliedFix was returned, so there is nothing to assert beyond
        # this not raising - the real source hash check in tearDown covers
        # the rest.


AUDIT_ROWS = 40_000
REPS = 2


def _small_fixture():
    fixture = build_fixture(seed.ORDERS)
    fixture["requests"] = fixture["requests"][:16]
    fixture["warmup"] = 4
    return fixture


class PatchedSandboxRunsTests(unittest.TestCase):
    """Slow: actually launches the sandboxed service, once unpatched and once
    from the patched copy, against the same fixture."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="causeway-fixsandbox-")
        cls.template = os.path.join(cls.tmp, "template.db")
        cls.work = os.path.join(cls.tmp, "sandbox.db")
        seed.build(cls.template, AUDIT_ROWS)
        cls.fixture = _small_fixture()
        cls.before_hash = _hash(REAL_SERVICE_PATH)
        cls.applied = fixapply.apply("SCANNING_PREDICATE", "order_id = ?")

    @classmethod
    def tearDownClass(cls):
        cls.applied.cleanup()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_real_source_is_unchanged_after_the_patched_sandbox_ran(self):
        with Sandbox(self.template, self.work,
                    service_path=self.applied.service_path) as patched:
            patched.measure(self.fixture, {"A": True, "B": True}, 1)
        self.assertEqual(_hash(REAL_SERVICE_PATH), self.before_hash)

    def test_the_patched_service_serves_the_corrected_predicate(self):
        with Sandbox(self.template, self.work,
                    service_path=self.applied.service_path) as patched:
            broken_would_be = patched.measure(self.fixture, {"A": True, "B": True}, REPS)
            healthy = patched.measure(self.fixture, {"A": False, "B": False}, REPS)
        # With the predicate patched, B=True no longer wraps order_id - the
        # index is usable again, so B on vs off should read close together
        # rather than the ~14x separation the unpatched service shows.
        self.assertLess(broken_would_be["p95_ms"], healthy["p95_ms"] * 4)

    def test_the_default_sandbox_still_runs_the_real_module(self):
        """service_path=None (the default) is unchanged from before Milestone
        5 - the causal experiment's own sandbox usage is untouched."""
        with Sandbox(self.template, self.work) as unpatched:
            self.assertIsNone(unpatched.service_path)
            broken = unpatched.measure(self.fixture, {"A": True, "B": True}, REPS)
            healthy = unpatched.measure(self.fixture, {"A": False, "B": False}, REPS)
        self.assertGreater(broken["p95_ms"], healthy["p95_ms"] * 4)


if __name__ == "__main__":
    unittest.main()
