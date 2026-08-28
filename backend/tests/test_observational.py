"""The observational baseline: right arithmetic, wrong answer, and blind by
construction."""
from __future__ import annotations

import ast
import os
import unittest

from causeway import observational as obs
from causeway.incident import deploy_record
from causeway.localizer import localize

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.record = deploy_record()
        self.candidates, _ = localize(self.record)
        self.assessments = obs.rank(self.candidates, self.record["incident"])
        self.by_id = {a.change_id: a for a in self.assessments}

    def test_it_ranks_the_decoy_first(self):
        self.assertEqual(obs.top_suspect(self.assessments), "A")

    def test_the_published_scores_are_what_the_formula_computes(self):
        """The demo quotes 0.961 and 0.567. They are computed here, from the
        deploy record, not written down anywhere."""
        self.assertEqual(self.by_id["A"].score, 0.961)
        self.assertEqual(self.by_id["B"].score, 0.567)

    def test_the_margin_is_not_a_coin_flip(self):
        self.assertGreater(self.by_id["A"].score - self.by_id["B"].score, 0.3)

    def test_diff_magnitude_is_what_drives_the_wrong_answer(self):
        """Neutralise the size term and the ranking stops being confident.
        That is the flaw the experiment exists to expose."""
        components = {k: self.by_id[k].components for k in ("A", "B")}
        without_magnitude = {
            k: sum(obs.WEIGHTS[name] * value
                   for name, value in components[k].items() if name != "magnitude")
            for k in ("A", "B")
        }
        gap_with = self.by_id["A"].score - self.by_id["B"].score
        gap_without = without_magnitude["A"] - without_magnitude["B"]
        self.assertLess(gap_without, gap_with / 2)

    def test_scores_are_bounded(self):
        for assessment in self.assessments:
            self.assertGreaterEqual(assessment.score, 0.0)
            self.assertLessEqual(assessment.score, 1.0)

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(obs.WEIGHTS.values()), 1.0)

    def test_is_deterministic(self):
        again = obs.rank(self.candidates, self.record["incident"])
        self.assertEqual([a.score for a in self.assessments],
                         [a.score for a in again])

    def test_the_reason_cites_the_evidence_it_actually_used(self):
        reason = self.by_id["A"].reason
        self.assertIn("412 lines", reason)
        self.assertIn("order-service", reason)


class BlindnessTests(unittest.TestCase):
    """The baseline gets the wrong answer because it only has correlational
    evidence - so it must be incapable of acquiring any other kind."""

    def _imports(self, module):
        path = os.path.join(BACKEND, *module.split(".")) + ".py"
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
        return found

    def test_the_baseline_cannot_reach_an_experiment(self):
        forbidden = {"causeway.verdict", "causeway.sandbox",
                     "causeway.sandbox.runner", "causeway.sandbox.replay",
                     "causeway.measurement", "causeway.orchestrator"}
        self.assertEqual(sorted(self._imports("causeway.observational") & forbidden), [])

    def test_the_baseline_cannot_reach_the_network_or_a_subprocess(self):
        forbidden = {"http", "http.client", "urllib", "socket", "subprocess",
                     "requests"}
        self.assertEqual(sorted(self._imports("causeway.observational") & forbidden), [])

    def test_no_public_function_accepts_a_measurement(self):
        import inspect
        for name in ("assess", "rank", "top_suspect"):
            params = list(inspect.signature(getattr(obs, name)).parameters)
            for suspicious in ("result", "results", "measurement", "sandbox",
                               "observed", "p95_ms"):
                self.assertNotIn(suspicious, params,
                                 "%s() can be handed a measurement" % name)


class LocalizerTests(unittest.TestCase):
    def setUp(self):
        self.record = deploy_record()
        self.candidates, self.excluded = localize(self.record)

    def test_returns_exactly_A_and_B(self):
        self.assertEqual([c.change_id for c in self.candidates], ["A", "B"])

    def test_every_deploy_is_either_a_candidate_or_explained(self):
        seen = {c.change_id for c in self.candidates} | {e.change_id for e in self.excluded}
        self.assertEqual(seen, {d["change_id"] for d in self.record["deploys"]})

    def test_another_service_is_excluded_with_a_reason(self):
        reasons = {e.change_id: e.reason for e in self.excluded}
        self.assertIn("billing-service", reasons["C"])

    def test_outside_the_window_is_excluded_with_a_reason(self):
        reasons = {e.change_id: e.reason for e in self.excluded}
        self.assertIn("window", reasons["D"])

    def test_is_deterministic(self):
        again, _ = localize(deploy_record())
        self.assertEqual([c.change_id for c in self.candidates],
                         [c.change_id for c in again])


if __name__ == "__main__":
    unittest.main()
