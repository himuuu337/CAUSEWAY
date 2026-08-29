"""The LanguageAdapter contract.

An adapter is a description of a language plus one safety-bounded
verification method - never an executor of the repository's own code.
`verify` is the only method an adapter defines that touches the filesystem
beyond reading, and it does so on a DISPOSABLE, ALREADY-COPIED workspace that
causeway.standard_investigation hands it - never the clone, and never before
the patch validator has already accepted every edit.

The rule every adapter's `verify` must follow, without exception: it may run
a compiler or interpreter's own SYNTAX or TYPE check (a flag that parses and
maybe type-checks without producing a running program - `py_compile`,
`node --check`, `-fsyntax-only`, `tsc --noEmit`, `go vet` against
already-vendored packages, `cargo check` against an already-vendored
Cargo.lock). It may never install a dependency, download a package, run a
repository-provided script (`npm install`, `pip install`, `mvn`, `gradle`,
`go build` against unfetched modules, `cargo build` against unfetched
crates), or execute the program the repository defines. When the tool that
would make even a syntax check possible is not present, or making it
possible would require one of those things, `verify` reports
`available=False` and says why - it does not degrade into running something
riskier to get an answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple


@dataclass(frozen=True)
class VerificationCheck:
    tool: str
    file: str
    passed: bool
    detail: str

    def as_dict(self) -> dict:
        return {"tool": self.tool, "file": self.file, "passed": self.passed,
                "detail": self.detail}


@dataclass(frozen=True)
class VerificationResult:
    available: bool
    checks: Tuple[VerificationCheck, ...]
    note: str    # always human-readable, whether or not verification ran

    @property
    def any_failed(self) -> bool:
        return any(not c.passed for c in self.checks)

    @property
    def all_passed(self) -> bool:
        return self.available and bool(self.checks) and not self.any_failed


class LanguageAdapter:
    """One language's detection signals and its one safe verification path.

    `source_extensions` and `manifest_files` are matched against a
    repository-relative path's lowercase form - case-insensitively, and
    never by executing anything. `entrypoint_names` is a preference order
    for guessing which selected file is the program's own entry, shown for
    context only; nothing launches it.
    """

    id: str = ""
    display_name: str = ""
    source_extensions: Tuple[str, ...] = ()
    manifest_files: Tuple[str, ...] = ()
    entrypoint_names: Tuple[str, ...] = ()

    def matches_file(self, relative_path: str) -> bool:
        lowered = relative_path.lower()
        return any(lowered.endswith(ext) for ext in self.source_extensions)

    def matches_manifest(self, root_filenames: Sequence[str]) -> bool:
        """Whether one of this language's project markers sits at the
        repository root. A marker starting with `*` matches by suffix
        (`*.csproj` matches any `Foo.csproj`); anything else matches the
        exact filename, case-insensitively."""
        lowered = [name.lower() for name in root_filenames]
        for marker in self.manifest_files:
            marker_l = marker.lower()
            if marker_l.startswith("*"):
                if any(name.endswith(marker_l[1:]) for name in lowered):
                    return True
            elif marker_l in lowered:
                return True
        return False

    def verify(self, workspace: str, changed_files: Sequence[str]) -> VerificationResult:
        """`workspace` is a disposable, already-patched copy. `changed_files`
        are repository-relative paths this adapter matched among the files a
        validated patch touched. Must never raise for an ordinary failure -
        a tool that is missing, times out, or reports a syntax error is a
        VerificationResult, not an exception."""
        raise NotImplementedError
