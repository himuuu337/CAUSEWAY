"""The standard repository path: a normal public GitHub repository that does
not ship causeway.json.

causeway.json is required for exactly one thing: the controlled causal
experiment (a repeatable workload replayed against a database built from the
repository's own schema, so a hypothesis can be measured rather than asserted).
Nothing else in Causeway needs it. A repository that does not opt into that
contract can still be read, and a change can still be proposed and validated
against it - there is simply no controlled experiment and no guaranteed way
to run it, so verification is whatever is actually available: a syntax check
always, real tests only if Causeway can tell they do not need dependencies it
has not installed.

This module never launches anything. It reads files and decides whether the
repository is a kind Causeway's prototype can read at all (Python, for the
hackathon) - both file operations, neither one execution.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from causeway.repository.errors import RepositoryRejected
from causeway.repository.git import ClonedRepo
from causeway.repository.urlcheck import RepoRef

# Directories never walked: version control metadata, virtualenvs, caches,
# and anything else that is not the repository's own source.
_SKIP_DIRS = frozenset((
    ".git", "venv", ".venv", "env", ".env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".tox", ".idea", ".vscode", "dist",
    "build", "site-packages", ".eggs",
))

_PY_PROJECT_MARKERS = ("requirements.txt", "pyproject.toml", "setup.py",
                       "Pipfile", "poetry.lock", "setup.cfg")
_ENTRYPOINT_NAMES = ("app.py", "main.py", "manage.py", "wsgi.py", "asgi.py",
                     "run.py", "server.py")

# Bounded context: a standard-path planner is shown a small, chosen subset of
# the repository's own files, never the whole repository blindly.
MAX_FILES = 12
MAX_FILE_CHARS = 6000
MAX_TOTAL_CHARS = 40000
MAX_FILE_BYTES_TO_READ = 200_000   # skip absurdly large generated files outright

_STOPWORDS = frozenset((
    "this", "that", "with", "from", "have", "does", "when", "what", "where",
    "only", "into", "your", "will", "should", "make", "sure", "fixed", "which",
))


@dataclass(frozen=True)
class StandardRepositoryContext:
    """A repository read without a manifest: detected language, a bounded,
    scored selection of its own source, and nothing manufactured - no
    database, no workload, no hypothesis. Whatever verification is possible
    is decided at investigation time from what is actually here."""

    owner: str
    name: str
    url: str
    commit_sha: str
    workspace: str
    language: str                        # "python" - the only one detected today
    entrypoint: str                      # best-guess; "" if none was recognisable
    sources: Tuple[str, ...]             # bounded, scored selection - analysable
    patchable: Tuple[str, ...]           # the same files - this path has no other list
    tests_detected: bool
    tests_note: str
    all_python_files: Tuple[str, ...]    # every .py file found, for display only
    cloned: ClonedRepo = field(repr=False, compare=False)

    def cleanup(self) -> None:
        self.cloned.cleanup()

    def as_event(self) -> dict:
        return {
            "owner": self.owner, "name": self.name, "url": self.url,
            "commit_sha": self.commit_sha, "service": self.name,
            "runtime": self.language, "verification": "none",
            "entrypoint": self.entrypoint, "sources": list(self.sources),
            "patchable": list(self.patchable),
            "database": None, "workload": None, "contract": "standard",
            "tests_detected": self.tests_detected, "tests_note": self.tests_note,
            "all_python_files": len(self.all_python_files),
        }


def _walk_python_files(workspace: str) -> List[str]:
    found = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.endswith(".egg-info")]
        for name in files:
            if name.endswith(".py"):
                relative = os.path.relpath(os.path.join(root, name), workspace)
                found.append(relative.replace(os.sep, "/"))
    return sorted(found)


def detect_python(workspace: str) -> bool:
    """Is this a repository Causeway's prototype can read at all?

    File presence only - never an import, never a subprocess. A hackathon
    scope: Python, recognised the way a person skimming the repo tree would.
    """
    for marker in _PY_PROJECT_MARKERS:
        if os.path.isfile(os.path.join(workspace, marker)):
            return True
    return bool(_walk_python_files(workspace))


def _instruction_words(instruction: str) -> Sequence[str]:
    words = []
    for raw in (instruction or "").lower().split():
        word = raw.strip(".,!?()[]{}:;\"'`")
        if len(word) > 3 and word not in _STOPWORDS:
            words.append(word)
    return words


def _score(relative: str, content: str, words: Sequence[str]) -> float:
    name = os.path.basename(relative).lower()
    score = 0.0
    if name in _ENTRYPOINT_NAMES:
        score += 100.0
    if name == "__init__.py":
        score -= 10.0
    if "/" not in relative:
        score += 20.0                          # root-level files first
    lowered_path = relative.lower()
    if "test" in lowered_path:
        score -= 25.0                          # readable, but rarely the edit target
    if "migrations" in lowered_path or "vendor" in lowered_path:
        score -= 40.0
    lowered_content = content.lower()
    for word in words:
        if word in lowered_content or word in lowered_path:
            score += 8.0
    score -= len(content) / 20000.0            # mild penalty for very large files
    return score


def discover_sources(workspace: str, instruction: str = ""
                     ) -> Tuple[List[str], Dict[str, str], List[str]]:
    """A bounded, scored selection of the repository's own Python source.

    Returns (chosen, contents, all_files): `chosen` is the ordered list of
    relative paths a planner will actually be shown (and the only ones it may
    propose an edit to); `contents` maps every readable candidate to its text,
    for scoring and for later re-reading; `all_files` is every .py file found,
    for display only - Causeway is not silently ignoring the rest, it is
    bounding what one prompt carries.
    """
    all_files = _walk_python_files(workspace)
    words = _instruction_words(instruction)
    contents: Dict[str, str] = {}
    scored: List[Tuple[float, str]] = []
    for relative in all_files:
        path = os.path.join(workspace, relative)
        try:
            if os.path.getsize(path) > MAX_FILE_BYTES_TO_READ:
                continue
            with io.open(path, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
        except OSError:
            continue
        contents[relative] = text
        scored.append((_score(relative, text, words), relative))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    chosen: List[str] = []
    total = 0
    for _, relative in scored:
        if len(chosen) >= MAX_FILES:
            break
        text = contents[relative]
        cost = min(len(text), MAX_FILE_CHARS)
        if chosen and total + cost > MAX_TOTAL_CHARS:
            continue
        chosen.append(relative)
        total += cost
    return sorted(chosen), contents, all_files


def guess_entrypoint(sources: Sequence[str]) -> str:
    for name in _ENTRYPOINT_NAMES:
        for relative in sources:
            if relative == name or relative.endswith("/" + name):
                return relative
    return ""


def detect_tests(workspace: str, all_python_files: Sequence[str]) -> Tuple[bool, str]:
    for relative in all_python_files:
        base = os.path.basename(relative)
        if base.startswith("test_") or base.endswith("_test.py") or "/tests/" in relative:
            return True, (
                "test files were found, but Causeway does not install this "
                "repository's dependencies or execute its tests automatically - "
                "running untrusted, arbitrary test code with unknown "
                "requirements is out of scope for this path")
    return False, "no test files were found in this repository"


def load_standard(cloned: ClonedRepo, ref: RepoRef, instruction: str = ""
                  ) -> StandardRepositoryContext:
    """Read a cloned workspace without a manifest. Rejects only when the
    repository is not a kind Causeway's prototype can read at all - never
    because it lacks causeway.json."""
    if not detect_python(cloned.path):
        raise RepositoryRejected(
            "analysis",
            "no supported language was detected in this repository. Causeway's "
            "prototype reads Python repositories (requirements.txt, "
            "pyproject.toml, setup.py, or .py files) - none of those were "
            "found at %s" % ref.url)

    chosen, _contents, all_files = discover_sources(cloned.path, instruction)
    if not chosen:
        raise RepositoryRejected(
            "analysis",
            "Python was detected but no readable .py source file was found to "
            "analyse (every candidate was empty, unreadable, or too large)")

    entrypoint = guess_entrypoint(chosen)
    tests_detected, tests_note = detect_tests(cloned.path, all_files)

    return StandardRepositoryContext(
        owner=ref.owner, name=ref.name, url=ref.url, commit_sha=cloned.commit_sha,
        workspace=cloned.path, language="python", entrypoint=entrypoint,
        sources=tuple(chosen), patchable=tuple(chosen),
        tests_detected=tests_detected, tests_note=tests_note,
        all_python_files=tuple(all_files), cloned=cloned,
    )
