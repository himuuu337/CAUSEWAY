"""The standard repository path: a normal public GitHub repository that does
not ship causeway.json.

causeway.json is required for exactly one thing: the controlled causal
experiment (a repeatable workload replayed against a database built from the
repository's own schema, so a hypothesis can be measured rather than
asserted). Nothing else in Causeway needs it. A repository that does not opt
into that contract can still be read, and a change can still be proposed and
validated against it - there is simply no controlled experiment and no
guaranteed way to run it, so verification is whatever is actually available
for whatever language the repository turns out to be written in.

Language detection and adapter selection live in causeway.languages; this
module is what applies that to one repository: which languages are here,
which of the repository's own files are worth showing a planner (bounded,
scored, never the whole tree), and what the best guess at an entrypoint is.
It never launches anything, and never runs a line of the repository's own
code - both file operations, neither one execution.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Sequence, Tuple

from causeway.languages import LanguageDetection, detect_languages
from causeway.languages.registry import walk_files
from causeway.repository.errors import RepositoryRejected
from causeway.repository.git import ClonedRepo
from causeway.repository.urlcheck import RepoRef

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

_TEST_MARKERS = ("test_", "_test.", ".test.", ".spec.", "test.go", "_spec.rb")


@dataclass(frozen=True)
class StandardRepositoryContext:
    """A repository read without a manifest: languages detected, a bounded,
    scored selection of its own source, and nothing manufactured - no
    database, no workload, no hypothesis. Whatever verification is possible
    is decided at investigation time from what is actually here."""

    owner: str
    name: str
    url: str
    commit_sha: str
    workspace: str
    primary_language: str                # "" only if nothing was detected (never loaded)
    detected_languages: Tuple[str, ...]   # every language found, primary first
    language_counts: Mapping[str, int]    # language id -> matched source file count
    entrypoint: str                      # best-guess; "" if none was recognisable
    sources: Tuple[str, ...]             # bounded, scored selection - analysable
    patchable: Tuple[str, ...]           # the same files - this path has no other list
    tests_detected: bool
    tests_note: str
    all_source_files: Tuple[str, ...]    # every recognised source file, for display only
    cloned: ClonedRepo = field(repr=False, compare=False)

    def cleanup(self) -> None:
        self.cloned.cleanup()

    def as_event(self) -> dict:
        return {
            "owner": self.owner, "name": self.name, "url": self.url,
            "commit_sha": self.commit_sha, "service": self.name,
            "runtime": self.primary_language, "verification": "none",
            "entrypoint": self.entrypoint, "sources": list(self.sources),
            "patchable": list(self.patchable),
            "database": None, "workload": None, "contract": "standard",
            "primary_language": self.primary_language,
            "detected_languages": list(self.detected_languages),
            "language_counts": dict(self.language_counts),
            "tests_detected": self.tests_detected, "tests_note": self.tests_note,
            "all_source_files": len(self.all_source_files),
        }


def _instruction_words(instruction: str) -> Sequence[str]:
    words = []
    for raw in (instruction or "").lower().split():
        word = raw.strip(".,!?()[]{}:;\"'`")
        if len(word) > 3 and word not in _STOPWORDS:
            words.append(word)
    return words


def _score(relative: str, content: str, words: Sequence[str],
          entrypoint_names: Sequence[str]) -> float:
    name = os.path.basename(relative).lower()
    score = 0.0
    if name in entrypoint_names:
        score += 100.0
    if name in ("__init__.py", "index.js", "index.ts"):
        score -= 10.0
    if "/" not in relative:
        score += 20.0                          # root-level files first
    lowered_path = relative.lower()
    if any(marker in lowered_path for marker in ("test", "spec", "__tests__")):
        score -= 25.0                          # readable, but rarely the edit target
    if any(marker in lowered_path for marker in
          ("migrations", "vendor", "generated", ".min.")):
        score -= 40.0
    lowered_content = content.lower()
    for word in words:
        if word in lowered_content or word in lowered_path:
            score += 8.0
    score -= len(content) / 20000.0            # mild penalty for very large files
    return score


def discover_sources(workspace: str, instruction: str = "",
                     detection: LanguageDetection = None
                     ) -> Tuple[List[str], Dict[str, str], List[str], LanguageDetection]:
    """A bounded, scored selection of the repository's own recognised
    source, across every language detected - not only the primary one, so a
    mixed repository still shows a planner the file it actually needs.

    Returns (chosen, contents, all_source_files, detection). `chosen` is the
    ordered list of relative paths a planner will actually be shown (and the
    only ones it may propose an edit to); `contents` maps every readable
    candidate to its text; `all_source_files` is every recognised source
    file found, for display only - Causeway is not silently ignoring the
    rest, it is bounding what one prompt carries.
    """
    all_files = walk_files(workspace)
    if detection is None:
        detection = detect_languages(workspace, all_files)

    from causeway.languages.registry import ADAPTERS, is_denied_path
    # is_denied_path is the same floor causeway.patch.validator applies to a
    # proposed edit - applied here too, so a credential-shaped file is never
    # even read into a prompt in the first place.
    recognised = [f for f in all_files
                 if any(a.matches_file(f) for a in ADAPTERS) and not is_denied_path(f)]
    entrypoint_names = {
        name for language in detection.detected
        for name in (_adapter_entrypoints(language))
    }

    words = _instruction_words(instruction)
    contents: Dict[str, str] = {}
    scored: List[Tuple[float, str]] = []
    for relative in recognised:
        path = os.path.join(workspace, relative)
        try:
            if os.path.getsize(path) > MAX_FILE_BYTES_TO_READ:
                continue
            with io.open(path, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
        except OSError:
            continue
        contents[relative] = text
        scored.append((_score(relative, text, words, entrypoint_names), relative))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    chosen: List[str] = []
    total = 0
    for _score_value, relative in scored:
        if len(chosen) >= MAX_FILES:
            break
        text = contents[relative]
        cost = min(len(text), MAX_FILE_CHARS)
        if chosen and total + cost > MAX_TOTAL_CHARS:
            continue
        chosen.append(relative)
        total += cost
    return sorted(chosen), contents, recognised, detection


def _adapter_entrypoints(language_id: str) -> Sequence[str]:
    from causeway.languages.registry import adapter_for
    adapter = adapter_for(language_id)
    return adapter.entrypoint_names if adapter else ()


def guess_entrypoint(sources: Sequence[str], detection: LanguageDetection) -> str:
    """Case-insensitive on purpose: a conventional entrypoint name like
    Java's Main.java is capitalised by convention, and entrypoint_names is
    written lowercase - the file itself is still the one being matched."""
    for language in detection.detected:
        for name in _adapter_entrypoints(language):
            for relative in sources:
                lowered = relative.lower()
                if lowered == name or lowered.endswith("/" + name):
                    return relative
    return ""


def detect_tests(all_source_files: Sequence[str]) -> Tuple[bool, str]:
    for relative in all_source_files:
        base = os.path.basename(relative).lower()
        lowered = relative.lower()
        if (any(marker in base for marker in _TEST_MARKERS)
                or "/tests/" in lowered or "/test/" in lowered
                or "/__tests__/" in lowered):
            return True, (
                "test files were found, but Causeway does not install this "
                "repository's dependencies or execute its tests automatically - "
                "running untrusted, arbitrary test code with unknown "
                "requirements is out of scope for this path")
    return False, "no test files were found in this repository"


def load_standard(cloned: ClonedRepo, ref: RepoRef, instruction: str = ""
                  ) -> StandardRepositoryContext:
    """Read a cloned workspace without a manifest. Rejects only when no
    supported language was detected at all - never because it lacks
    causeway.json."""
    chosen, _contents, all_source_files, detection = discover_sources(
        cloned.path, instruction)

    if not detection.primary:
        from causeway.languages.registry import ADAPTERS
        supported = ", ".join(sorted(a.display_name for a in ADAPTERS))
        raise RepositoryRejected(
            "analysis",
            "no supported language was detected in this repository. Causeway's "
            "prototype currently recognises %s - none of their signal files or "
            "source extensions were found at %s" % (supported, ref.url))

    if not chosen:
        raise RepositoryRejected(
            "analysis",
            "%s was detected but no readable source file was found to analyse "
            "(every candidate was empty, unreadable, or too large)"
            % detection.primary)

    entrypoint = guess_entrypoint(chosen, detection)
    tests_detected, tests_note = detect_tests(all_source_files)

    return StandardRepositoryContext(
        owner=ref.owner, name=ref.name, url=ref.url, commit_sha=cloned.commit_sha,
        workspace=cloned.path, primary_language=detection.primary,
        detected_languages=detection.detected, language_counts=detection.counts,
        entrypoint=entrypoint, sources=tuple(chosen), patchable=tuple(chosen),
        tests_detected=tests_detected, tests_note=tests_note,
        all_source_files=tuple(all_source_files), cloned=cloned,
    )
