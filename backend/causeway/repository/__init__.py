"""GitHub repository ingestion - the narrow, whitelisted front door onto the
existing causal investigation.

    GitHub URL -> validate -> clone (disposable, isolated workspace) ->
    causeway.json validated -> RepositoryContext

Nothing here ever executes a line of the cloned repository. The entrypoint
this module resolves is later launched by causeway.sandbox.runner.Sandbox
exactly like the bundled demo service is - a subprocess, never an import -
and only after every check in causeway.repository.manifest has passed. A
repository that fails any check here never reaches the sandbox at all, and
Causeway never falls back to the bundled demo fixture in its place: a
repository URL either produces a real investigation of that repository, or a
visible rejection.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from causeway.repository import git, manifest, urlcheck
from causeway.repository.errors import RepositoryRejected
from causeway.repository.git import CLONE_TIMEOUT, ClonedRepo
from causeway.repository.manifest import Manifest
from causeway.repository.urlcheck import RepoRef
from causeway.sandbox import repair as repairmod

__all__ = ["RepositoryRejected", "RepoRef", "ClonedRepo", "Manifest",
          "RepositoryContext", "validate_url", "clone", "load"]

validate_url = urlcheck.validate_url


def clone(ref: RepoRef, timeout: float = CLONE_TIMEOUT, source: str = None) -> ClonedRepo:
    return git.clone(ref, timeout=timeout, source=source)


def _validate_fixture(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise RepositoryRejected("manifest", "the fixture file is not a JSON object")
    if not isinstance(raw.get("id"), str) or not raw["id"]:
        raise RepositoryRejected("manifest", "the fixture is missing a string id")
    requests = raw.get("requests")
    if (not isinstance(requests, list) or not requests
            or not all(isinstance(r, str) for r in requests)):
        raise RepositoryRejected(
            "manifest", "the fixture must list at least one request path")
    concurrency = raw.get("concurrency")
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise RepositoryRejected(
            "manifest", "the fixture's concurrency must be a positive integer")
    return raw


@dataclass(frozen=True)
class RepositoryContext:
    owner: str
    name: str
    url: str
    commit_sha: str
    workspace: str
    service: str
    runtime: str
    entrypoint_path: str
    fixture: Mapping[str, Any]
    incident_record: Mapping[str, Any]                    # {"incident": ..., "deploys": [...]}
    repair_surfaces: Mapping[str, Mapping[str, dict]]
    cloned: ClonedRepo = field(repr=False, compare=False)

    def cleanup(self) -> None:
        self.cloned.cleanup()

    def as_event(self) -> dict:
        """Everything safe and useful to show about the loaded repository -
        no fabricated history, only what was actually validated above."""
        candidates = [{"change_id": d["change_id"], "branch": d["branch"],
                      "summary": d["summary"]} for d in self.incident_record["deploys"]]
        return {
            "owner": self.owner, "name": self.name, "url": self.url,
            "commit_sha": self.commit_sha, "service": self.service,
            "runtime": self.runtime, "candidates": candidates,
        }


def load(cloned: ClonedRepo, ref: RepoRef) -> RepositoryContext:
    """Validate a cloned workspace against the Causeway demo contract and
    build the context the orchestrator runs the investigation from."""
    manifest_obj = manifest.load(cloned.path)

    with open(manifest_obj.fixture_path, "r", encoding="utf-8") as handle:
        try:
            fixture_raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise RepositoryRejected(
                "manifest", "the fixture file is not valid JSON: %s" % exc)
    fixture = _validate_fixture(fixture_raw)

    surface = manifest_obj.repair_surface
    entrypoint_path = manifest_obj.entrypoint_path
    target = surface["target"]
    repair_surfaces = {
        surface["hypothesis_id"]: {
            target: {
                "operation_type": surface["operation_type"],
                "safe_after": surface["safe_after"],
                "description": surface["description"],
                # 'current' is read from the actual cloned file the moment it
                # is asked for, never trusted from the manifest itself - the
                # same rule causeway.sandbox.repair already enforces for the
                # bundled demo's own hardcoded surfaces.
                "current": (lambda p=entrypoint_path, t=target:
                           repairmod.read_current_from_file(p, t)),
            },
        },
    }

    return RepositoryContext(
        owner=ref.owner, name=ref.name, url=ref.url, commit_sha=cloned.commit_sha,
        workspace=cloned.path, service=manifest_obj.service, runtime=manifest_obj.runtime,
        entrypoint_path=entrypoint_path, fixture=fixture,
        incident_record={"incident": manifest_obj.incident,
                         "deploys": list(manifest_obj.deploys)},
        repair_surfaces=repair_surfaces, cloned=cloned,
    )
