"""Reading a plain-English instruction without a model.

The offline parser, and the fallback for every Gemini failure. It is
deliberately literal: it recognises the phrasings this prototype can actually
honour and says NEEDS_CLARIFICATION rather than guessing at anything else.
Guessing is the failure mode that matters here - a wrong mode means Causeway
either edits code it was told to leave alone, or refuses to do the work it
was asked for.
"""
from __future__ import annotations

import re
from typing import List, Optional

from causeway.intent.schema import (DIAGNOSE_AND_FIX, DIAGNOSE_ONLY,
                                    NEEDS_CLARIFICATION, REQUESTED_CHANGE,
                                    Constraint, IntentSpec)

NAME = "deterministic"

_DIAGNOSE_ONLY = (
    "without changing anything", "only analyse", "only analyze",
    "analysis only", "diagnose only", "diagnosis only", "just tell me",
    "leave the code", "don't fix", "do not fix", "no fix",
    "tell me what's wrong", "tell me whats wrong",
)

# "do not modify anything" forbids changing everything, and is a mode.
# "do not modify app.py" forbids changing ONE file, and is a scope constraint
# on a fix the user still wants. Reading the second as the first is how a
# request to fix something quietly becomes a request to fix nothing, so the
# object of the verb decides which it is.
_BLANKET = re.compile(
    r"(?:do ?n[o']?t|never|avoid)\s+(?:modify|change|touch|edit)(?:ing)?"
    r"\s*(?:the\s+)?(?P<object>[\w./-]*)", re.I)
_FIX = ("fix it", "and fix", "then fix", "fix the", "repair", "resolve it",
        "make it fast", "make it faster", "sort it out")
_CHANGE = ("instead of", "change it to", "make it so", "should now",
           "rather than", "replace the", "switch to")

_ONLY_MODIFY = re.compile(
    r"only (?:modify|change|touch|edit)\s+([\w./-]+(?:\s*(?:,|and)\s*[\w./-]+)*)", re.I)
_DO_NOT_MODIFY = re.compile(
    r"(?:do ?n[o']?t|never|avoid)\s+(?:modify|change|touch|edit)(?:ing)?\s+(?:the\s+)?([\w./-]+)", re.I)
_NO_DEPS = ("no new dependencies", "without new dependencies", "don't add dependencies",
            "do not add dependencies", "no extra dependencies", "no dependencies")
_NO_SCHEMA = ("do not change the schema", "don't change the schema",
              "without changing the schema", "no schema change", "keep the schema")
# "do not modify anything" is not a scope constraint naming a file called
# "anything" - it is the diagnose_only constraint, which is recognised
# separately. A do_not_modify constraint that names one of these is dropped
# rather than filed, because an enforced constraint pointing at a file that
# cannot exist reads as enforcement and enforces nothing.
_NOT_A_FILE = frozenset((
    "anything", "any", "it", "them", "code", "the", "this", "that",
    "everything", "nothing", "files", "file", "source", "repo", "repository",
))

_ADVISORY = (
    ("do not change the api", "the public API must not change"),
    ("don't change the api", "the public API must not change"),
    ("without changing the api", "the public API must not change"),
    ("backward compat", "backward compatibility must be preserved"),
    ("backwards compat", "backward compatibility must be preserved"),
    ("keep it simple", "keep the change simple"),
    ("minimal", "keep the change minimal"),
)


# What each mode means when the user picked it in the interface rather than
# typing it. Not an interpretation of anything - a restatement of the choice.
_GOAL_FOR_MODE = {
    DIAGNOSE_ONLY: "diagnose the incident and change nothing",
    DIAGNOSE_AND_FIX: "diagnose the incident and propose a verified fix",
    REQUESTED_CHANGE: "make the requested change",
}


def _split(raw: str) -> List[str]:
    return [part.strip() for part in re.split(r"\s*(?:,|and)\s*", raw) if part.strip()]


def _looks_like_a_file(name: str) -> bool:
    """A repository-relative path, not an English word. Deliberately strict:
    a scope constraint naming something that cannot be a file would be shown
    as ENFORCED and would enforce nothing."""
    cleaned = name.strip().strip(".,;:!?")
    if not cleaned or cleaned.lower() in _NOT_A_FILE:
        return False
    return "." in cleaned or "/" in cleaned


def _blanket_phrase(text: str) -> Optional[str]:
    """The phrase that forbids changing ANYTHING, if the instruction has one."""
    for match in _BLANKET.finditer(text):
        named = match.group("object").strip().strip(".,;:!?")
        if not named or named.lower() in _NOT_A_FILE:
            return match.group(0)
    return None


def detect_mode(text: str, requested: Optional[str] = None) -> str:
    """An explicit mode from the interface always wins - the user chose it."""
    if requested in (DIAGNOSE_ONLY, DIAGNOSE_AND_FIX, REQUESTED_CHANGE):
        return requested
    lowered = text.lower()
    if any(phrase in lowered for phrase in _DIAGNOSE_ONLY):
        return DIAGNOSE_ONLY
    if _blanket_phrase(text):
        return DIAGNOSE_ONLY
    if any(phrase in lowered for phrase in _CHANGE):
        return REQUESTED_CHANGE
    if any(phrase in lowered for phrase in _FIX):
        return DIAGNOSE_AND_FIX
    if re.search(r"\bwhy\b|\bslow\b|\bwhat'?s wrong\b|\bdiagnos", lowered):
        return DIAGNOSE_ONLY
    return NEEDS_CLARIFICATION


def find_constraints(text: str) -> List[Constraint]:
    lowered = text.lower()
    found: List[Constraint] = []

    match = _ONLY_MODIFY.search(text)
    if match:
        # only the parts that actually look like files. "only modify db.py and
        # no new dependencies" names one file and then starts a different
        # constraint; filing "no" as a filename would be enforcement that
        # enforces nothing.
        named = [part for part in _split(match.group(1)) if _looks_like_a_file(part)]
        if named:
            found.append(Constraint("only_modify", named, match.group(0)))
    for match in _DO_NOT_MODIFY.finditer(text):
        named = match.group(1).strip().strip(".,;:!?")
        if not _looks_like_a_file(named):
            continue
        found.append(Constraint("do_not_modify", [named], match.group(0)))
    for phrase in _NO_DEPS:
        if phrase in lowered:
            found.append(Constraint("no_new_dependencies", True, phrase))
            break
    for phrase in _NO_SCHEMA:
        if phrase in lowered:
            found.append(Constraint("no_schema_change", True, phrase))
            break
    phrase = next((p for p in _DIAGNOSE_ONLY if p in lowered), None) \
        or _blanket_phrase(text)
    if phrase:
        found.append(Constraint("diagnose_only", True, phrase))
    for phrase, description in _ADVISORY:
        if phrase in lowered:
            found.append(Constraint("advisory", description, phrase))
    return found


def parse(instruction: str, requested_mode: str = None) -> IntentSpec:
    text = (instruction or "").strip()
    if not text:
        # An empty box is ambiguous - unless the interface already carries an
        # explicit choice, in which case the user has answered the question
        # this would otherwise ask them.
        if requested_mode in (DIAGNOSE_ONLY, DIAGNOSE_AND_FIX, REQUESTED_CHANGE):
            return IntentSpec(
                raw_instruction="", mode=requested_mode,
                goal=_GOAL_FOR_MODE[requested_mode], source=NAME)
        return IntentSpec(
            raw_instruction="", mode=NEEDS_CLARIFICATION, goal="",
            question=("What should Causeway do with this repository - diagnose "
                      "a problem, diagnose and fix it, or make a specific "
                      "change?"), source=NAME)

    mode = detect_mode(text, requested_mode)
    constraints = find_constraints(text)
    if mode == NEEDS_CLARIFICATION:
        return IntentSpec(
            raw_instruction=text, mode=mode, goal=text, constraints=tuple(constraints),
            question=("Should Causeway only diagnose this, diagnose and fix it, "
                      "or make a specific change you have in mind?"), source=NAME)

    only = [c for c in constraints if c.kind == "only_modify"]
    return IntentSpec(
        raw_instruction=text, mode=mode, goal=text, constraints=tuple(constraints),
        allowed_scope=tuple(only[0].value) if only else (),
        prohibited_scope=tuple(v for c in constraints
                               if c.kind == "do_not_modify" for v in c.value),
        source=NAME)
