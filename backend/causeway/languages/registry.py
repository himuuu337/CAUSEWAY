"""Every adapter Causeway ships, and the one detection pass that walks a
repository once and scores each of them against what it found.

Detection never executes anything - it lists directories and matches
filenames and extensions, nothing more. A repository may be, and often is,
more than one language at once; this module never picks a single "the"
language and discards the rest, it ranks them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from causeway.languages import adapters as _adapters
from causeway.languages.base import LanguageAdapter

# Directories never walked, anywhere in this package: version control
# metadata, dependency/package caches, build output, and anything else that
# is not a repository's own source. Shared by detection AND source
# selection so the two can never disagree about what "the repository" means.
SKIP_DIRS = frozenset((
    ".git", "node_modules", "vendor", "dist", "build", "target", "coverage",
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".tox", ".idea", ".vscode", "site-packages", ".eggs", "bin", "obj",
    ".gradle", ".mvn", "out", ".next", ".nuxt", "cmake-build-debug",
    "cmake-build-release", ".cargo", ".cache", "__snapshots__",
))

# Substrings, checked case-insensitively against a repository-relative path.
# Shared by source selection (a file matching one of these is never even
# read into a prompt) and causeway.patch.validator (a patch may never touch
# one, as a backstop independent of what was or was not offered to a
# planner in the first place).
DENY_PATH_SUBSTRINGS = (".env", ".git/", ".git\\", "secret", "credential",
                        "token", ".pem", ".key", "id_rsa")


def is_denied_path(relative_path: str) -> bool:
    lowered = relative_path.lower().replace("\\", "/")
    return any(bad in lowered for bad in DENY_PATH_SUBSTRINGS)

ADAPTERS: Tuple[LanguageAdapter, ...] = (
    _adapters.PythonAdapter(),
    _adapters.JavaScriptAdapter(),
    _adapters.TypeScriptAdapter(),
    _adapters.JavaAdapter(),
    _adapters.GoAdapter(),
    _adapters.CAdapter(),
    _adapters.CppAdapter(),
    _adapters.CSharpAdapter(),
    _adapters.RustAdapter(),
)

_BY_ID = {adapter.id: adapter for adapter in ADAPTERS}

# A manifest marker at the repository root is a much stronger signal than an
# incidental file with a matching extension somewhere in the tree (a stray
# .py script in an otherwise-JavaScript repository should not outrank
# package.json). Not a threshold anything is judged against - just how the
# two signals are weighted against each other when ranking.
_MANIFEST_WEIGHT = 1000


def adapter_for(language_id: str) -> Optional[LanguageAdapter]:
    return _BY_ID.get(language_id)


@dataclass(frozen=True)
class LanguageDetection:
    primary: str                 # "" if nothing was detected
    detected: Tuple[str, ...]    # every language found, primary first
    counts: Mapping[str, int]    # language id -> matched source file count

    def as_dict(self) -> dict:
        return {"primary": self.primary, "detected": list(self.detected),
               "counts": dict(self.counts)}


def walk_files(workspace: str) -> List[str]:
    """Every file in the repository, repository-relative, skipping only
    SKIP_DIRS. Used by detection and by source selection - the one place
    either of them touches the filesystem to enumerate what is here."""
    found: List[str] = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in files:
            relative = os.path.relpath(os.path.join(root, name), workspace)
            found.append(relative.replace(os.sep, "/"))
    return sorted(found)


def detect_languages(workspace: str, all_files: Sequence[str] = None) -> LanguageDetection:
    """Rank every adapter against what is actually in the repository.
    `all_files` may be passed in when the caller has already walked the
    tree (source selection always has), so detection never walks twice."""
    try:
        root_names = os.listdir(workspace)
    except OSError:
        root_names = []
    files = list(all_files) if all_files is not None else walk_files(workspace)

    counts: Dict[str, int] = {}
    manifests = set()
    for adapter in ADAPTERS:
        count = sum(1 for f in files if adapter.matches_file(f))
        if count:
            counts[adapter.id] = count
        if adapter.matches_manifest(root_names):
            manifests.add(adapter.id)

    scored = []
    for adapter in ADAPTERS:
        count = counts.get(adapter.id, 0)
        if count == 0 and adapter.id not in manifests:
            continue
        score = count + (_MANIFEST_WEIGHT if adapter.id in manifests else 0)
        scored.append((score, adapter.id))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))

    detected = tuple(language for _score, language in scored)
    primary = detected[0] if detected else ""
    return LanguageDetection(primary=primary, detected=detected, counts=counts)
