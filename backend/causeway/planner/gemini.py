"""The Gemini experiment planner.

Gemini's entire authority in Causeway is to answer one question: given the
incident and the candidates, which change should be removed, and what would we
expect to see if it were the cause? It returns an ExperimentSpec and nothing
else. It never sees a measurement, it is called before the sandbox runs, and
its output passes the same eight deterministic checks the offline planner's
output passes.

Standard library only. A demo that needs an SDK installed is a demo with one
more way to fail on stage, and the REST surface used here is four fields wide.

What this module may not do, structurally:
  - it is not reachable from causeway.verdict, and a test walks the import
    graph to keep it that way
  - it never receives a phase result, a ratio, a control or a verdict
  - it never puts the API key in a URL, a log line, an exception or an event
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request

from causeway.planner.schema import PlanRequest, ProviderUnavailable

API_HOST = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_TIMEOUT = 20.0

# The boundary, stated in the model's own context.
SYSTEM_INSTRUCTION = (
    "You are designing a controlled diagnostic experiment for a production "
    "incident.\n"
    "You do not know the experiment outcome.\n"
    "You must select one testable hypothesis and propose one safe intervention.\n"
    "Change exactly one independent variable and hold every other flag fixed.\n"
    "Do not provide or predict the final root-cause verdict. A sandbox will run "
    "your experiment and the measurement decides.\n"
    "Output must conform to the supplied ExperimentSpec schema.\n"
    "Keep reasoning_summary concise - one or two sentences."
)

# The ExperimentSpec, expressed the way the Gemini API wants a response schema.
# This is a serialisation of the schema in schema.py for one provider, not a
# second schema: whatever comes back is still checked against the real one by
# the deterministic validator.
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "hypothesis_id": {"type": "STRING"},
        "intervention": {
            "type": "OBJECT",
            "properties": {"flag": {"type": "STRING"}, "value": {"type": "BOOLEAN"}},
            "required": ["flag", "value"],
            "propertyOrdering": ["flag", "value"],
        },
        "fixture_id": {"type": "STRING"},
        "expected_signature": {
            "type": "OBJECT",
            "properties": {
                "metric": {"type": "STRING"},
                "op": {"type": "STRING"},
                "relative_to": {"type": "STRING"},
                "factor": {"type": "NUMBER"},
            },
            "required": ["metric", "op", "relative_to", "factor"],
            "propertyOrdering": ["metric", "op", "relative_to", "factor"],
        },
        "discriminates_between": {"type": "ARRAY", "items": {"type": "STRING"}},
        "reasoning_summary": {"type": "STRING"},
    },
    "required": ["hypothesis_id", "intervention", "fixture_id",
                 "expected_signature", "discriminates_between",
                 "reasoning_summary"],
    "propertyOrdering": ["hypothesis_id", "intervention", "fixture_id",
                         "expected_signature", "discriminates_between",
                         "reasoning_summary"],
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


def build_prompt(request: PlanRequest) -> str:
    """Everything the planner is allowed to know.

    Note what is absent and stays absent: no phase result, no ratio, no
    control, no verdict, and nothing that says which candidate is the real
    cause. This function IS the information boundary, and tests assert on the
    string it returns.
    """
    incident = request.incident
    lines = [
        "INCIDENT %s on %s." % (incident.get("id"), incident.get("service")),
        "Symptom: %s" % incident.get("symptom"),
        "Detected at %s." % incident.get("detected_at"),
        "",
        "CANDIDATE CHANGES, all deployed inside the incident window:",
    ]
    for candidate in request.candidates:
        line = ("  %s  %s  -  %s  (%d lines across %d files"
                % (candidate.get("change_id"), candidate.get("branch"),
                   candidate.get("summary"), candidate.get("lines_changed", 0),
                   candidate.get("files_changed", 0)))
        score = candidate.get("observational_score")
        if score is not None:
            line += ", correlation-only score %.3f" % score
        lines.append(line + ")")

    lines += [
        "",
        "The correlation-only score is an observational ranking: evidence about "
        "how suspicious a change looks, not about whether it is causal, and not "
        "a result.",
        "",
        "AVAILABLE INTERVENTIONS (runtime flags the sandbox can toggle): %s"
        % ", ".join(request.intervention_surfaces),
        "FLAG STATE AT INCIDENT TIME: %s"
        % json.dumps(request.incident_state, sort_keys=True),
        "REPLAY FIXTURES AVAILABLE: %s" % ", ".join(request.fixtures),
        "METRIC OBSERVED: p95_ms, the 95th percentile request latency over a "
        "replayed workload.",
        "",
        "HOW THE ENGINE WILL JUDGE THE EXPERIMENT YOU DESIGN:",
        "  It measures a healthy control immediately before and after every "
        "phase, and compares each phase against the median of the two beside it.",
        "  The failure counts as present at >= %.1fx that local control and as "
        "gone at <= %.1fx." % (request.failure_factor, request.recovery_factor),
        "  Absolute millisecond thresholds are never used - they go stale the "
        "moment the machine changes.",
        "",
        "DESIGN THE EXPERIMENT THAT TESTS HYPOTHESIS %s."
        % request.target_hypothesis,
        "Rules your plan must satisfy, or it will be rejected:",
        "  - hypothesis_id must be %s" % request.target_hypothesis,
        "  - intervention.flag must be %s and intervention.value must be false, "
        "so the change is removed" % request.target_hypothesis,
        "  - exactly one flag may move; every other flag is held fixed",
        "  - fixture_id must be one of the fixtures listed above",
        "  - discriminates_between must name at least two candidates",
        "  - expected_signature describes what you expect IF the hypothesis is "
        "true: metric \"p95_ms\", op \"<=\", relative_to \"control\", and factor "
        "exactly %s" % request.recovery_factor,
        "  - reasoning_summary is one or two sentences for a human reader and "
        "must not claim a result",
    ]
    return "\n".join(lines)


def _plan_object(payload):
    """Pull the plan out of whatever envelope came back.

    Written tolerantly on purpose: response envelopes shift between API
    revisions, and a demo should not break because a field moved. Any failure
    here just means the deterministic planner runs instead.
    """
    stack = [payload]
    seen = 0
    while stack and seen < 500:
        seen += 1
        node = stack.pop()
        if isinstance(node, dict):
            if "hypothesis_id" in node and "intervention" in node:
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
    raise ProviderUnavailable("no ExperimentSpec object in the Gemini response")


class GeminiPlanner:
    """Gemini over REST. Proposes; never decides."""

    kind = "gemini"

    def __init__(self, api_key: str = None, model: str = None,
                 timeout: float = None, transport=None):
        self._api_key = api_key if api_key is not None else api_key_from_env()
        self.model = model or model_from_env()
        self.timeout = timeout if timeout is not None else timeout_from_env()
        # Injectable so tests never touch the network:
        #   transport(url, headers, body_dict) -> parsed response dict
        self._transport = transport or self._post

    @property
    def name(self) -> str:
        return "gemini:%s" % self.model

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _redact(self, text) -> str:
        """The key must never reach a log line, an exception or an event."""
        cleaned = str(text)
        if self._api_key:
            cleaned = cleaned.replace(self._api_key, "***")
        return cleaned

    # -- transport ---------------------------------------------------------
    def _post(self, url: str, headers: dict, body: dict) -> dict:
        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=payload, headers=headers,
                                         method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _call(self, path: str, body: dict) -> dict:
        # The key travels in a header, never in the URL - a URL ends up in
        # error strings, proxy logs and stack traces.
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

    # -- the one thing it does ---------------------------------------------
    def propose(self, request: PlanRequest, schema=None) -> dict:
        """Return a raw plan dict. The validator decides whether it may run."""
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
            return _plan_object(response)
        except ProviderUnavailable:
            raise
        except Exception as exc:                       # noqa: BLE001
            raise ProviderUnavailable(
                self._redact("could not read the Gemini response: %s" % exc))

    def list_models(self):
        """Setup diagnostics only: which model names this key can actually use.
        Nothing on the investigation path calls this."""
        url = "%s/v1beta/models" % API_HOST
        request = urllib.request.Request(
            url, headers={"x-goog-api-key": self._api_key}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:                       # noqa: BLE001
            raise ProviderUnavailable(self._redact("%s: %s" % (type(exc).__name__, exc)))
        names = []
        for model in payload.get("models", []):
            methods = model.get("supportedGenerationMethods", [])
            if not methods or "generateContent" in methods:
                names.append(str(model.get("name", "")).replace("models/", ""))
        return sorted(name for name in names if name)
