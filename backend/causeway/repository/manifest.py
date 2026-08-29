"""causeway.json - the one file that tells Causeway what it is allowed to run.

A repository "supports the Causeway demo contract" if and only if this file
exists at its root, parses as JSON, and passes every check below. Nothing
about accepting a repository comes from guessing: `entrypoint` and `fixture`
are resolved and checked to stay inside the cloned workspace, `runtime` is
checked against a whitelist, and every required field is a plain string,
number or list Causeway reads from - never a command, a flag or a shell
fragment for Causeway to invoke on its own initiative. A repository that
fails any check here never reaches a subprocess at all.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from causeway.repository.errors import RepositoryRejected

MANIFEST_FILENAME = "causeway.json"
SUPPORTED_VERSIONS = (1,)
ALLOWED_RUNTIMES = ("python",)
ALLOWED_OPERATION_TYPES = ("replace_predicate",)

REQUIRED_TOP_LEVEL = ("version", "service", "runtime", "entrypoint", "fixture",
                     "incident", "deploys", "repair_surface")
REQUIRED_INCIDENT = ("id", "title", "service", "symptom", "detected_at",
                    "window_seconds", "hot_path_files")
REQUIRED_DEPLOY = ("change_id", "sha", "branch", "service", "summary",
                  "deployed_at", "files_changed", "lines_changed", "changed_files")
REQUIRED_REPAIR_SURFACE = ("hypothesis_id", "target", "operation_type",
                          "safe_after", "description")


@dataclass(frozen=True)
class Manifest:
    version: int
    service: str
    runtime: str
    entrypoint_path: str      # absolute path, already verified inside the workspace
    fixture_path: str         # absolute path, already verified inside the workspace
    incident: Mapping[str, Any]
    deploys: Tuple[Mapping[str, Any], ...]
    repair_surface: Mapping[str, Any]


def _safe_relative_path(workspace: str, relative: Any, field: str) -> str:
    """Resolve `relative` against `workspace` and refuse anything that
    escapes it - an absolute path, a drive letter, or a `..` that walks out,
    however it is spelled. Only ever returns a path that both resolves
    inside the workspace and actually exists."""
    if not isinstance(relative, str) or not relative or relative.strip() != relative:
        raise RepositoryRejected("manifest", "%s must be a non-empty relative path" % field)
    if relative.startswith(("/", "\\")) or ":" in relative or "\x00" in relative:
        raise RepositoryRejected(
            "manifest", "%s must be a relative path, got %r" % (field, relative))

    workspace_real = os.path.realpath(workspace)
    resolved = os.path.realpath(os.path.join(workspace_real, relative))
    try:
        inside = os.path.commonpath([workspace_real, resolved]) == workspace_real
    except ValueError:
        inside = False
    if not inside:
        raise RepositoryRejected(
            "manifest", "%s escapes the repository workspace: %r" % (field, relative))
    if not os.path.isfile(resolved):
        raise RepositoryRejected(
            "manifest", "%s does not exist in the repository: %r" % (field, relative))
    return resolved


def _require_fields(obj: Any, fields: Tuple[str, ...], what: str) -> None:
    if not isinstance(obj, dict):
        raise RepositoryRejected("manifest", "%s must be an object" % what)
    missing = [f for f in fields if f not in obj]
    if missing:
        raise RepositoryRejected("manifest", "%s is missing %s" % (what, ", ".join(missing)))


def load(workspace: str) -> Manifest:
    """Read and validate causeway.json at the root of a cloned workspace."""
    path = os.path.join(workspace, MANIFEST_FILENAME)
    if not os.path.isfile(path):
        raise RepositoryRejected(
            "manifest",
            "no %s at the repository root - this repository does not contain "
            "a supported Causeway demo configuration" % MANIFEST_FILENAME)

    with open(path, "r", encoding="utf-8") as handle:
        raw_text = handle.read()
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RepositoryRejected(
            "manifest", "%s is not valid JSON: %s" % (MANIFEST_FILENAME, exc))

    _require_fields(raw, REQUIRED_TOP_LEVEL, MANIFEST_FILENAME)

    version = raw["version"]
    if version not in SUPPORTED_VERSIONS:
        raise RepositoryRejected(
            "manifest", "unsupported manifest version %r (Causeway supports %s)"
            % (version, ", ".join(str(v) for v in SUPPORTED_VERSIONS)))

    runtime = raw["runtime"]
    if runtime not in ALLOWED_RUNTIMES:
        raise RepositoryRejected(
            "manifest", "unsupported runtime %r (Causeway supports %s)"
            % (runtime, ", ".join(ALLOWED_RUNTIMES)))

    service = raw["service"]
    if not isinstance(service, str) or not service.strip():
        raise RepositoryRejected("manifest", "service must be a non-empty string")

    entrypoint_path = _safe_relative_path(workspace, raw["entrypoint"], "entrypoint")
    fixture_path = _safe_relative_path(workspace, raw["fixture"], "fixture")

    _require_fields(raw["incident"], REQUIRED_INCIDENT, "incident")

    deploys = raw["deploys"]
    if not isinstance(deploys, list) or len(deploys) < 2:
        raise RepositoryRejected("manifest", "deploys must list at least two candidates")
    for deploy in deploys:
        _require_fields(deploy, REQUIRED_DEPLOY, "each entry in deploys")
    change_ids = [d["change_id"] for d in deploys]
    if len(set(change_ids)) != len(change_ids):
        raise RepositoryRejected("manifest", "deploys must have distinct change_id values")

    surface = raw["repair_surface"]
    _require_fields(surface, REQUIRED_REPAIR_SURFACE, "repair_surface")
    if surface["operation_type"] not in ALLOWED_OPERATION_TYPES:
        raise RepositoryRejected(
            "manifest", "unsupported repair operation type %r" % surface["operation_type"])
    if surface["hypothesis_id"] not in change_ids:
        raise RepositoryRejected(
            "manifest", "repair_surface.hypothesis_id %r is not one of the declared deploys"
            % surface["hypothesis_id"])
    if not isinstance(surface["target"], str) or not surface["target"]:
        raise RepositoryRejected("manifest", "repair_surface.target must be a non-empty string")
    if not isinstance(surface["safe_after"], str) or not surface["safe_after"]:
        raise RepositoryRejected("manifest", "repair_surface.safe_after must be a non-empty string")

    return Manifest(version=version, service=service, runtime=runtime,
                    entrypoint_path=entrypoint_path, fixture_path=fixture_path,
                    incident=dict(raw["incident"]),
                    deploys=tuple(dict(d) for d in deploys),
                    repair_surface=dict(surface))
