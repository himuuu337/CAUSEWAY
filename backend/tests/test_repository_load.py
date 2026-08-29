"""causeway.repository.load() - the layer above the manifest that also
validates the workload, builds the repository's OWN database from its own
schema and seed, and reads hypotheses out of its own source.

No real clone is needed here: a bare ClonedRepo pointed at a local checkout
exercises the same code the real clone path calls into.
"""
from __future__ import annotations

import json
import os
import sqlite3
import unittest

from causeway import config
from causeway.repository import load
from causeway.repository.errors import RepositoryRejected
from causeway.repository.git import ClonedRepo
from causeway.repository.urlcheck import RepoRef
from tests.repo_fixtures import local_repo
from tests.test_repository_manifest import GOOD_MANIFEST, _files

REF = RepoRef(owner="foo", name="bar", url="https://github.com/foo/bar")


def _cloned(path: str) -> ClonedRepo:
    return ClonedRepo(path=path, commit_sha="deadbeef" * 5, workdir=path)


class LoadTests(unittest.TestCase):
    def test_a_well_formed_repository_builds_a_context(self):
        with local_repo(copy_demo=True) as root:
            ctx = load(_cloned(root), REF)
            try:
                self.assertEqual(ctx.owner, "foo")
                self.assertEqual(ctx.name, "bar")
                self.assertEqual(ctx.service, "order-service")
                self.assertEqual(ctx.runtime, "python")
                self.assertEqual(ctx.verification, "latency_p95")
                self.assertGreater(len(ctx.workload["requests"]), 0)
            finally:
                pass

    def test_the_context_carries_no_ab_candidates_at_all(self):
        """The whole point of the repository path: there is no A and no B,
        and nothing that could be mistaken for one."""
        with local_repo(copy_demo=True) as root:
            ctx = load(_cloned(root), REF)
            for attribute in ("candidates", "repair_surfaces", "incident_record",
                              "fixture", "deploys"):
                self.assertFalse(hasattr(ctx, attribute),
                                 "RepositoryContext still carries %r" % attribute)
            ids = {h.id for h in ctx.hypotheses}
            self.assertNotIn("A", ids)
            self.assertNotIn("B", ids)

    def test_hypotheses_are_read_out_of_the_repositorys_own_source(self):
        with local_repo(copy_demo=True) as root:
            ctx = load(_cloned(root), REF)
            self.assertGreaterEqual(len(ctx.testable), 2)
            for hypothesis in ctx.hypotheses:
                self.assertIn(hypothesis.file, ctx.sources)
                path = os.path.join(root, hypothesis.file)
                with open(path, encoding="utf-8") as handle:
                    source = handle.read()
                # the evidence is text that is actually in the file, once
                self.assertEqual(source.count(hypothesis.observed), 1)

    def test_at_least_two_hypotheses_are_statically_indistinguishable(self):
        """One is the incident and one is innocent, and the detector cannot
        tell which - both are the same shape. Only the experiment separates
        them, which is the claim the whole product rests on."""
        with local_repo(copy_demo=True) as root:
            ctx = load(_cloned(root), REF)
            kinds = {h.kind for h in ctx.testable}
            detectors = {h.detector for h in ctx.testable}
            self.assertEqual(len(kinds), 1)
            self.assertEqual(len(detectors), 1)
            self.assertGreaterEqual(len(ctx.testable), 2)

    def test_the_database_is_built_from_the_repositorys_own_schema(self):
        with local_repo(copy_demo=True) as root:
            ctx = load(_cloned(root), REF)
            self.assertTrue(os.path.isfile(ctx.database_path))
            connection = sqlite3.connect(ctx.database_path)
            try:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                indexes = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name NOT LIKE 'sqlite_%'")}
            finally:
                connection.close()
            self.assertIn("order_audit", tables)
            self.assertIn("status_label", tables)
            self.assertIn("idx_audit_order", indexes)

    def test_the_repository_database_is_not_causeways_bundled_template(self):
        """A repository investigation that quietly ran against Causeway's own
        seeded fixture would be a demo pretending to be a product."""
        with local_repo(copy_demo=True) as root:
            ctx = load(_cloned(root), REF)
            self.assertNotEqual(os.path.realpath(ctx.database_path),
                                os.path.realpath(config.TEMPLATE_DB))
            self.assertNotEqual(os.path.realpath(ctx.work_db),
                                os.path.realpath(config.WORK_DB))
            # and it lives inside the disposable clone workspace
            self.assertTrue(os.path.realpath(ctx.database_path).startswith(
                os.path.realpath(root)))

    def test_as_event_carries_no_fabricated_history(self):
        """Only what the manifest actually declared, what git actually
        reported, and what was actually built or detected."""
        with local_repo(copy_demo=True) as root:
            ctx = load(_cloned(root), REF)
            event = ctx.as_event()
            self.assertEqual(event["commit_sha"], "deadbeef" * 5)
            self.assertEqual(event["owner"], "foo")
            self.assertNotIn("candidates", event)
            self.assertNotIn("deploys", event)
            self.assertNotIn("repair_surface", event)
            self.assertGreater(event["database"]["tables"]["order_audit"], 0)

    # -- rejections ---------------------------------------------------------

    def test_a_malformed_workload_file_is_rejected(self):
        with local_repo(_files(extra={"workload.json": "{not json"})) as root:
            with self.assertRaises(RepositoryRejected) as caught:
                load(_cloned(root), REF)
            self.assertEqual(caught.exception.stage, "manifest")

    def test_a_workload_missing_requests_is_rejected(self):
        with local_repo(_files(extra={
                "workload.json": json.dumps({"id": "x", "concurrency": 1})})) as root:
            with self.assertRaises(RepositoryRejected):
                load(_cloned(root), REF)

    def test_a_workload_with_a_non_positive_concurrency_is_rejected(self):
        with local_repo(_files(extra={
                "workload.json": json.dumps(
                    {"id": "x", "concurrency": 0, "requests": ["/health"]})})) as root:
            with self.assertRaises(RepositoryRejected):
                load(_cloned(root), REF)

    def test_a_workload_request_that_is_not_a_path_is_rejected(self):
        with local_repo(_files(extra={
                "workload.json": json.dumps(
                    {"id": "x", "concurrency": 1,
                     "requests": ["http://evil.example/steal"]})})) as root:
            with self.assertRaises(RepositoryRejected):
                load(_cloned(root), REF)

    def test_a_repository_with_nothing_testable_in_it_is_rejected(self):
        """Causeway's detectors are narrow, and a repository they cannot
        experiment on is told so - never quietly investigated as something
        else."""
        with local_repo(_files()) as root:
            with self.assertRaises(RepositoryRejected) as caught:
                load(_cloned(root), REF)
            self.assertEqual(caught.exception.stage, "analysis")


if __name__ == "__main__":
    unittest.main()
