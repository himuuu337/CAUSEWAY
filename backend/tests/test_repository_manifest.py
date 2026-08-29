"""causeway.json version 2 validation. Every repository here is a real,
disposable local git checkout (tests.repo_fixtures.local_repo) - no network.

The single most important group of tests in this file is AnswerKeyTests. A
manifest declares capabilities; the moment it can declare the answer, nothing
Causeway concludes means anything, because the repository told it. Version 1
allowed exactly that (`deploys`, `repair_surface`), which is why version 1 is
rejected here rather than supported alongside version 2.
"""
from __future__ import annotations

import json
import os
import unittest

from causeway.repository.errors import RepositoryRejected
from causeway.repository.manifest import ANSWER_KEYS, load
from tests.repo_fixtures import local_repo

SCHEMA_SQL = """
-- a small, well-indexed schema
CREATE TABLE order_audit (
    id       INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    payload  TEXT NOT NULL
);
CREATE INDEX idx_audit_order ON order_audit(order_id);
"""

GOOD_MANIFEST = {
    "version": 2,
    "service": "order-service",
    "runtime": "python",
    "entrypoint": "app.py",
    "sources": ["app.py", "db.py"],
    "patchable": ["db.py"],
    "workload": "workload.json",
    "verification": "latency_p95",
    "incident": {
        "id": "INCIDENT-001", "title": "t", "service": "order-service",
        "symptom": "s", "detected_at": "2026-08-28T14:05:00Z",
    },
    "database": {
        "engine": "sqlite",
        "schema": "schema.sql",
        "seed": [{
            "table": "order_audit",
            "rows": 10,
            "columns": {
                "id": {"kind": "rowid"},
                "order_id": {"kind": "cycle", "modulo": 5, "offset": 1},
                "payload": {"kind": "text", "length": 8},
            },
        }],
    },
}


def _files(manifest=None, extra=None):
    files = {
        "causeway.json": json.dumps(GOOD_MANIFEST if manifest is None else manifest),
        "app.py": "# a fake entrypoint\n",
        "db.py": "# a fake data-access module\n",
        "schema.sql": SCHEMA_SQL,
        "workload.json": json.dumps(
            {"id": "workload-001", "concurrency": 1, "requests": ["/health"]}),
    }
    if extra:
        files.update(extra)
    return files


class AcceptanceTests(unittest.TestCase):
    def test_a_well_formed_manifest_is_accepted(self):
        with local_repo(_files()) as root:
            manifest = load(root)
            self.assertEqual(manifest.version, 2)
            self.assertEqual(manifest.service, "order-service")
            self.assertEqual(manifest.runtime, "python")
            self.assertEqual(manifest.verification, "latency_p95")
            self.assertEqual(manifest.sources, ("app.py", "db.py"))
            self.assertEqual(manifest.patchable, ("db.py",))
            self.assertTrue(os.path.isfile(manifest.entrypoint_path))
            self.assertTrue(os.path.isfile(manifest.workload_path))
            self.assertTrue(os.path.isfile(manifest.schema_path))

    def test_the_real_demo_repo_is_itself_accepted(self):
        """The actual demo-repo/ directory this project ships must pass its
        own contract - if it didn't, nothing else here would mean anything."""
        with local_repo(copy_demo=True) as root:
            manifest = load(root)
            self.assertEqual(manifest.service, "order-service")
            self.assertEqual(manifest.version, 2)


class AnswerKeyTests(unittest.TestCase):
    """A manifest may describe what a repository CAN do. It may never
    describe what Causeway should conclude about it."""

    def _rejects_key(self, key, value):
        bad = dict(GOOD_MANIFEST)
        bad[key] = value
        with local_repo(_files(bad)) as root:
            with self.assertRaises(RepositoryRejected) as caught:
                load(root)
        self.assertIn(key, caught.exception.reason)
        return caught.exception

    def test_every_answer_key_is_rejected_by_name(self):
        for key in ANSWER_KEYS:
            with self.subTest(key=key):
                exception = self._rejects_key(key, "anything at all")
                self.assertEqual(exception.stage, "manifest")

    def test_the_version_1_answer_keys_are_the_ones_that_are_rejected(self):
        """Version 1 let a repository hand Causeway both the suspects and the
        repair. Both spellings are named in ANSWER_KEYS for that reason."""
        self.assertIn("deploys", ANSWER_KEYS)
        self.assertIn("repair_surface", ANSWER_KEYS)

    def test_a_root_cause_declaration_is_rejected(self):
        self._rejects_key("root_cause", {"file": "db.py", "line": 19})

    def test_the_rejection_explains_how_causeway_actually_decides(self):
        exception = self._rejects_key("verdict", "PROVEN")
        self.assertIn("measuring", exception.reason)


class RejectionTests(unittest.TestCase):
    def _rejects(self, files, stage="manifest"):
        with local_repo(files) as root:
            with self.assertRaises(RepositoryRejected) as caught:
                load(root)
            if stage:
                self.assertEqual(caught.exception.stage, stage)
            return caught.exception

    def test_a_missing_manifest_is_rejected(self):
        self._rejects({"README.md": "nothing here"})

    def test_malformed_json_is_rejected(self):
        self._rejects(_files(extra={"causeway.json": "{not json"}))

    def test_a_non_object_manifest_is_rejected(self):
        self._rejects(_files(extra={"causeway.json": json.dumps([1, 2, 3])}))

    def test_a_missing_required_top_level_field_is_rejected(self):
        for field in ("service", "runtime", "entrypoint", "sources", "patchable",
                      "workload", "verification", "incident", "database"):
            with self.subTest(field=field):
                bad = dict(GOOD_MANIFEST)
                del bad[field]
                self._rejects(_files(bad))

    def test_version_1_is_no_longer_supported(self):
        bad = dict(GOOD_MANIFEST, version=1)
        exception = self._rejects(_files(bad))
        self.assertIn("version", exception.reason)

    def test_an_unsupported_future_version_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, version=3)))

    def test_a_missing_version_is_rejected(self):
        bad = dict(GOOD_MANIFEST)
        del bad["version"]
        self._rejects(_files(bad))

    def test_an_unknown_runtime_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, runtime="node")))

    def test_an_unknown_verification_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, verification="eyeball")))

    # -- paths --------------------------------------------------------------

    def test_path_traversal_in_entrypoint_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, entrypoint="../../etc/passwd")))

    def test_an_absolute_entrypoint_path_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, entrypoint="/etc/passwd")))

    def test_a_windows_drive_absolute_entrypoint_path_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, entrypoint="C:/Windows/System32/cmd.exe")))

    def test_path_traversal_in_workload_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, workload="../outside.json")))

    def test_path_traversal_in_sources_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, sources=["app.py", "../../etc/passwd"])))

    def test_path_traversal_in_patchable_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, patchable=["../../etc/passwd"])))

    def test_path_traversal_in_the_schema_is_rejected(self):
        bad = dict(GOOD_MANIFEST,
                   database=dict(GOOD_MANIFEST["database"], schema="../../etc/passwd"))
        self._rejects(_files(bad))

    def test_a_missing_entrypoint_file_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, entrypoint="does_not_exist.py")))

    def test_a_missing_workload_file_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, workload="nope.json")))

    def test_an_empty_sources_list_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, sources=[])))

    def test_an_empty_patchable_list_is_rejected(self):
        self._rejects(_files(dict(GOOD_MANIFEST, patchable=[])))

    # -- the database contract ----------------------------------------------

    def test_an_unsupported_database_engine_is_rejected(self):
        bad = dict(GOOD_MANIFEST,
                   database=dict(GOOD_MANIFEST["database"], engine="postgres"))
        self._rejects(_files(bad))

    def test_a_database_without_a_seed_is_rejected(self):
        database = dict(GOOD_MANIFEST["database"])
        del database["seed"]
        self._rejects(_files(dict(GOOD_MANIFEST, database=database)))

    def test_a_schema_that_would_need_a_statement_causeway_will_not_run_is_rejected(self):
        """The schema vocabulary is a closed set: create/drop table, index and
        view. Anything that attaches a file, loads an extension or writes one
        is refused before a database file exists."""
        for statement in ("ATTACH DATABASE '/etc/passwd' AS leak;",
                          "PRAGMA writable_schema = ON;",
                          "SELECT load_extension('evil.so');",
                          "INSERT INTO order_audit VALUES (1, 1, 'x');",
                          "DELETE FROM order_audit;"):
            with self.subTest(statement=statement):
                self._rejects(_files(extra={"schema.sql": SCHEMA_SQL + statement}),
                              stage="database")

    def test_an_unknown_seed_column_kind_is_rejected(self):
        database = dict(GOOD_MANIFEST["database"])
        database["seed"] = [{
            "table": "order_audit", "rows": 10,
            "columns": {"id": {"kind": "shell_command", "value": "rm -rf /"}},
        }]
        self._rejects(_files(dict(GOOD_MANIFEST, database=database)), stage="database")

    def test_an_absurd_row_count_is_rejected(self):
        database = dict(GOOD_MANIFEST["database"])
        database["seed"] = [dict(GOOD_MANIFEST["database"]["seed"][0], rows=10 ** 9)]
        self._rejects(_files(dict(GOOD_MANIFEST, database=database)), stage="database")


if __name__ == "__main__":
    unittest.main()
