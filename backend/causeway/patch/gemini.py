"""The Gemini requested-change planner.

Given the user's own instruction, the enforceable constraints already parsed
out of it, bounded source from the repository's own patchable files, and (if
the repository declares any) the acceptance criteria a change like this would
be checked against, Gemini proposes a CodePatch: a small, bounded set of
file+hunk edits. It is never told a known-safe answer, because there is not
one - a requested change is not a repair for an already-proven cause. It
returns a CodePatch and nothing else; causeway.patch.validator decides
whether it may reach a sandbox, and a real HTTP request against a disposable,
patched copy decides whether it actually does what was asked.

Standard library only, over the same REST surface causeway/planner/gemini.py
and causeway/fixer/gemini.py use, for the same reason: a demo that needs an
SDK installed is a demo with one more way to fail on stage.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request

from causeway.patch.schema import (MAX_FILES, MAX_HUNKS_PER_FILE, MAX_HUNK_CHARS,
                                   PatchRequest, ProviderUnavailable)

API_HOST = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_TIMEOUT = 25.0

SYSTEM_INSTRUCTION = (
    "You are proposing a concrete, narrowly-scoped source code change to "
    "satisfy a user's explicit instruction against a real repository whose "
    "relevant source you are shown below.\n"
    "You may only edit files you are shown the current content of, and only "
    "within the bounded list of files this run is allowed to touch.\n"
    "Every hunk's `before` field must be copied EXACTLY, character for "
    "character, from the file content you were shown - it is matched "
    "verbatim against the real file and rejected if it does not match "
    "exactly once.\n"
    "Propose the smallest change that satisfies the instruction. Do not "
    "refactor, rename, reformat, or touch anything the instruction did not "
    "ask for.\n"
    "Do not claim or predict whether the change will be VERIFIED or FAILED - "
    "a sandbox will apply it to a disposable copy and real requests decide.\n"
    "Output must conform to the supplied CodePatch schema."
)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "files": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING"},
                    "hunks": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {"before": {"type": "STRING"},
                                          "after": {"type": "STRING"}},
                            "required": ["before", "after"],
                            "propertyOrdering": ["before", "after"],
                        },
                    },
                },
                "required": ["path", "hunks"],
                "propertyOrdering": ["path", "hunks"],
            },
        },
        "reasoning_summary": {"type": "STRING"},
    },
    "required": ["summary", "files", "reasoning_summary"],
    "propertyOrdering": ["summary", "files", "reasoning_summary"],
}


def api_key_from_env() -> str:
    return (os.environ.get("GEMINI_API_KEY")
            or os.environ.get("CAUSEWAY_GEMINI_KEY") or "")


def model_from_env() -> str:
    return os.environ.get("CAUSEWAY_GEMINI_MODEL", DEFAULT_MODEL)


def timeout_from_env() -> float:
    try:
        return float(os.environ.get("CAUSEWAY_GEMINI_TIMEOUT", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def build_prompt(request: PatchRequest) -> str:
    lines = [
        "WHAT THE USER ASKED FOR: %s" % json.dumps(request.instruction),
        "PARSED GOAL: %s" % request.goal,
        "",
        "SERVICE: %s (runtime: python, entrypoint %s)" % (request.service, request.entrypoint),
    ]

    enforced = request.intent.get("enforced") or ()
    if enforced:
        lines.append("CONSTRAINTS THAT WILL BE ENFORCED ON YOUR PROPOSAL (not "
                     "suggestions - a violation is rejected before anything runs):")
        for constraint in enforced:
            lines.append("  - %s %s" % (constraint.get("kind"),
                                        json.dumps(constraint.get("value"))))
    advisory = request.intent.get("advisory") or ()
    if advisory:
        lines.append("STATED AS ADVISORY (recorded, not mechanically checked): %s"
                     % "; ".join(str(c.get("source")) for c in advisory))

    lines += ["", "FILES YOU MAY EDIT (current content shown in full or truncated):"]
    for path in request.patchable:
        content = request.file_contents.get(path)
        if content is None:
            continue
        lines.append("--- %s ---" % path)
        lines.append(content)
        lines.append("--- end %s ---" % path)

    if request.acceptance:
        lines += ["", "HOW THIS CHANGE WILL BE CHECKED (real HTTP requests against a "
                 "disposable, patched copy of the service - this is the "
                 "specification of correct behaviour, not something to hard-code "
                 "a special case for):"]
        for name, probe in request.acceptance.items():
            lines.append("  probe %r: %s %s" % (name, probe.get("method"), probe.get("path")))
            for case in probe.get("cases", ()):
                lines.append("    case %r: body=%s -> expected HTTP status in %s"
                             % (case.get("name"), json.dumps(case.get("body")),
                                case.get("expect_status")))

    lines += [
        "",
        "PROPOSE THE PATCH.",
        "Rules your proposal must satisfy, or it will be rejected:",
        "  - at most %d file(s), each with at most %d hunk(s)" % (MAX_FILES, MAX_HUNKS_PER_FILE),
        "  - each hunk's before/after text is at most %d characters" % MAX_HUNK_CHARS,
        "  - every file path must be one you were shown the content of above",
        "  - every hunk's `before` must match that content exactly, character for "
        "character, and occur exactly once in the file",
        "  - reasoning_summary is one or two sentences for a human reader and must "
        "not claim a verification result - none has happened yet",
    ]
    return "\n".join(lines)


def _patch_object(payload):
    stack = [payload]
    seen = 0
    while stack and seen < 500:
        seen += 1
        node = stack.pop()
        if isinstance(node, dict):
            if "files" in node and "summary" in node:
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str):
            text = node.strip()
            if text.startswith("{"):
                try:
                    stack.append(json.loads(text))
                except ValueError:
                    continue
    raise ProviderUnavailable("no CodePatch object in the Gemini response")


class GeminiPatchPlanner:
    """Gemini over REST. Proposes a patch; never decides whether it works."""

    kind = "gemini"

    def __init__(self, api_key: str = None, model: str = None,
                 timeout: float = None, transport=None):
        self._api_key = api_key if api_key is not None else api_key_from_env()
        self.model = model or model_from_env()
        self.timeout = timeout if timeout is not None else timeout_from_env()
        self._transport = transport or self._post

    @property
    def name(self) -> str:
        return "gemini:%s" % self.model

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _redact(self, text) -> str:
        cleaned = str(text)
        if self._api_key:
            cleaned = cleaned.replace(self._api_key, "***")
        return cleaned

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _call(self, path: str, body: dict) -> dict:
        url = "%s/v1beta/%s" % (API_HOST, path)
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._api_key}
        try:
            return self._transport(url, headers, body)
        except ProviderUnavailable:
            raise
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            raise ProviderUnavailable(self._redact(
                "Gemini returned HTTP %s%s" % (exc.code, ": " + detail if detail else "")))
        except urllib.error.URLError as exc:
            raise ProviderUnavailable(self._redact("Gemini unreachable: %s" % exc.reason))
        except socket.timeout:
            raise ProviderUnavailable("Gemini timed out after %.0fs" % self.timeout)
        except Exception as exc:                       # noqa: BLE001
            raise ProviderUnavailable(self._redact("%s: %s" % (type(exc).__name__, exc)))

    def propose(self, request: PatchRequest, schema=None) -> dict:
        if not self.available:
            raise ProviderUnavailable("no GEMINI_API_KEY in the environment")

        response = self._call("models/%s:generateContent" % self.model, {
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": build_prompt(request)}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
            },
        })

        try:
            return _patch_object(response)
        except ProviderUnavailable:
            raise
        except Exception as exc:                       # noqa: BLE001
            raise ProviderUnavailable(
                self._redact("could not read the Gemini response: %s" % exc))
