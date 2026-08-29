"""The Causeway repository contract: `causeway.json`, version 2.

A manifest declares CAPABILITIES and SAFE INPUTS. What runtime, which file to
launch, which files may be analysed, which may be patched, how to build the
database, and what traffic to replay.

It may not declare the ANSWER. There is no root cause here, no correct
hypothesis, no repair, and no deploy history - and a manifest that tries to
supply one is rejected by name rather than quietly ignored, because a
contract that can hand Causeway the conclusion is not a contract worth
having. Version 1 did exactly that, which is why version 1 is no longer
accepted.

There are no command strings anywhere in this file's vocabulary. Causeway
runs `python <entrypoint>` and nothing else; the schema may only create and
drop tables, indexes and views; seed columns come from a closed set of kinds.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from causeway.repository import database
from causeway.repository.errors import RepositoryRejected

MANIFEST_FILENAME = "causeway.json"
SUPPORTED_VERSIONS = (2,)
SUPPORTED_RUNTIMES = ("python",)
SUPPORTED_ENGINES = ("sqlite",)
SUPPORTED_VERIFICATION = ("latency_p95",)

REQUIRED_TOP_LEVEL = ("version", "service", "runtime", "entrypoint", "sources",
                      "patchable", "workload", "verification", "incident",
                      "database")
REQUIRED_INCIDENT = ("id", "title", "service", "symptom", "detected_at")

# Optional. A probe describes an HTTP request shape and, for each named case,
# the status codes that count as CORRECT behaviour - the acceptance criteria
# for a requested change, not the code that satisfies them. That distinction
# is what keeps this out of ANSWER_KEYS: it says what "done" looks like from
# the outside, the same way workload.json says what traffic looks like: it
# never names a file, a line, or a line of code to write.
PROBE_METHODS = ("GET", "POST")

# Keys that would mean the repository is telling Causeway what to conclude.
ANSWER_KEYS = ("repair_surface", "root_cause", "deploys", "answer",
               "correct_hypothesis", "known_cause", "verdict", "fix")


@dataclass(frozen=True)
class Manifest:
    version: int
    service: str
    runtime: str
    verification: str
    entrypoint: str            # repository-relative
    entrypoint_path: str       # absolute, verified inside the workspace
    sources: Tuple[str, ...]   # repository-relative, analysable
    patchable: Tuple[str, ...] # repository-relative, a fix may touch these
    workload_path: str         # absolute, verified inside the workspace
    schema_path: str           # absolute, verified inside the workspace
    schema_relative: str
    seed: Tuple[Mapping[str, Any], ...]
    engine: str
    incident: Mapping[str, Any]
    probes: Mapping[str, Any]  # {} when the repository declares none


def _reject(reason: str):
    raise RepositoryRejected("manifest", reason)


def _safe_relative_path(workspace: str, relative: Any, field: str) -> str:
    """Resolve `relative` against `workspace` and refuse anything that
    escapes it - an absolute path, a drive letter, or a `..` that walks out,
    however it is spelled. Only ever returns a path that both resolves
    inside the workspace and actually exists."""
    if not isinstance(relative, str) or not relative or relative.strip() != relative:
        _reject("%s must be a non-empty relative path" % field)
    if relative.startswith(("/", "\\")) or ":" in relative or "\x00" in relative:
        _reject("%s must be a relative path, got %r" % (field, relative))

    workspace_real = os.path.realpath(workspace)
    resolved = os.path.realpath(os.path.join(workspace_real, relative))
    try:
        inside = os.path.commonpath([workspace_real, resolved]) == workspace_real
    except ValueError:
        inside = False
    if not inside:
        _reject("%s escapes the repository workspace: %r" % (field, relative))
    if not os.path.isfile(resolved):
        _reject("%s does not exist in the repository: %r" % (field, relative))
    return resolved


def _string_list(workspace: str, raw: Any, field: str) -> Tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        _reject("%s must list at least one repository-relative file" % field)
    for entry in raw:
        _safe_relative_path(workspace, entry, "%s entry" % field)
    return tuple(raw)


def _check_probes(raw: Any) -> Mapping[str, Any]:
    """Validate the optional `probes` section without executing anything.

    Each probe names an HTTP request shape and, per case, the status codes
    that count as correct. This is a specification of observable behaviour -
    like workload.json - never a hint about what code to write.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _reject("probes must be an object of probe name -> request shape")
    for name, probe in raw.items():
        if not isinstance(name, str) or not name:
            _reject("every probe needs a non-empty string name")
        if not isinstance(probe, dict):
            _reject("probe %r must be an object" % name)
        method = probe.get("method")
        if method not in PROBE_METHODS:
            _reject("probe %r method must be one of %s" % (name, ", ".join(PROBE_METHODS)))
        path = probe.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            _reject("probe %r path must be a string beginning with /" % name)
        cases = probe.get("cases")
        if not isinstance(cases, list) or not cases:
            _reject("probe %r must list at least one case" % name)
        for case in cases:
            if not isinstance(case, dict) or not isinstance(case.get("name"), str) \
                    or not case["name"]:
                _reject("every case in probe %r needs a non-empty string name" % name)
            if "body" in case and case["body"] is not None \
                    and not isinstance(case["body"], dict):
                _reject("case %r.%s body must be an object" % (name, case["name"]))
            statuses = case.get("expect_status")
            if (not isinstance(statuses, list) or not statuses
                    or not all(isinstance(s, int) and not isinstance(s, bool)
                              and 100 <= s <= 599 for s in statuses)):
                _reject("case %r.%s must list expect_status as HTTP status "
                        "codes" % (name, case["name"]))
    return raw


def load(workspace: str) -> Manifest:
    path = os.path.join(workspace, MANIFEST_FILENAME)
    if not os.path.isfile(path):
        _reject("the repository has no %s at its root, so it does not follow "
                "the Causeway contract" % MANIFEST_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        _reject("%s could not be read as JSON: %s" % (MANIFEST_FILENAME, exc))

    if not isinstance(raw, dict):
        _reject("%s must contain a JSON object" % MANIFEST_FILENAME)

    # Before anything else: a manifest may not carry the conclusion.
    smuggled = sorted(k for k in raw if k.lower() in ANSWER_KEYS)
    if smuggled:
        _reject("a manifest describes capabilities, not conclusions - remove "
                "%s. Causeway finds hypotheses by reading the source and "
                "settles them by measuring." % ", ".join(smuggled))

    missing = [k for k in REQUIRED_TOP_LEVEL if k not in raw]
    if missing:
        _reject("%s is missing %s" % (MANIFEST_FILENAME, ", ".join(missing)))

    version = raw["version"]
    if version not in SUPPORTED_VERSIONS:
        _reject("manifest version %r is not supported; this Causeway "
                "understands version %s"
                % (version, ", ".join(str(v) for v in SUPPORTED_VERSIONS)))

    runtime = raw["runtime"]
    if runtime not in SUPPORTED_RUNTIMES:
        _reject("runtime %r is not supported; this Causeway can run %s"
                % (runtime, ", ".join(SUPPORTED_RUNTIMES)))

    verification = raw["verification"]
    if verification not in SUPPORTED_VERIFICATION:
        _reject("verification %r is not supported; this Causeway can verify %s"
                % (verification, ", ".join(SUPPORTED_VERIFICATION)))

    if not isinstance(raw["service"], str) or not raw["service"].strip():
        _reject("service must be a non-empty string")

    incident = raw["incident"]
    if not isinstance(incident, dict):
        _reject("incident must be an object")
    incident_missing = [k for k in REQUIRED_INCIDENT if k not in incident]
    if incident_missing:
        _reject("incident is missing %s" % ", ".join(incident_missing))

    entrypoint_path = _safe_relative_path(workspace, raw["entrypoint"], "entrypoint")
    workload_path = _safe_relative_path(workspace, raw["workload"], "workload")
    sources = _string_list(workspace, raw["sources"], "sources")
    patchable = _string_list(workspace, raw["patchable"], "patchable")

    db = raw["database"]
    if not isinstance(db, dict):
        _reject("database must be an object")
    engine = db.get("engine")
    if engine not in SUPPORTED_ENGINES:
        _reject("database engine %r is not supported; this Causeway can build %s"
                % (engine, ", ".join(SUPPORTED_ENGINES)))
    if "schema" not in db or "seed" not in db:
        _reject("database must declare both a schema file and a seed")
    schema_path = _safe_relative_path(workspace, db["schema"], "database.schema")

    # Validate the schema and the seed without writing anything: a repository
    # that would need a statement Causeway will not run is rejected here,
    # before a database file exists.
    with open(schema_path, "r", encoding="utf-8") as handle:
        database.check_schema(handle.read())
    database.check_seed(db["seed"])
    probes = _check_probes(raw.get("probes"))

    return Manifest(
        version=version, service=raw["service"], runtime=runtime,
        verification=verification, entrypoint=raw["entrypoint"],
        entrypoint_path=entrypoint_path, sources=sources, patchable=patchable,
        workload_path=workload_path, schema_path=schema_path,
        schema_relative=db["schema"], seed=tuple(db["seed"]), engine=engine,
        incident=incident, probes=probes,
    )
