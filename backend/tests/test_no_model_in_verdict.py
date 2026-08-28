"""Causeway's core rule, enforced structurally.

    AI PROPOSES.  CODE VALIDATES.  SYSTEM EXPERIMENTS.  MEASUREMENTS DECIDE.

A language model proposes hypotheses and designs experiments. It never decides
whether a candidate is the cause. This test walks the first-party import graph
of the module that produces the verdict and fails the build if anything
model-shaped, networked or planner-shaped is reachable from it.

This is the test that makes the claim checkable instead of rhetorical.
"""
from __future__ import annotations

import ast
import os
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORBIDDEN_SUBSTRINGS = ("gemini", "openai", "anthropic", "llm", "langchain",
                        "generativeai", "transformers", "vertexai", "planner")
FORBIDDEN_MODULES = {"causeway.planner", "causeway.orchestrator", "causeway.cli"}
NETWORK_MODULES = {"http", "http.client", "urllib", "urllib.request", "socket",
                   "requests", "httpx", "subprocess"}
FIRST_PARTY = ("causeway",)


def _module_path(module: str):
    parts = module.split(".")
    candidate = os.path.join(BACKEND, *parts) + ".py"
    if os.path.exists(candidate):
        return candidate
    package = os.path.join(BACKEND, *parts, "__init__.py")
    return package if os.path.exists(package) else None


def _imports_of(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            found.add(node.module)
            for alias in node.names:
                found.add(node.module + "." + alias.name)
    return found


def reachable(entry: str):
    seen, all_imports, queue = set(), set(), [entry]
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _module_path(module)
        if path is None:
            continue
        for imported in _imports_of(path):
            all_imports.add(imported)
            if imported.split(".")[0] in FIRST_PARTY:
                queue.append(imported)
    return all_imports


class VerdictBoundaryTests(unittest.TestCase):
    def test_the_verdict_module_exists(self):
        self.assertIsNotNone(_module_path("causeway.verdict"))

    def test_no_model_is_reachable_from_the_verdict(self):
        offenders = sorted(
            name for name in reachable("causeway.verdict")
            if any(bad in name.lower() for bad in FORBIDDEN_SUBSTRINGS)
            or name in FORBIDDEN_MODULES)
        self.assertEqual(offenders, [],
                         "model-shaped imports reachable from the verdict: %r" % offenders)

    def test_the_verdict_cannot_reach_the_network_or_a_subprocess(self):
        reached = sorted(reachable("causeway.verdict") & NETWORK_MODULES)
        self.assertEqual(reached, [],
                         "the verdict path must not reach %r" % reached)

    def test_the_dependency_points_one_way(self):
        """planner imports verdict, never the reverse. If that ever inverted,
        a planner could reach the decision."""
        self.assertIn("causeway.verdict", reachable("causeway.planner"))
        self.assertNotIn("causeway.planner", reachable("causeway.verdict"))

    def test_the_verdict_function_takes_results_and_nothing_else(self):
        import inspect
        from causeway import verdict
        self.assertEqual(list(inspect.signature(verdict.decide).parameters),
                         ["results"])

    def test_measurement_is_also_model_free(self):
        offenders = sorted(
            name for name in reachable("causeway.measurement")
            if any(bad in name.lower() for bad in FORBIDDEN_SUBSTRINGS))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
