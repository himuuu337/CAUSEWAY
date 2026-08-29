"""The CodePatch a requested-change planner returns, and the bounds it must
satisfy. See causeway/patch/__init__.py for why this is a different shape
from causeway.fixer.schema.FixSpec.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Tuple

from causeway.planner.schema import Check, ProviderUnavailable  # re-exported

__all__ = ["MAX_FILES", "MAX_HUNKS_PER_FILE", "MAX_TOTAL_HUNKS", "MAX_HUNK_CHARS",
          "PATCH_SCHEMA", "PatchHunk", "PatchFile", "CodePatch", "PatchRequest",
          "Check", "ProviderUnavailable"]

# A small, bounded number of actual file edits - never a whole-repository
# rewrite, and never something too large for a human to review on screen.
MAX_FILES = 3
MAX_HUNKS_PER_FILE = 4
MAX_TOTAL_HUNKS = 6
MAX_HUNK_CHARS = 4000

PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "hunks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"before": {"type": "string"},
                                          "after": {"type": "string"}},
                            "required": ["before", "after"],
                        },
                    },
                },
                "required": ["path", "hunks"],
            },
        },
        "reasoning_summary": {"type": "string"},
    },
    "required": ["summary", "files", "reasoning_summary"],
}


@dataclass(frozen=True)
class PatchHunk:
    before: str
    after: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PatchFile:
    path: str
    hunks: Tuple[PatchHunk, ...]

    def as_dict(self) -> dict:
        return {"path": self.path, "hunks": [h.as_dict() for h in self.hunks]}


@dataclass(frozen=True)
class CodePatch:
    summary: str
    files: Tuple[PatchFile, ...]
    # Presentation only. Quoted on screen, never read by the engine.
    reasoning_summary: str

    def as_dict(self) -> dict:
        return {"summary": self.summary,
                "files": [f.as_dict() for f in self.files],
                "reasoning_summary": self.reasoning_summary}


@dataclass(frozen=True)
class PatchRequest:
    """Everything a requested-change planner is allowed to see. Bounded,
    repository-owned context - never the whole repository blindly."""

    instruction: str                       # the user's own words, verbatim
    goal: str
    intent: Mapping[str, Any]              # IntentSpec.as_dict()
    service: str
    entrypoint: str
    sources: Tuple[str, ...]               # analysable
    patchable: Tuple[str, ...]             # writable
    file_contents: Mapping[str, str]       # patchable path -> bounded current text
    acceptance: Mapping[str, Any]          # the manifest's own declared probes
