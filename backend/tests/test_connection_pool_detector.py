"""causeway.analysis.detectors_pool: a second real, deterministic detector
- read out of source, never told the answer, and mechanical enough that its
derived counterfactual always compiles.
"""
from __future__ import annotations

import unittest

from causeway.analysis import detectors_pool


def _compiles(source: str) -> bool:
    try:
        compile(source, "<test>", "exec")
        return True
    except SyntaxError:
        return False


class DetectionTests(unittest.TestCase):
    def test_acquire_then_release_as_sibling_statements_is_flagged(self):
        source = (
            "def handle_work(pool):\n"
            "    pool.acquire()\n"
            "    result = do_work()\n"
            "    pool.release()\n"
            "    return result\n"
        )
        findings = detectors_pool.scan_source("app.py", source)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.file, "app.py")
        self.assertEqual(finding.symbol, "handle_work")
        self.assertEqual(finding.detector, detectors_pool.NAME)
        self.assertTrue(finding.testable)

    def test_the_counterfactual_moves_release_into_a_finally_block(self):
        source = (
            "def handle_work(pool):\n"
            "    pool.acquire()\n"
            "    result = do_work()\n"
            "    pool.release()\n"
            "    return result\n"
        )
        finding = detectors_pool.scan_source("app.py", source)[0]
        self.assertIn("try:", finding.counterfactual)
        self.assertIn("finally:", finding.counterfactual)
        self.assertTrue(finding.counterfactual.strip().endswith("pool.release()"))

    def test_the_counterfactual_always_compiles_when_substituted(self):
        source = (
            "def handle_work(pool):\n"
            "    pool.acquire()\n"
            "    result = do_work()\n"
            "    status = summarize(result)\n"
            "    pool.release()\n"
            "    return status\n"
        )
        finding = detectors_pool.scan_source("app.py", source)[0]
        self.assertEqual(source.count(finding.observed), 1)
        patched = source.replace(finding.observed, finding.counterfactual, 1)
        self.assertTrue(_compiles(patched))

    def test_an_already_guaranteed_release_is_not_flagged(self):
        source = (
            "def handle_work(pool):\n"
            "    pool.acquire()\n"
            "    try:\n"
            "        result = do_work()\n"
            "    finally:\n"
            "        pool.release()\n"
            "    return result\n"
        )
        self.assertEqual(detectors_pool.scan_source("app.py", source), [])

    def test_a_release_immediately_after_acquire_has_nothing_to_leak(self):
        source = "def handle_work(pool):\n    pool.acquire()\n    pool.release()\n"
        self.assertEqual(detectors_pool.scan_source("app.py", source), [])

    def test_an_unrelated_release_call_does_not_confuse_the_owner_match(self):
        source = (
            "def handle_work(pool, other):\n"
            "    pool.acquire()\n"
            "    other.release()\n"
            "    pool.release()\n"
            "    return 1\n"
        )
        findings = detectors_pool.scan_source("app.py", source)
        self.assertEqual(len(findings), 1)
        self.assertIn("other.release()", findings[0].observed)

    def test_a_file_with_no_acquire_release_pattern_finds_nothing(self):
        source = "def add(a, b):\n    return a + b\n"
        self.assertEqual(detectors_pool.scan_source("app.py", source), [])

    def test_a_syntax_error_is_skipped_not_raised(self):
        # scan_repository (not scan_source) is the one that tolerates a
        # broken file, matching causeway.analysis.detectors' own contract.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "broken.py"), "w") as handle:
                handle.write("def broken(:\n")
            findings = detectors_pool.scan_repository(root, ["broken.py"])
        self.assertEqual(findings, [])

    def test_multiple_functions_each_get_their_own_finding(self):
        source = (
            "def a(pool):\n"
            "    pool.acquire()\n"
            "    work()\n"
            "    pool.release()\n"
            "\n"
            "def b(pool):\n"
            "    pool.acquire()\n"
            "    other_work()\n"
            "    pool.release()\n"
        )
        findings = detectors_pool.scan_source("app.py", source)
        self.assertEqual({f.symbol for f in findings}, {"a", "b"})


if __name__ == "__main__":
    unittest.main()
