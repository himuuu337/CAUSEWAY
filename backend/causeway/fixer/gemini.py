"""The Gemini fix planner.

Gemini's entire authority in the fix loop is to answer one question: given a
hypothesis that a deterministic experiment has already PROVEN, and the
current (broken) value at one named, whitelisted repair surface, what should
that value become? It returns a FixSpec and nothing else. It is never given a
fix-verification measurement - none exists yet, since planning happens before
the sandbox runs - and it is never told the answer: the known-safe repair
lives in causeway.sandbox.repair, which this module does not import and this
prompt does not quote.

Standard library only, over the same REST surface causeway/planner/gemini.py
uses, for the same reason: a demo that needs an SDK installed is a demo with
one more way to fail on stage.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request

from causeway.fixer.schema import FixRequest, ProviderUnavailable

API_HOST = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_TIMEOUT = 20.0

SYSTEM_INSTRUCTION = (
    "You are proposing a concrete, narrowly-scoped code fix for a production "
    "incident whose root cause has already been proven by a deterministic "
    "experiment - you are not being asked to find the cause, only to repair "
    "it.\n"
    "You do not know whether your fix will verify; no fix-verification "
    "measurement exists yet.\n"
    "You may only change the ONE named repair surface you are given, from "
    "its current value to a corrected value you propose.\n"
    "Do not provide or predict whether the fix will be VERIFIED or FAILED. A "
    "sandbox will apply your fix to a disposable copy and the measurement "
    "decides.\n"
    "Output must conform to the supplied FixSpec schema.\n"
    "Keep reasoning_summary concise - one or two sentences."
)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "hypothesis_id": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "operation": {
            "type": "OBJECT",
            "properties": {
                "type": {"type": "STRING"},
                "target": {"type": "STRING"},
                "before": {"type": "STRING"},
                "after": {"type": "STRING"},
            },
            "required": ["type", "target", "before", "after"],
            "propertyOrdering": ["type", "target", "before", "after"],
        },
        "reasoning_summary": {"type": "STRING"},
    },
    "required": ["hypothesis_id", "summary", "operation", "reasoning_summary"],
    "propertyOrdering": ["hypothesis_id", "summary", "operation", "reasoning_summary"],
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


def build_prompt(request: FixRequest) -> str:
    """Everything the fix planner is allowed to know.

    Note what is absent and stays absent: no fix-verification measurement (none
    exists yet), no ratio, no control, no VERIFIED/FAILED word, and no quote of
    the known-safe repair value - that lives only in causeway.sandbox.repair,
    which this function never imports.
    """
    candidate = request.candidate
    location = request.location or {}
    if location:
        # A repository: the hypothesis is a place in real source, so say
        # where. Note what is still not here - the counterfactual the
        # detector derived is the known-safe repair, and quoting it would
        # hand the model the answer the validator exists to check.
        lines = [
            "HYPOTHESIS %s has been PROVEN the cause of the incident by a "
            "deterministic controlled experiment." % request.hypothesis_id,
            "It is a location in this repository's own source:",
            "    file    %s" % location.get("file"),
            "    line    %s" % location.get("line"),
            "    symbol  %s()" % location.get("symbol"),
            "    present %s" % json.dumps(location.get("observed", "")),
            "Why it was proven: %s" % request.causal_reason,
        ]
    else:
        lines = [
            "HYPOTHESIS %s (%s - %s) has been PROVEN the cause of the incident by a "
            "deterministic controlled experiment." % (
                request.hypothesis_id, candidate.get("branch"), candidate.get("summary")),
            "Why it was proven: %s" % request.causal_reason,
        ]

    lines += [
        "",
        "THE MECHANISM: %s" % request.mechanism,
    ]

    intent = request.intent or {}
    if intent:
        # The user's instruction is the goal. It is quoted, never rewritten,
        # and the constraints below are the ones deterministic code will
        # enforce after this proposal comes back - they are not suggestions
        # the model may negotiate.
        lines += ["", "WHAT THE USER ASKED FOR: %s"
                  % json.dumps(intent.get("raw_instruction", ""))]
        enforced = intent.get("enforced") or ()
        if enforced:
            lines.append("CONSTRAINTS THAT WILL BE ENFORCED ON YOUR PROPOSAL:")
            for constraint in enforced:
                lines.append("  - %s %s" % (constraint.get("kind"),
                                            json.dumps(constraint.get("value"))))
        advisory = intent.get("advisory") or ()
        if advisory:
            lines.append("STATED AS ADVISORY (recorded, not mechanically checked): %s"
                         % "; ".join(str(c.get("source")) for c in advisory))

    lines += [
        "",
        "REPAIR SURFACE AVAILABLE (the only thing you may change):",
    ]
    for target in request.repair_targets:
        lines.append("  %s  current value:  %s"
                     % (target, json.dumps(request.current_code.get(target, ""))))

    lines += [
        "",
        "PROPOSE THE FIX.",
        "Rules your proposal must satisfy, or it will be rejected:",
        "  - hypothesis_id must be %s" % request.hypothesis_id,
        "  - operation.type must be \"replace_predicate\"",
        "  - operation.target must be exactly one of the repair surfaces listed above",
        "  - operation.before must be exactly the current value shown above for "
        "that target",
        "  - operation.after must be a corrected value that restores index-friendly, "
        "unwrapped-column access - not a shell command, not a file path, not a "
        "verdict",
        "  - reasoning_summary is one or two sentences for a human reader and must "
        "not claim a result - a sandbox has not tested this fix yet",
    ]
    return "\n".join(lines)


def _spec_object(payload):
    """Pull the FixSpec out of whatever envelope came back. Written tolerantly
    on purpose, exactly as causeway.planner.gemini._plan_object is."""
    stack = [payload]
    seen = 0
    while stack and seen < 500:
        seen += 1
        node = stack.pop()
        if isinstance(node, dict):
            if "hypothesis_id" in node and "operation" in node:
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
    raise ProviderUnavailable("no FixSpec object in the Gemini response")


class GeminiFixPlanner:
    """Gemini over REST. Proposes a fix; never decides whether it worked."""

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
        request = urllib.request.Request(url, data=payload, headers=headers,
                                         method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _call(self, path: str, body: dict) -> dict:
        url = "%s/v1beta/%s" % (API_HOST, path)
        headers = {"Content-Type": "application/json",
                   "x-goog-api-key": self._api_key}
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
            raise ProviderUnavailable(
                self._redact("Gemini unreachable: %s" % exc.reason))
        except socket.timeout:
            raise ProviderUnavailable("Gemini timed out after %.0fs" % self.timeout)
        except Exception as exc:                       # noqa: BLE001
            raise ProviderUnavailable(
                self._redact("%s: %s" % (type(exc).__name__, exc)))

    def propose(self, request: FixRequest, schema=None) -> dict:
        """Return a raw fix dict. The validator decides whether it may run."""
        if not self.available:
            raise ProviderUnavailable("no GEMINI_API_KEY in the environment")

        response = self._call("models/%s:generateContent" % self.model, {
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user",
                          "parts": [{"text": build_prompt(request)}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
            },
        })

        try:
            return _spec_object(response)
        except ProviderUnavailable:
            raise
        except Exception as exc:                       # noqa: BLE001
            raise ProviderUnavailable(
                self._redact("could not read the Gemini response: %s" % exc))
