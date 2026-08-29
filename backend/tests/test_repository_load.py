"""causeway.repository.load() - the layer above the manifest that also reads
and validates the fixture file, and builds the repair-surfaces mapping the
fix loop uses. No real clone is needed here: a bare ClonedRepo pointed at a
local checkout exercises the same code the real clone path calls into."""
from __future__ import annotations

import json
import unittest

from causeway.repository import load
from causeway.repository.errors import RepositoryRejected
from causeway.repository.git import ClonedRepo
from causeway.repository.urlcheck import RepoRef
from causeway.sandbox import repair
from tests.repo_fixtures import local_repo
from tests.test_repository_manifest import GOOD_MANIFEST, _files

REF = RepoRef(owner="foo", name="bar", url="https://github.com/foo/bar")


def _cloned(path: str) -> ClonedRepo:
    return ClonedRepo(path=path, commit_sha="deadbeef" * 5, workdir=path)


class LoadTests(unittest.TestCase):
    def test_a_well_formed_repository_builds_a_context(self):
        with local_repo(copy_demo=True) as root:
            ctx = load(_cloned(root), REF)
            self.assertEqual(ctx.owner, "foo")
            self.assertEqual(ctx.name, "bar")
            self.assertEqual(ctx.service, "order-service")
            self.assertEqual(ctx.runtime, "python")
            self.assertGreater(len(ctx.fixture["requests"]), 0)
            self.assertIn("B", [d["change_id"] for d in ctx.incident_record["deploys"]])

    def test_as_event_carries_no_fabricated_history(self):
        """Only what the manifest actually declared and what git actually
        reported - never anything Causeway invented about the repository."""
        with local_repo(copy_demo=True) as root:
            ctx = load(_cloned(root), REF)
            event = ctx.as_event()
            self.assertEqual(event["commit_sha"], "deadbeef" * 5)
            self.assertEqual(event["owner"], "foo")
            self.assertEqual({c["change_id"] for c in event["candidates"]}, {"A", "B", "C", "D"})

    def test_the_repair_surfaces_current_value_is_read_from_the_cloned_file(self):
        """Never trusted from the manifest - read live from the file, the
        same rule the bundled demo's own surfaces enforce."""
        with local_repo(copy_demo=True) as root:
            ctx = load(_cloned(root), REF)
            current = repair.current_value("B", "SCANNING_PREDICATE",
                                           surfaces=ctx.repair_surfaces)
            self.assertEqual(current, "order_id + 0 = ?")
            self.assertTrue(repair.is_safe_after(
                "B", "SCANNING_PREDICATE", "order_id = ?", surfaces=ctx.repair_surfaces))

    def test_a_malformed_fixture_file_is_rejected(self):
        with local_repo(_files(extra={"fixtures/incident-001.json": "{not json"})) as root:
            with self.assertRaises(RepositoryRejected) as caught:
                load(_cloned(root), REF)
            self.assertEqual(caught.exception.stage, "manifest")

    def test_a_fixture_missing_requests_is_rejected(self):
        with local_repo(_files(extra={
                "fixtures/incident-001.json": json.dumps({"id": "x", "concurrency": 1})})) as root:
            with self.assertRaises(RepositoryRejected):
                load(_cloned(root), REF)

    def test_a_fixture_with_a_non_positive_concurrency_is_rejected(self):
        with local_repo(_files(extra={
                "fixtures/incident-001.json": json.dumps(
                    {"id": "x", "concurrency": 0, "requests": ["/health"]})})) as root:
            with self.assertRaises(RepositoryRejected):
                load(_cloned(root), REF)


if __name__ == "__main__":
    unittest.main()
