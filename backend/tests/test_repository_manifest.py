"""causeway.json validation. Every repository here is a real, disposable
local git checkout (tests.repo_fixtures.local_repo) - no network."""
from __future__ import annotations

import json
import os
import unittest

from causeway.repository.errors import RepositoryRejected
from causeway.repository.manifest import load
from tests.repo_fixtures import local_repo

GOOD_MANIFEST = {
    "version": 1,
    "service": "order-service",
    "runtime": "python",
    "entrypoint": "service.py",
    "fixture": "fixtures/incident-001.json",
    "incident": {
        "id": "INCIDENT-001", "title": "t", "service": "order-service",
        "symptom": "s", "detected_at": "2026-08-28T14:05:00Z",
        "window_seconds": 900, "hot_path_files": ["a.py"],
    },
    "deploys": [
        {"change_id": "A", "sha": "aaa", "branch": "a", "service": "order-service",
         "summary": "s", "deployed_at": "2026-08-28T14:02:54Z",
         "files_changed": 1, "lines_changed": 1, "changed_files": ["a.py"]},
        {"change_id": "B", "sha": "bbb", "branch": "b", "service": "order-service",
         "summary": "s", "deployed_at": "2026-08-28T14:02:18Z",
         "files_changed": 1, "lines_changed": 1, "changed_files": ["a.py"]},
    ],
    "repair_surface": {
        "hypothesis_id": "B", "target": "SCANNING_PREDICATE",
        "operation_type": "replace_predicate", "safe_after": "order_id = ?",
        "description": "d",
    },
}


def _files(manifest=None, extra=None):
    files = {
        "causeway.json": json.dumps(GOOD_MANIFEST if manifest is None else manifest),
        "service.py": "# a fake entrypoint\n",
        "fixtures/incident-001.json": json.dumps(
            {"id": "incident-001", "concurrency": 1, "requests": ["/health"]}),
    }
    if extra:
        files.update(extra)
    return files


class AcceptanceTests(unittest.TestCase):
    def test_a_well_formed_manifest_is_accepted(self):
        with local_repo(_files()) as root:
            manifest = load(root)
            self.assertEqual(manifest.service, "order-service")
            self.assertEqual(manifest.runtime, "python")
            self.assertTrue(os.path.isfile(manifest.entrypoint_path))
            self.assertTrue(os.path.isfile(manifest.fixture_path))
            self.assertEqual([d["change_id"] for d in manifest.deploys], ["A", "B"])

    def test_the_real_demo_repo_is_itself_accepted(self):
        """The actual demo-repo/ directory this project ships must pass its
        own contract - if it didn't, nothing else here would mean anything."""
        with local_repo(copy_demo=True) as root:
            manifest = load(root)
            self.assertEqual(manifest.service, "order-service")


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
        self._rejects({"causeway.json": "{not json", "service.py": "x",
                       "fixtures/incident-001.json": "{}"})

    def test_a_non_object_manifest_is_rejected(self):
        self._rejects({"causeway.json": json.dumps([1, 2, 3])})

    def test_a_missing_required_top_level_field_is_rejected(self):
        bad = dict(GOOD_MANIFEST)
        del bad["fixture"]
        self._rejects(_files(bad))

    def test_an_unsupported_version_is_rejected(self):
        bad = dict(GOOD_MANIFEST, version=2)
        self._rejects(_files(bad))

    def test_a_missing_version_is_rejected(self):
        bad = dict(GOOD_MANIFEST)
        del bad["version"]
        self._rejects(_files(bad))

    def test_an_unknown_runtime_is_rejected(self):
        bad = dict(GOOD_MANIFEST, runtime="node")
        self._rejects(_files(bad))

    def test_path_traversal_in_entrypoint_is_rejected(self):
        bad = dict(GOOD_MANIFEST, entrypoint="../../etc/passwd")
        self._rejects(_files(bad))

    def test_an_absolute_entrypoint_path_is_rejected(self):
        bad = dict(GOOD_MANIFEST, entrypoint="/etc/passwd")
        self._rejects(_files(bad))

    def test_a_windows_drive_absolute_entrypoint_path_is_rejected(self):
        bad = dict(GOOD_MANIFEST, entrypoint="C:/Windows/System32/cmd.exe")
        self._rejects(_files(bad))

    def test_path_traversal_in_fixture_is_rejected(self):
        bad = dict(GOOD_MANIFEST, fixture="../outside.json")
        self._rejects(_files(bad))

    def test_a_missing_entrypoint_file_is_rejected(self):
        bad = dict(GOOD_MANIFEST, entrypoint="does_not_exist.py")
        self._rejects(_files(bad))

    def test_a_missing_fixture_file_is_rejected(self):
        bad = dict(GOOD_MANIFEST, fixture="fixtures/does_not_exist.json")
        self._rejects(_files(bad))

    def test_fewer_than_two_deploys_is_rejected(self):
        bad = dict(GOOD_MANIFEST, deploys=[GOOD_MANIFEST["deploys"][0]])
        self._rejects(_files(bad))

    def test_a_deploy_missing_a_required_field_is_rejected(self):
        deploy = dict(GOOD_MANIFEST["deploys"][0])
        del deploy["sha"]
        bad = dict(GOOD_MANIFEST, deploys=[deploy, GOOD_MANIFEST["deploys"][1]])
        self._rejects(_files(bad))

    def test_duplicate_change_ids_are_rejected(self):
        deploy = dict(GOOD_MANIFEST["deploys"][1], change_id="A")
        bad = dict(GOOD_MANIFEST, deploys=[GOOD_MANIFEST["deploys"][0], deploy])
        self._rejects(_files(bad))

    def test_an_unsupported_repair_operation_type_is_rejected(self):
        surface = dict(GOOD_MANIFEST["repair_surface"], operation_type="run_shell_command")
        bad = dict(GOOD_MANIFEST, repair_surface=surface)
        self._rejects(_files(bad))

    def test_a_repair_surface_pointing_at_an_undeclared_hypothesis_is_rejected(self):
        surface = dict(GOOD_MANIFEST["repair_surface"], hypothesis_id="Z")
        bad = dict(GOOD_MANIFEST, repair_surface=surface)
        self._rejects(_files(bad))

    def test_a_repair_surface_missing_a_required_field_is_rejected(self):
        surface = dict(GOOD_MANIFEST["repair_surface"])
        del surface["safe_after"]
        bad = dict(GOOD_MANIFEST, repair_surface=surface)
        self._rejects(_files(bad))


if __name__ == "__main__":
    unittest.main()
