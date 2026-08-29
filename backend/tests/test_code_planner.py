"""The planner on the REPOSITORY path: what it may see, and what it may not.

The bundled demo's boundary is already covered by tests/test_gemini.py. This
file covers the same boundary for a code-location request, because the
evidence is different and a different prompt renders it - and a boundary that
only holds on one of two paths is not a boundary.

Nothing here touches the network: the transport is mocked, and the
deterministic planner needs no transport at all.
"""
from __future__ import annotations

import json
import re
import unittest

from causeway import planner, verdict
from causeway.analysis.hypothesis import CodeHypothesis
from causeway.planner.deterministic import DeterministicPlanner
from causeway.planner.gemini import GeminiPlanner, build_prompt
from causeway.planner.schema import CODE_EVIDENCE

INCIDENT = {
    "id": "INCIDENT-001", "title": "Order Service Latency Incident",
    "service": "order-service",
    "symptom": "p95 latency on the order audit endpoint",
    "detected_at": "2026-08-28T14:05:00Z",
}

CAUSE = CodeHypothesis(
    file="db.py", line=19, symbol="lookup_order_audit", kind="query_predicate",
    observed="order_id + 0 = ?", counterfactual="order_id = ?",
    evidence="order_id + 0 = ?",
    reason=("order_id is indexed by this repository's schema, but the predicate "
            "wraps it in arithmetic (+ 0)."),
    detector="sql_predicate_index_usability")

DECOY = CodeHypothesis(
    file="db.py", line=29, symbol="lookup_status_label", kind="query_predicate",
    observed="UPPER(code) = ?", counterfactual="code = ?",
    evidence="UPPER(code) = ?",
    reason=("code is indexed by this repository's schema, but the predicate "
            "wraps it in the UPPER() function."),
    detector="sql_predicate_index_usability")

HYPOTHESES = (CAUSE, DECOY)
STATE = {h.id: True for h in HYPOTHESES}


def request_for(target=None):
    return planner.build_request_for_code(
        INCIDENT, HYPOTHESES, STATE, ["order-audit-latency"],
        target or CAUSE.id)


class CodeRequestTests(unittest.TestCase):
    def test_the_request_is_marked_as_code_evidence(self):
        request = request_for()
        self.assertEqual(request.evidence_kind, CODE_EVIDENCE)
        self.assertTrue(request.is_code)

    def test_candidate_ids_are_the_hypothesis_ids_never_A_or_B(self):
        ids = request_for().candidate_ids
        self.assertEqual(sorted(ids), sorted(h.id for h in HYPOTHESES))
        self.assertNotIn("A", ids)
        self.assertNotIn("B", ids)

    def test_the_thresholds_come_from_the_verdict_engine(self):
        """A planner is told the rule it will be judged by. It does not get to
        choose it, and the numbers are read from causeway.verdict rather than
        restated anywhere on this path."""
        request = request_for()
        self.assertEqual(request.failure_factor, verdict.FAILURE_FACTOR)
        self.assertEqual(request.recovery_factor, verdict.RECOVERY_FACTOR)


class CodeInformationBoundaryTests(unittest.TestCase):
    """The prompt is the boundary. These assert on the string it returns."""

    def setUp(self):
        self.prompt = build_prompt(request_for())

    def test_the_prompt_contains_no_verdict_word(self):
        upper = self.prompt.upper()
        for word in ("PROVEN", "REFUTED", "SUPPORTED", "UNRESOLVED"):
            self.assertNotIn(word, upper, "%s leaked into the planner prompt" % word)

    def test_the_prompt_names_no_phase_of_the_experiment(self):
        lowered = self.prompt.lower()
        for token in ("ablate", "ablation", "reproduce", "restore", "recheck",
                      "control-1", "control-2", "control-3", "control-4",
                      "phase_result", "phase_judged"):
            self.assertNotIn(token, lowered,
                             "%r leaked into the planner prompt" % token)

    def test_the_prompt_contains_no_measured_latency(self):
        found = re.findall(r"\d+(?:\.\d+)?\s*(?:ms|milliseconds|s\b)",
                           self.prompt.lower())
        self.assertEqual(found, [], "a latency leaked into the prompt: %r" % found)

    def test_the_prompt_never_says_which_location_is_the_cause(self):
        lowered = self.prompt.lower()
        for phrase in ("is the cause", "is the root cause", "actual cause",
                       "the decoy", "innocent", "known cause",
                       "40,000", "six-row", "large table", "small table"):
            self.assertNotIn(phrase, lowered)

    def test_the_prompt_says_the_locations_are_indistinguishable(self):
        """The honest framing is part of the boundary: a planner that is told
        it can settle this from the source alone will try to."""
        self.assertIn("statically indistinguishable", self.prompt)

    def test_the_prompt_shows_both_locations_and_their_evidence(self):
        for hypothesis in HYPOTHESES:
            self.assertIn(hypothesis.id, self.prompt)
            self.assertIn(hypothesis.file, self.prompt)
            self.assertIn(json.dumps(hypothesis.observed), self.prompt)

    def test_the_prompt_never_mentions_a_runtime_flag_the_repository_has_no_such_thing(self):
        self.assertNotIn("runtime flags", self.prompt)


class CodePlanValidationTests(unittest.TestCase):
    """A code plan is checked by the SAME validator a deploy plan is - the one
    in causeway/planner/validator.py, unchanged for either."""

    def _validated(self, provider):
        return planner.plan_experiment(request_for(), provider)

    def test_the_deterministic_planner_produces_an_accepted_code_plan(self):
        outcome = self._validated(DeterministicPlanner())
        report = outcome.report.as_dict()
        self.assertTrue(outcome.report.accepted)
        self.assertEqual(report["passed"], report["total"])
        self.assertEqual(outcome.plan.hypothesis_id, CAUSE.id)
        self.assertFalse(outcome.used_fallback)

    def test_a_plan_naming_a_location_that_was_not_detected_is_rejected(self):
        raw = DeterministicPlanner().propose(request_for())
        raw["hypothesis_id"] = "db.py:something_else:query_predicate@000000"
        raw["intervention"] = {"flag": raw["hypothesis_id"], "value": False}
        report = planner.validate(raw, request_for())
        self.assertFalse(report.accepted)
        self.assertIn("hypothesis_in_candidates",
                      [c.name for c in report.rejections])

    def test_a_plan_moving_two_locations_at_once_is_rejected(self):
        request = request_for()
        raw = DeterministicPlanner().propose(request)
        raw["intervention"] = {"flag": DECOY.id, "value": False}
        report = planner.validate(raw, request)
        self.assertFalse(report.accepted)
        self.assertIn("single_independent_variable",
                      [c.name for c in report.rejections])

    def test_a_plan_that_encodes_a_conclusion_is_rejected(self):
        request = request_for()
        raw = DeterministicPlanner().propose(request)
        raw["verdict"] = "PROVEN"
        report = planner.validate(raw, request)
        self.assertFalse(report.accepted)

    def test_a_gemini_failure_falls_back_and_is_labelled_as_a_fallback(self):
        import urllib.error

        def transport(url, headers, body):
            raise urllib.error.URLError("getaddrinfo failed")

        provider = GeminiPlanner(api_key="AIzaSy-not-a-real-key", model="test",
                                 timeout=1.0, transport=transport)
        outcome = self._validated(provider)
        self.assertTrue(outcome.report.accepted)
        self.assertTrue(outcome.used_fallback)
        self.assertEqual(outcome.kind, "deterministic")
        self.assertTrue(outcome.fallback_reason)

    def test_the_phases_come_from_the_verdict_engine_not_the_plan(self):
        outcome = self._validated(DeterministicPlanner())
        specs = planner.phases_for(outcome.plan, STATE)
        self.assertEqual([s.phase for s in specs], list(verdict.PHASES))
        # the incident state is the repository as cloned: nothing removed
        reproduce = next(s for s in specs if s.phase == verdict.PHASE_REPRODUCE)
        self.assertTrue(all(reproduce.flags.values()))
        # the ablation removes exactly one location
        ablate = next(s for s in specs if s.phase == verdict.PHASE_ABLATE)
        self.assertEqual([k for k, v in ablate.flags.items() if not v], [CAUSE.id])


class CodeFixBoundaryTests(unittest.TestCase):
    """The fix planner on the repository path. Same rule as the experiment
    planner's: it may see the broken value, and it may not be handed the
    answer the validator exists to check its proposal against."""

    def setUp(self):
        import os
        import tempfile

        from causeway import fixer
        from causeway.fixer.gemini import build_prompt as fix_prompt
        from causeway.intent import parse

        self.workspace = tempfile.mkdtemp(prefix="causeway-fixtest-")
        with open(os.path.join(self.workspace, "db.py"), "w",
                  encoding="utf-8", newline="") as handle:
            handle.write('SQL = """SELECT 1 FROM order_audit '
                         'WHERE order_id + 0 = ?"""\n')
        self.request = fixer.build_code_fix_request(
            CAUSE, verdict.PROVEN, "removing it recovered and restoring it did not",
            self.workspace, parse("find why it is slow and fix it"))
        self.prompt = fix_prompt(self.request)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_the_prompt_never_quotes_the_known_safe_repair(self):
        self.assertNotIn(CAUSE.counterfactual, self.prompt)

    def test_the_prompt_contains_no_fix_verdict_word(self):
        upper = self.prompt.upper()
        for word in ("VERIFIED", "FAILED", "UNRESOLVED"):
            self.assertNotIn(word, upper)

    def test_the_prompt_contains_no_measurement(self):
        found = re.findall(r"\d+(?:\.\d+)?\s*(?:ms|milliseconds)",
                           self.prompt.lower())
        self.assertEqual(found, [])

    def test_the_prompt_shows_the_location_and_the_broken_value(self):
        self.assertIn("db.py", self.prompt)
        self.assertIn(json.dumps(CAUSE.observed), self.prompt)

    def test_the_prompt_carries_the_users_instruction_verbatim(self):
        self.assertIn("find why it is slow and fix it", self.prompt)

    def test_the_repair_target_is_a_bare_name_never_a_path(self):
        from causeway import fixer
        target = fixer.code_target(CAUSE)
        for separator in ("/", "\\", "..", ":", "\x00"):
            self.assertNotIn(separator, target)

    def test_the_current_value_is_read_live_from_the_workspace(self):
        target = self.request.repair_targets[0]
        self.assertEqual(self.request.current_code[target], CAUSE.observed)

    def test_a_workspace_that_no_longer_says_what_the_hypothesis_says_is_refused(self):
        import os

        from causeway import fixer
        from causeway.intent import parse
        with open(os.path.join(self.workspace, "db.py"), "w",
                  encoding="utf-8", newline="") as handle:
            handle.write("SQL = \"\"\"SELECT 1\"\"\"\n")
        with self.assertRaises(fixer.FixSurfaceUnavailable):
            fixer.build_code_fix_request(
                CAUSE, verdict.PROVEN, "reason", self.workspace,
                parse("fix it"))

    def test_the_edit_written_is_the_repositorys_own_text_not_the_proposals(self):
        """A proposal is validated whitespace-insensitively, which is the right
        test for "did you understand the surface" and the wrong text to write
        into a file. The bytes come from the hypothesis."""
        from causeway import fixer
        from causeway.fixer.schema import FixOperation
        operation = FixOperation(type="replace_predicate",
                                 target=fixer.code_target(CAUSE),
                                 before="order_id   +   0   =   ?",
                                 after="order_id   =   ?")
        edit = fixer.edit_for(operation, CAUSE)
        self.assertEqual(edit.before, CAUSE.observed)
        self.assertEqual(edit.after, CAUSE.counterfactual)
        self.assertEqual(edit.file, CAUSE.file)


if __name__ == "__main__":
    unittest.main()
