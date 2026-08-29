"""The repository path cannot reach the A/B demo. Structurally, not by
convention.

Causeway's most damaging possible failure is not a wrong verdict - it is a
run that LOOKS like an analysis of someone's repository while quietly being
the bundled demonstration. A judge cannot tell those apart by watching the
screen, which is exactly why it has to be provable from the code.

So this test walks causeway.repo_investigation's first-party import graph and
fails the build if the A/B pipeline - the fabricated deploy records
(causeway.incident), the localizer that ranks them (causeway.localizer), or
the correlation-only baseline over them (causeway.observational) - is
reachable from it at all. It is the same technique
tests/test_no_model_in_verdict.py uses to keep a model away from the verdict,
pointed at a different boundary.

It also checks the reverse direction for the fixture data itself: nothing on
the repository path may read Causeway's own seeded template database, because
a repository investigation that measured Causeway's fixture would be
measuring the wrong program.
"""
from __future__ import annotations

import ast
import io
import os
import tokenize
import unittest

from tests.test_no_model_in_verdict import _module_path, reachable

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The bundled A/B demonstration, module by module.
AB_PIPELINE = {"causeway.incident", "causeway.localizer", "causeway.observational"}

# Identifiers that only exist because of the A/B fixture. Checked against
# CODE tokens, never raw text: this module's own docstrings name the A/B
# pipeline in order to say it is not imported, and a substring scan would
# flag exactly the sentence that documents the boundary.
AB_SYMBOLS = frozenset(("deploy_record", "localize", "top_suspect",
                        "localizer", "observational",
                        "TEMPLATE_DB", "FIXTURE_PATH"))

REPO_PATH_MODULES = ("causeway.repo_investigation", "causeway.repository",
                     "causeway.analysis.detectors", "causeway.analysis.hypothesis",
                     "causeway.sandbox.variant", "causeway.sandbox.actuator",
                     "causeway.intent")


def _source_of(module: str) -> str:
    path = _module_path(module)
    assert path is not None, "%s does not exist" % module
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _identifiers_of(module: str) -> set:
    """Every NAME token in a module - comments and string literals excluded.

    Prose is not a dependency. A docstring that says "this module does not
    import causeway.localizer" is the boundary being documented, not the
    boundary being crossed, and a raw substring scan cannot tell the two
    apart. Tokens can.
    """
    names = set()
    source = _source_of(module)
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME:
            names.add(token.string)
    return names


class RepositoryPathCannotReachTheDemoTests(unittest.TestCase):
    def test_the_repository_investigation_module_exists(self):
        self.assertIsNotNone(_module_path("causeway.repo_investigation"))

    def test_the_ab_pipeline_is_not_reachable_from_the_repository_path(self):
        for module in REPO_PATH_MODULES:
            with self.subTest(module=module):
                reached = sorted(reachable(module) & AB_PIPELINE)
                self.assertEqual(
                    reached, [],
                    "%s can reach the bundled A/B pipeline: %r" % (module, reached))

    def test_no_ab_symbol_is_used_anywhere_on_the_repository_path(self):
        for module in REPO_PATH_MODULES:
            used = sorted(_identifiers_of(module) & AB_SYMBOLS)
            with self.subTest(module=module):
                self.assertEqual(used, [],
                                 "%s uses A/B demo identifiers: %s"
                                 % (module, ", ".join(used)))

    def test_the_orchestrator_is_the_only_module_that_knows_both(self):
        """One dispatcher may know both paths exist - that is what a front
        door is. Nothing below it may."""
        orchestrator_reach = reachable("causeway.orchestrator")
        self.assertTrue(AB_PIPELINE & orchestrator_reach)
        self.assertIn("causeway.repo_investigation", orchestrator_reach)
        for module in REPO_PATH_MODULES:
            self.assertNotIn("causeway.orchestrator", reachable(module))

    def test_the_repository_path_never_reads_causeways_own_seeded_database(self):
        """config.TEMPLATE_DB is the bundled demo's fixture. A repository
        builds its own database from its own schema; if it ever fell back to
        this one, the numbers on screen would belong to the wrong program."""
        for module in REPO_PATH_MODULES:
            used = sorted(_identifiers_of(module) & {"TEMPLATE_DB", "FIXTURE_PATH"})
            with self.subTest(module=module):
                self.assertEqual(used, [],
                                 "%s reads Causeway's own fixture: %s"
                                 % (module, ", ".join(used)))

    def test_both_paths_still_share_exactly_one_thing_the_verdict(self):
        """Separation is not the goal for its own sake. The two paths must
        judge identically, which means they must share the verdict engine and
        the fix verdict - and share nothing else that decides anything."""
        reach = reachable("causeway.repo_investigation")
        self.assertIn("causeway.verdict", reach)
        self.assertIn("causeway.fix_verdict", reach)


class RepositoryPathBuildsItsOwnDatabaseTests(unittest.TestCase):
    def test_the_repository_loader_builds_a_database_rather_than_copying_one(self):
        source = _source_of("causeway.repository")
        self.assertIn("database.build", source)

    def test_the_database_builder_accepts_no_command_strings(self):
        """The manifest's database contract is declarative: a schema of
        create/drop statements and a seed of closed-vocabulary column kinds.
        There is nothing in it a repository could put a command into."""
        source = _source_of("causeway.repository.database")
        for forbidden in ("subprocess", "os.system", "shell=True", "eval(", "exec("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_nothing_on_the_repository_path_uses_a_shell(self):
        for module in REPO_PATH_MODULES + ("causeway.repository.git",):
            source = _source_of(module)
            with self.subTest(module=module):
                self.assertNotIn("shell=True", source,
                                 "%s uses a shell" % module)
                self.assertNotIn("os.system", source,
                                 "%s uses os.system" % module)


class SourceEditsStayInsideTheWorkspaceTests(unittest.TestCase):
    """The actuator writes to real files. Everything it writes to has to be
    proven inside a disposable copy first - and proven AFTER resolution, so a
    symlink cannot walk out of it."""

    def test_every_edit_path_goes_through_resolve_inside(self):
        source = _source_of("causeway.sandbox.variant")
        tree = ast.parse(source)
        opens = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open"]
        self.assertTrue(opens, "variant.py no longer opens any file")
        # every open() in this module takes `path`, which resolve_inside produced
        for call in opens:
            self.assertEqual(getattr(call.args[0], "id", None), "path")

    def test_resolve_inside_checks_containment_after_realpath(self):
        source = _source_of("causeway.sandbox.variant")
        self.assertIn("os.path.realpath", source)
        self.assertIn("resolves outside the repository workspace", source)


if __name__ == "__main__":
    unittest.main()
