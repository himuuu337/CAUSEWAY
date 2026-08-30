"""CodeHypothesis.category and .line_end: the engineering-insight labels
Feature 3 (evidence-backed source insight) adds on top of the existing
file/line/symbol/observed/counterfactual finding. Both are properties of
the hypothesis itself - never invented, never upgraded by anything
downstream - so they are exercised directly against the dataclass and
against both real detectors' actual findings.
"""
from __future__ import annotations

import unittest

from causeway.analysis import detectors, detectors_pool
from causeway.analysis.hypothesis import CATEGORY_BY_DETECTOR, CodeHypothesis, UNKNOWN_CATEGORY


def _hypothesis(**overrides) -> CodeHypothesis:
    base = dict(file="app.py", line=10, symbol="f", kind="k", observed="x = 1",
               counterfactual="x = 2", evidence="x = 1", reason="because",
               detector="sql_predicate_index_usability")
    base.update(overrides)
    return CodeHypothesis(**base)


class CategoryTests(unittest.TestCase):
    def test_a_known_detector_maps_to_its_fixed_category(self):
        self.assertEqual(_hypothesis(detector="sql_predicate_index_usability").category,
                         "DATABASE ISSUE")
        self.assertEqual(_hypothesis(detector="resource_release_not_guaranteed").category,
                         "RESOURCE ISSUE")

    def test_an_unrecognised_detector_is_honestly_unknown_not_guessed(self):
        self.assertEqual(_hypothesis(detector="some_future_detector").category, UNKNOWN_CATEGORY)

    def test_category_is_present_in_as_dict(self):
        self.assertEqual(_hypothesis().as_dict()["category"], "DATABASE ISSUE")

    def test_every_wired_detector_is_named_in_the_category_map(self):
        # Not exhaustive by design elsewhere in this codebase (a new detector
        # is free to ship with no category yet), but a silent gap here is
        # worth catching immediately rather than shipping UNKNOWN by accident.
        self.assertIn(detectors.NAME, CATEGORY_BY_DETECTOR)
        self.assertIn(detectors_pool.NAME, CATEGORY_BY_DETECTOR)


class LineEndTests(unittest.TestCase):
    def test_a_single_line_finding_has_the_same_start_and_end(self):
        self.assertEqual(_hypothesis(line=42, observed="order_id = ?").line_end, 42)

    def test_a_multi_line_finding_with_a_trailing_newline_on_every_line(self):
        observed = "pool.acquire()\nresult = do_work()\npool.release()\n"
        self.assertEqual(_hypothesis(line=10, observed=observed).line_end, 12)

    def test_a_multi_line_finding_whose_last_line_has_no_trailing_newline(self):
        observed = "pool.acquire()\nresult = do_work()\npool.release()"
        self.assertEqual(_hypothesis(line=10, observed=observed).line_end, 12)


class RealDetectorIntegrationTests(unittest.TestCase):
    """The properties as they actually come out of the two live detectors,
    not just the bare dataclass."""

    def test_the_sql_predicate_detector_finding_is_a_database_issue(self):
        schema = "CREATE TABLE t (order_id TEXT); CREATE INDEX ix ON t (order_id);"
        source = (
            "def lookup(conn, order_id):\n"
            "    return conn.execute('SELECT * FROM t WHERE UPPER(order_id) = ?', (order_id,))\n"
        )
        indexed = detectors.indexed_columns(schema)
        findings = detectors.scan_source("app.py", source, indexed)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, "DATABASE ISSUE")
        self.assertEqual(findings[0].line_end, findings[0].line)

    def test_the_pool_detector_finding_is_a_resource_issue_with_a_real_line_range(self):
        source = (
            "def handle_work(pool):\n"
            "    pool.acquire()\n"
            "    result = do_work()\n"
            "    pool.release()\n"
            "    return result\n"
        )
        finding = detectors_pool.scan_source("app.py", source)[0]
        self.assertEqual(finding.category, "RESOURCE ISSUE")
        self.assertGreater(finding.line_end, finding.line)
        # The range covers exactly acquire() through release(), inclusive.
        self.assertEqual(finding.line_end - finding.line, 2)


if __name__ == "__main__":
    unittest.main()
