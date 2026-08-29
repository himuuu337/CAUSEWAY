"""Causeway's core rule, enforced structurally, for the fix loop's own half:

    AI PROPOSES.  CODE VALIDATES.  SYSTEM EXPERIMENTS.  MEASUREMENTS DECIDE.

Gemini may propose a fix. It never decides whether that fix VERIFIED. This
walks causeway.fix_verdict's import graph exactly as
tests/test_no_model_in_verdict.py walks causeway.verdict's, using the same
`reachable()` helper, and fails the build if anything model-shaped, networked,
subprocess-shaped or fixer-shaped becomes reachable from it.
"""
from __future__ import annotations

import unittest

from tests.test_no_model_in_verdict import (FIRST_PARTY, FORBIDDEN_SUBSTRINGS,
                                            NETWORK_MODULES, _module_path,
                                            reachable)

FORBIDDEN_MODULES = {"causeway.fixer", "causeway.orchestrator", "causeway.cli",
                     "causeway.sandbox.fixapply", "causeway.sandbox.repair",
                     "causeway.planner"}


class FixVerdictBoundaryTests(unittest.TestCase):
    def test_the_fix_verdict_module_exists(self):
        self.assertIsNotNone(_module_path("causeway.fix_verdict"))

    def test_no_model_is_reachable_from_the_fix_verdict(self):
        offenders = sorted(
            name for name in reachable("causeway.fix_verdict")
            if any(bad in name.lower() for bad in FORBIDDEN_SUBSTRINGS)
            or name in FORBIDDEN_MODULES)
        self.assertEqual(offenders, [],
                         "model-shaped imports reachable from the fix verdict: %r" % offenders)

    def test_the_fix_verdict_cannot_reach_the_network_or_a_subprocess(self):
        """subprocess is explicitly in NETWORK_MODULES - the sandbox launches
        the patched service through it, and that must stay unreachable from
        the module that decides VERIFIED/FAILED/UNRESOLVED."""
        reached = sorted(reachable("causeway.fix_verdict") & NETWORK_MODULES)
        self.assertEqual(reached, [],
                         "the fix verdict path must not reach %r" % reached)

    def test_the_fix_verdict_only_imports_the_causal_verdict_and_measurement(self):
        """Narrower than the generic forbidden-list check above: the fix
        verdict's own first-party import graph should be exactly the two
        modules causeway.verdict already proved clean (plus the bare
        `causeway` package name itself, which `from causeway import x`
        contributes as an artifact of how reachable() walks imports - the
        same artifact causeway.verdict's own import of measurement produces)."""
        reached_first_party = {name for name in reachable("causeway.fix_verdict")
                               if name.split(".")[0] in FIRST_PARTY}
        self.assertEqual(reached_first_party,
                         {"causeway", "causeway.verdict", "causeway.measurement"})

    def test_the_dependency_points_one_way(self):
        """fixer imports fix_verdict's PROVEN-adjacent constant path (via
        causeway.verdict), never the reverse - if that ever inverted, a fix
        planner could reach the decision."""
        self.assertIn("causeway.verdict", reachable("causeway.fixer"))
        self.assertNotIn("causeway.fixer", reachable("causeway.fix_verdict"))
        self.assertNotIn("causeway.fix_verdict", reachable("causeway.fixer"))

    def test_the_fix_verdict_function_takes_results_and_nothing_else(self):
        import inspect

        from causeway import fix_verdict
        self.assertEqual(list(inspect.signature(fix_verdict.decide).parameters),
                         ["results"])

    def test_the_causal_verdict_cannot_reach_the_fix_verdict(self):
        """The dependency runs one way, same as causeway.verdict -> nothing:
        causeway.fix_verdict legitimately imports causeway.verdict (the
        shared local_control/failure_present/recovered arithmetic), but
        causeway.verdict must never import causeway.fix_verdict back."""
        self.assertNotIn("causeway.fix_verdict", reachable("causeway.verdict"))


if __name__ == "__main__":
    unittest.main()
