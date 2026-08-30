"""GitHub repository ingestion - the front door onto a real investigation.

    GitHub URL -> validate -> clone (disposable, isolated workspace) ->
    causeway.json v2 validated -> database built from the repository's own
    schema and seed -> hypotheses READ OUT OF THE REPOSITORY'S SOURCE ->
    RepositoryContext

Nothing here executes a line of the cloned repository. The entrypoint this
module resolves is later launched by causeway.sandbox as a subprocess, never
imported, and only after every check here has passed. A repository that fails
any check never reaches the sandbox, and Causeway never substitutes its own
bundled demo in its place: a repository URL either produces a real
investigation of that repository, or a visible rejection.

What changed in Milestone 6's second half is where hypotheses come from. They
are no longer read out of the manifest - the manifest is forbidden from
carrying them - but detected in the repository's own source by
causeway.analysis.detectors, which learns which columns are indexed from the
repository's own schema. Causeway has to find the suspects itself, and has to
settle between them by measuring.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence, Tuple

from causeway.analysis import detectors, detectors_pool
from causeway.analysis.hypothesis import CodeHypothesis
from causeway.repository import database, git, manifest, standard, urlcheck
from causeway.repository.errors import RepositoryRejected
from causeway.repository.git import CLONE_TIMEOUT, ClonedRepo
from causeway.repository.manifest import MANIFEST_FILENAME, Manifest
from causeway.repository.standard import StandardRepositoryContext
from causeway.repository.urlcheck import RepoRef

__all__ = ["RepositoryRejected", "RepoRef", "ClonedRepo", "Manifest",
           "RepositoryContext", "StandardRepositoryContext", "validate_url",
           "clone", "load", "has_manifest", "acquire"]

validate_url = urlcheck.validate_url


def clone(ref: RepoRef, timeout: float = CLONE_TIMEOUT, source: str = None) -> ClonedRepo:
    return git.clone(ref, timeout=timeout, source=source)


def has_manifest(workspace: str) -> bool:
    """Whether this repository opted into the causeway.json contract - the
    controlled causal experiment. Its absence is not a rejection: it is the
    signal to read the repository the standard way instead."""
    return os.path.isfile(os.path.join(workspace, MANIFEST_FILENAME))


def _validate_workload(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise RepositoryRejected("manifest", "the workload file is not a JSON object")
    if not isinstance(raw.get("id"), str) or not raw["id"]:
        raise RepositoryRejected("manifest", "the workload is missing a string id")
    requests = raw.get("requests")
    if (not isinstance(requests, list) or not requests
            or not all(isinstance(r, str) for r in requests)):
        raise RepositoryRejected(
            "manifest", "the workload must list at least one request path")
    for request in requests:
        if not request.startswith("/"):
            raise RepositoryRejected(
                "manifest", "workload requests must be paths beginning with / - got %r"
                % request[:40])
    concurrency = raw.get("concurrency")
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise RepositoryRejected(
            "manifest", "the workload's concurrency must be a positive integer")
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
    verification: str
    entrypoint: str                       # repository-relative
    entrypoint_path: str                  # absolute, inside the workspace
    sources: Tuple[str, ...]
    patchable: Tuple[str, ...]
    incident: Mapping[str, Any]
    workload: Mapping[str, Any]
    hypotheses: Tuple[CodeHypothesis, ...]
    probes: Mapping[str, Any]             # {} when the repository declares none
    database_path: str                    # the repository's OWN database
    work_db: str
    database_info: Mapping[str, Any]
    cloned: ClonedRepo = field(repr=False, compare=False)

    @property
    def testable(self) -> Tuple[CodeHypothesis, ...]:
        return tuple(h for h in self.hypotheses if h.testable)

    def hypothesis(self, hypothesis_id: str):
        for candidate in self.hypotheses:
            if candidate.id == hypothesis_id:
                return candidate
        return None

    def cleanup(self) -> None:
        self.cloned.cleanup()

    def as_event(self) -> dict:
        """Everything safe and useful to show about the loaded repository - no
        fabricated history, only what was actually validated or detected."""
        return {
            "owner": self.owner, "name": self.name, "url": self.url,
            "commit_sha": self.commit_sha, "service": self.service,
            "runtime": self.runtime, "verification": self.verification,
            "entrypoint": self.entrypoint, "sources": list(self.sources),
            "patchable": list(self.patchable),
            "database": {"engine": "sqlite",
                         "tables": dict(self.database_info.get("tables", {})),
                         "bytes": self.database_info.get("bytes", 0)},
            "workload": {"id": self.workload["id"],
                         "requests": len(self.workload["requests"]),
                         "concurrency": self.workload["concurrency"]},
            "contract": "causeway",
        }


def load(cloned: ClonedRepo, ref: RepoRef) -> RepositoryContext:
    """Validate a cloned workspace, build its database, and read its source."""
    spec = manifest.load(cloned.path)

    with open(spec.workload_path, "r", encoding="utf-8") as handle:
        try:
            workload_raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise RepositoryRejected(
                "manifest", "the workload file is not valid JSON: %s" % exc)
    workload = _validate_workload(workload_raw)

    # The repository's own database, built from its own schema and seed, into
    # the same disposable workdir the clone lives in. Causeway's bundled
    # TEMPLATE_DB is never touched or consulted on this path.
    data_dir = os.path.join(cloned.workdir, "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(spec.schema_path, "r", encoding="utf-8") as handle:
        schema_sql = handle.read()
    info = database.build(schema_sql, spec.seed, os.path.join(data_dir, "app.db"))

    hypotheses = tuple(detectors.scan_repository(
        cloned.path, spec.schema_relative, spec.sources))
    hypotheses += tuple(detectors_pool.scan_repository(cloned.path, spec.sources))
    # A repository must offer Causeway SOMETHING to work with: either a
    # testable hypothesis for a diagnosis, or a declared probe an
    # instruction-driven change can be verified against. Neither is required
    # of both at once - a repository can ship only one and still be a real
    # investigation target for the mode it supports.
    if not hypotheses and not spec.probes:
        raise RepositoryRejected(
            "analysis",
            "no testable hypothesis was found in %s, and the manifest "
            "declares no verification probes. Causeway's prototype detects a "
            "narrow set of patterns; this repository does not contain "
            "anything it can experiment on or verify a change against."
            % ", ".join(spec.sources))

    return RepositoryContext(
        owner=ref.owner, name=ref.name, url=ref.url, commit_sha=cloned.commit_sha,
        workspace=cloned.path, service=spec.service, runtime=spec.runtime,
        verification=spec.verification, entrypoint=spec.entrypoint,
        entrypoint_path=spec.entrypoint_path, sources=spec.sources,
        patchable=spec.patchable, incident=spec.incident, workload=workload,
        hypotheses=hypotheses, probes=spec.probes, database_path=info["path"],
        work_db=os.path.join(data_dir, "work.db"), database_info=info,
        cloned=cloned,
    )


def acquire(repository_url: str, instruction: str = ""):
    """Validate, clone, and load - the front door for BOTH repository paths.

    causeway.json is checked for only after the clone exists, and its
    presence is what decides which of the two loaders runs - never whether
    the repository is accepted at all:

        present      the causeway.json v2 contract: `load`, a database built
                     from the repository's own schema, hypotheses read out
                     of its own source, a controlled causal experiment.
        absent       `standard.load_standard`: no manifest, no database, no
                     workload - a detected language, a bounded, scored
                     selection of the repository's own source, and whatever
                     verification is actually available for it.

    Yields lifecycle events; the last item is the loaded context (a
    RepositoryContext or a StandardRepositoryContext) or None after a
    `repository_rejected` event, so a caller can tell the two apart without
    inspecting event dicts. Nothing before a `repository_loaded` event
    touches a sandbox.
    """
    yield {"type": "repository_validating", "url": repository_url}
    try:
        ref = validate_url(repository_url)
    except RepositoryRejected as exc:
        yield {"type": "repository_rejected", "stage": exc.stage, "reason": exc.reason}
        yield None
        return

    yield {"type": "repository_cloning", "owner": ref.owner, "name": ref.name,
           "url": ref.url}
    try:
        cloned = clone(ref)
    except RepositoryRejected as exc:
        yield {"type": "repository_rejected", "stage": exc.stage, "reason": exc.reason}
        yield None
        return

    try:
        if has_manifest(cloned.path):
            context = load(cloned, ref)
        else:
            context = standard.load_standard(cloned, ref, instruction)
    except RepositoryRejected as exc:
        cloned.cleanup()
        yield {"type": "repository_rejected", "stage": exc.stage, "reason": exc.reason}
        yield None
        return

    yield dict({"type": "repository_loaded"}, **context.as_event())
    yield context
