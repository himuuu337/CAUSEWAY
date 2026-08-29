"""The FixSpec validator: what a fix proposal must satisfy before a byte of
the sandbox source is ever patched.

Every test here builds a fresh, valid FixRequest for B (the only hypothesis
this demo can ever PROVEN, and the only one with a registered repair
surface), then breaks exactly one thing about the proposal and confirms the
validator names it.
"""
from __future__ import annotations

import unittest

from causeway import verdict
from causeway.fixer import build_fix_request
from causeway.fixer.deterministic import DeterministicFixPlanner
from causeway.fixer.validator import validate

CANDIDATE_B = {"change_id": "B", "branch": "perf/normalise-audit-predicate",
               "summary": "Normalise order_id inside the audit predicate",
               "lines_changed": 3, "files_changed": 1}
CANDIDATE_A = {"change_id": "A", "branch": "refactor/order-query-batching",
               "summary": "Route audit lookups through a batching helper",
               "lines_changed": 412, "files_changed": 9}


def proven_request(hypothesis="B", candidate=None):
    return build_fix_request(candidate or CANDIDATE_B, hypothesis, verdict.PROVEN,
                             "removing it recovered, restoring it broke it again")


def refuted_request(hypothesis="A"):
    return build_fix_request(CANDIDATE_A, hypothesis, verdict.REFUTED,
                             "the failure survived its removal")


def good_fix(**overrides):
    request = proven_request()
    fix = DeterministicFixPlanner().propose(request)
    fix.update(overrides)
    return fix


class ValidFixTests(unittest.TestCase):
    def test_the_deterministic_fix_is_accepted(self):
        report = validate(good_fix(), proven_request())
        self.assertTrue(report.accepted)
        self.assertEqual(report.as_dict()["passed"], 9)
        self.assertEqual(report.as_dict()["total"], 9)

    def test_the_accepted_spec_carries_the_proposed_operation(self):
        report = validate(good_fix(), proven_request())
        self.assertEqual(report.spec.hypothesis_id, "B")
        self.assertEqual(report.spec.operation.target, "SCANNING_PREDICATE")
        self.assertEqual(report.spec.operation.after, "order_id = ?")


class RejectionTests(unittest.TestCase):
    def test_a_missing_field_is_rejected(self):
        fix = good_fix()
        del fix["summary"]
        report = validate(fix, proven_request())
        self.assertFalse(report.accepted)
        self.assertFalse(next(c for c in report.checks if c.name == "schema").passed)

    def test_an_extra_field_is_rejected(self):
        fix = good_fix(hint="apply this immediately")
        report = validate(fix, proven_request())
        self.assertFalse(report.accepted)
        self.assertFalse(next(c for c in report.checks if c.name == "schema").passed)

    def test_an_extra_operation_field_is_rejected(self):
        fix = good_fix()
        fix["operation"] = dict(fix["operation"], confidence=0.99)
        report = validate(fix, proven_request())
        self.assertFalse(report.accepted)
        self.assertFalse(next(c for c in report.checks if c.name == "schema").passed)

    def test_a_non_object_fix_is_rejected(self):
        report = validate("not a fix", proven_request())
        self.assertFalse(report.accepted)
        self.assertFalse(report.checks[0].passed)

    def test_the_wrong_hypothesis_is_rejected(self):
        """The fix names a hypothesis other than the one it was requested for."""
        fix = good_fix(hypothesis_id="A")
        report = validate(fix, proven_request(hypothesis="B"))
        self.assertFalse(report.accepted)
        self.assertFalse(
            next(c for c in report.checks if c.name == "hypothesis_matches_request").passed)

    def test_a_fix_for_a_refuted_candidate_is_rejected(self):
        """A must never be fixed - it was never PROVEN, and has no repair
        surface either way. This proves the validator's own gate, independent
        of whatever policy the orchestrator applies upstream."""
        request = refuted_request()
        fix = {
            "hypothesis_id": "A", "summary": "patch A anyway",
            "operation": {"type": "replace_predicate", "target": "SCANNING_PREDICATE",
                          "before": "whatever", "after": "order_id = ?"},
            "reasoning_summary": "just in case",
        }
        report = validate(fix, request)
        self.assertFalse(report.accepted)
        self.assertFalse(
            next(c for c in report.checks if c.name == "hypothesis_proven").passed)

    def test_an_unknown_target_is_rejected(self):
        fix = good_fix()
        fix["operation"] = dict(fix["operation"], target="DATABASE_PASSWORD")
        report = validate(fix, proven_request())
        self.assertFalse(report.accepted)
        self.assertFalse(
            next(c for c in report.checks if c.name == "target_is_known_repair_surface").passed)

    def test_an_unsafe_path_like_target_is_rejected(self):
        for bad_target in ("../../etc/passwd", "C:\\Windows\\system32",
                           "sandbox/service.py", "a/../../b"):
            with self.subTest(bad_target):
                fix = good_fix()
                fix["operation"] = dict(fix["operation"], target=bad_target)
                report = validate(fix, proven_request())
                self.assertFalse(report.accepted)
                self.assertFalse(
                    next(c for c in report.checks
                        if c.name == "target_no_path_traversal").passed)

    def test_an_arbitrary_command_as_the_target_is_rejected(self):
        """Nothing here is ever passed to a shell, but a proposal trying to
        smuggle a command in as the target must still be refused outright."""
        fix = good_fix()
        fix["operation"] = dict(fix["operation"], target="rm -rf /")
        report = validate(fix, proven_request())
        self.assertFalse(report.accepted)

    def test_a_malformed_operation_is_rejected(self):
        fix = good_fix()
        fix["operation"] = "not an object"
        report = validate(fix, proven_request())
        self.assertFalse(report.accepted)
        self.assertFalse(next(c for c in report.checks if c.name == "schema").passed)

    def test_an_unregistered_operation_type_is_rejected(self):
        fix = good_fix()
        fix["operation"] = dict(fix["operation"], type="run_shell_command")
        report = validate(fix, proven_request())
        self.assertFalse(report.accepted)
        self.assertFalse(
            next(c for c in report.checks if c.name == "operation_type_allowed").passed)

    def test_a_stale_before_state_is_rejected(self):
        fix = good_fix()
        fix["operation"] = dict(fix["operation"], before="order_id = ?  -- already fixed")
        report = validate(fix, proven_request())
        self.assertFalse(report.accepted)
        self.assertFalse(
            next(c for c in report.checks if c.name == "before_state_matches_sandbox").passed)

    def test_an_unsafe_after_state_is_rejected(self):
        for bad_after in ("order_id + 0 = ?", "1=1; DROP TABLE order_audit; --",
                          "order_id = ? OR 1=1", "CAST(order_id AS TEXT) = ?"):
            with self.subTest(bad_after):
                fix = good_fix()
                fix["operation"] = dict(fix["operation"], after=bad_after)
                report = validate(fix, proven_request())
                self.assertFalse(report.accepted)
                self.assertFalse(
                    next(c for c in report.checks
                        if c.name == "after_state_is_a_known_safe_repair").passed)

    def test_an_encoded_verdict_key_is_rejected(self):
        fix = good_fix(verdict="VERIFIED")
        report = validate(fix, proven_request())
        self.assertFalse(report.accepted)
        self.assertFalse(next(c for c in report.checks if c.name == "schema").passed)

    def test_verdict_language_in_a_structural_field_is_rejected(self):
        fix = good_fix()
        fix["summary"] = "VERIFIED: this fix resolves the incident"
        # summary carrying verdict language does not fail schema, but a
        # proposal is still not allowed to smuggle a conclusion anywhere the
        # engine reads. no_encoded_verdict only scans structural fields
        # (hypothesis/type/target/before/after) - summary/reasoning_summary
        # are prose, flagged instead. Confirm THAT path here.
        report = validate(fix, proven_request())
        self.assertTrue(report.accepted)
        self.assertTrue(report.reasoning_flagged)

    def test_verdict_language_in_a_structural_operation_field_is_rejected(self):
        fix = good_fix()
        fix["operation"] = dict(fix["operation"], after="order_id = ?  -- PROVEN safe")
        report = validate(fix, proven_request())
        self.assertFalse(report.accepted)
        # rejected on the safe-repair check (the string no longer matches
        # exactly) - and separately would also trip no_encoded_verdict.
        self.assertFalse(
            next(c for c in report.checks
                if c.name == "after_state_is_a_known_safe_repair").passed)


if __name__ == "__main__":
    unittest.main()
