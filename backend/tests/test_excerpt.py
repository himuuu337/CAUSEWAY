"""causeway.analysis.excerpt: a real window of a finding's own surrounding
source, and its wiring into both detectors. Every assertion here checks the
excerpt against the exact source text it was cut from - nothing here can
pass if a line were ever synthesized rather than copied.
"""
from __future__ import annotations

import unittest

from causeway.analysis import detectors, detectors_pool
from causeway.analysis.excerpt import excerpt_for


SOURCE = "\n".join("line %d" % n for n in range(1, 11)) + "\n"   # lines 1..10


class ExcerptForTests(unittest.TestCase):
    def test_a_single_line_finding_gets_context_on_both_sides(self):
        lines = excerpt_for(SOURCE, 5, 5, context=2)
        self.assertEqual([l.number for l in lines], [3, 4, 5, 6, 7])
        self.assertEqual([l.text for l in lines],
                         ["line 3", "line 4", "line 5", "line 6", "line 7"])
        self.assertEqual([l.highlighted for l in lines], [False, False, True, False, False])

    def test_a_multi_line_finding_highlights_every_line_it_spans(self):
        lines = excerpt_for(SOURCE, 4, 6, context=1)
        self.assertEqual([l.number for l in lines], [3, 4, 5, 6, 7])
        self.assertEqual([l.highlighted for l in lines], [False, True, True, True, False])

    def test_context_clamps_to_the_start_of_the_file(self):
        lines = excerpt_for(SOURCE, 1, 1, context=3)
        self.assertEqual(lines[0].number, 1)

    def test_context_clamps_to_the_end_of_the_file(self):
        lines = excerpt_for(SOURCE, 10, 10, context=3)
        self.assertEqual(lines[-1].number, 10)

    def test_every_returned_line_is_the_source_files_own_text(self):
        lines = excerpt_for(SOURCE, 5, 5, context=2)
        source_lines = SOURCE.splitlines()
        for line in lines:
            self.assertEqual(line.text, source_lines[line.number - 1])

    def test_an_out_of_range_start_line_returns_nothing_rather_than_raising(self):
        self.assertEqual(excerpt_for(SOURCE, 999, 999), ())
        self.assertEqual(excerpt_for("", 1, 1), ())

    def test_an_unreasonably_large_span_is_capped_not_dumped_whole(self):
        big_source = "\n".join("line %d" % n for n in range(1, 101))
        lines = excerpt_for(big_source, 1, 100, context=2)
        self.assertLessEqual(len(lines), 14)


class DetectorExcerptWiringTests(unittest.TestCase):
    def test_the_sql_predicate_detectors_finding_carries_a_real_excerpt(self):
        schema = "CREATE TABLE t (order_id TEXT); CREATE INDEX ix ON t (order_id);"
        source = (
            "def lookup(conn, order_id):\n"
            "    query = 'SELECT * FROM t WHERE UPPER(order_id) = ?'\n"
            "    return conn.execute(query, (order_id,))\n"
        )
        indexed = detectors.indexed_columns(schema)
        finding = detectors.scan_source("app.py", source, indexed)[0]
        self.assertTrue(finding.excerpt)
        highlighted = [l for l in finding.excerpt if l.highlighted]
        self.assertTrue(all("UPPER(order_id)" in l.text for l in highlighted))
        source_lines = source.splitlines()
        for line in finding.excerpt:
            self.assertEqual(line.text, source_lines[line.number - 1])

    def test_the_pool_detectors_finding_excerpt_covers_the_whole_acquire_release_span(self):
        source = (
            "def handle_work(pool):\n"
            "    pool.acquire()\n"
            "    result = do_work()\n"
            "    pool.release()\n"
            "    return result\n"
        )
        finding = detectors_pool.scan_source("app.py", source)[0]
        highlighted_text = [l.text for l in finding.excerpt if l.highlighted]
        self.assertEqual(highlighted_text,
                         ["    pool.acquire()", "    result = do_work()", "    pool.release()"])

    def test_the_excerpt_is_present_in_as_dict(self):
        source = "def handle_work(pool):\n    pool.acquire()\n    pool.release()\n"
        # nothing to leak here - use the sql detector instead for a real, non-empty finding
        schema = "CREATE TABLE t (order_id TEXT); CREATE INDEX ix ON t (order_id);"
        sql_source = "def f(c, i):\n    return c.execute('SELECT * FROM t WHERE UPPER(order_id) = ?', (i,))\n"
        finding = detectors.scan_source("app.py", sql_source, detectors.indexed_columns(schema))[0]
        payload = finding.as_dict()
        self.assertIn("excerpt", payload)
        self.assertTrue(payload["excerpt"])
        self.assertEqual(set(payload["excerpt"][0].keys()), {"number", "text", "highlighted"})


if __name__ == "__main__":
    unittest.main()
