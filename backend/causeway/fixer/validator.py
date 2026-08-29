"""The deterministic gate between a proposed fix and the sandbox.

This is the CODE VALIDATES clause of the fix loop. Every FixSpec - from
Gemini or from the fallback - passes through the same checks before a single
byte is patched anywhere. `target` is resolved through the whitelist in
causeway.sandbox.repair, never treated as a filesystem path, so path
traversal is not merely checked for, it is structurally impossible: nothing
here ever concatenates `target` onto a path. `after` is compared against the
one value causeway.sandbox.repair already knows is safe - a proposal cannot
introduce a new string into the patched source unless it matches, whitespace
aside, that known-safe repair.

reasoning_summary and summary are exempt from the verdict-language check on
their own text, for the same reason ExperimentPlan.reasoning_summary is: they
are prose for a human, quoted on screen, never read by the engine. A summary
that says "this fix definitely resolves the incident" is accepted and
FLAGGED, not rejected - what is rejected is a conclusion appearing in a field
the engine actually reads.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from causeway import verdict
from causeway.fixer.schema import (ALLOWED_OPERATION_TYPES, FIX_SCHEMA,
                                   VERDICT_KEYS, VERDICT_TOKENS, Check,
                                   FixOperation, FixRequest, FixSpec)
from causeway.sandbox import repair

CHECK_NAMES = (
    "schema",
    "hypothesis_matches_request",
    "hypothesis_proven",
    "target_no_path_traversal",
    "target_is_known_repair_surface",
    "operation_type_allowed",
    "before_state_matches_sandbox",
    "after_state_is_a_known_safe_repair",
    "no_encoded_verdict",
)


@dataclass(frozen=True)
class FixValidationReport:
    checks: Tuple[Check, ...]
    spec: Any = None
    reasoning_flagged: bool = False

    @property
    def accepted(self) -> bool:
        return all(c.passed for c in self.checks) and self.spec is not None

    @property
    def rejections(self) -> Tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.passed)

    def as_dict(self) -> dict:
        return {
            "checks": [c.as_dict() for c in self.checks],
            "passed": sum(1 for c in self.checks if c.passed),
            "total": len(self.checks),
            "accepted": self.accepted,
            "reasoning_flagged": self.reasoning_flagged,
        }


def _contains_verdict(text) -> bool:
    lowered = str(text).lower()
    return any(token in lowered for token in VERDICT_TOKENS)


def _looks_like_a_path(target) -> bool:
    if not isinstance(target, str) or not target:
        return True
    forbidden = ("/", "\\", "..", ":", "\x00")
    return any(token in target for token in forbidden)


def _schema_ok(raw: Mapping) -> Tuple[bool, str]:
    if not isinstance(raw, dict):
        return False, "fix is not an object"
    missing = [k for k in FIX_SCHEMA["required"] if k not in raw]
    if missing:
        return False, "missing %s" % ", ".join(missing)
    extra = [k for k in raw if k not in FIX_SCHEMA["properties"]]
    if extra:
        return False, "unexpected field(s): %s" % ", ".join(sorted(extra))
    operation = raw["operation"]
    if not isinstance(operation, dict):
        return False, "operation is not an object"
    op_schema = FIX_SCHEMA["properties"]["operation"]
    op_missing = [k for k in op_schema["required"] if k not in operation]
    if op_missing:
        return False, "operation is missing %s" % ", ".join(op_missing)
    op_extra = [k for k in operation if k not in op_schema["properties"]]
    if op_extra:
        return False, "operation carries extra key(s): %s" % ", ".join(sorted(op_extra))
    for key in ("type", "target", "before", "after"):
        if not isinstance(operation[key], str):
            return False, "operation.%s is not a string" % key
    if not isinstance(raw["summary"], str) or not isinstance(raw["reasoning_summary"], str):
        return False, "summary/reasoning_summary must be strings"
    return True, "all required fields present, no extras"


def validate(raw: Mapping, request: FixRequest) -> FixValidationReport:
    """Every rule that stands between a fix proposal and the sandbox."""
    checks = []

    ok, detail = _schema_ok(raw)
    checks.append(Check("schema", ok, detail))
    if not ok:
        return FixValidationReport(tuple(checks))

    hypothesis = raw["hypothesis_id"]
    operation = raw["operation"]
    op_type, target = operation["type"], operation["target"]
    before, after = operation["before"], operation["after"]

    matches_request = hypothesis == request.hypothesis_id
    checks.append(Check(
        "hypothesis_matches_request", matches_request,
        "%s is %sthe hypothesis this fix was requested for (%s)"
        % (hypothesis, "" if matches_request else "NOT ", request.hypothesis_id)))

    proven = request.causal_verdict == verdict.PROVEN
    checks.append(Check(
        "hypothesis_proven", proven,
        "the causal verdict for %s is %s%s"
        % (request.hypothesis_id, request.causal_verdict,
           "" if proven else " - a fix may only be requested for PROVEN")))

    no_path = not _looks_like_a_path(target)
    checks.append(Check(
        "target_no_path_traversal", no_path,
        "target %r is %sa bare symbolic name, never a filesystem path"
        % (target, "" if no_path else "NOT ")))

    surface = repair.repair_surface(hypothesis, target) if no_path else None
    surface_ok = surface is not None and target in request.repair_targets
    checks.append(Check(
        "target_is_known_repair_surface", surface_ok,
        "%r is %sa whitelisted repair surface for %s (%s)"
        % (target, "" if surface_ok else "NOT ", hypothesis,
           ", ".join(request.repair_targets) or "none registered")))

    type_ok = (surface_ok and op_type in ALLOWED_OPERATION_TYPES
              and op_type == surface.get("operation_type"))
    checks.append(Check(
        "operation_type_allowed", type_ok,
        "operation type %r is %san allowed, registered repair for this target"
        % (op_type, "" if type_ok else "NOT ")))

    before_ok = surface_ok and repair.matches_current(hypothesis, target, before)
    checks.append(Check(
        "before_state_matches_sandbox", before_ok,
        "the proposed before-state %sthe sandbox fixture's current value"
        % ("matches " if before_ok else "does NOT match ")))

    after_ok = surface_ok and repair.is_safe_after(hypothesis, target, after)
    checks.append(Check(
        "after_state_is_a_known_safe_repair", after_ok,
        "the proposed after-state is %sthe known-safe repair for this surface"
        % ("" if after_ok else "NOT ")))

    structural = [hypothesis, op_type, target, before, after]
    offending_keys = [k for k in raw if k.lower() in VERDICT_KEYS]
    offending_keys += ["operation.%s" % k for k in operation if k.lower() in VERDICT_KEYS]
    no_verdict = not offending_keys and not any(
        _contains_verdict(v) for v in structural if isinstance(v, str))
    checks.append(Check(
        "no_encoded_verdict", no_verdict,
        "no field the engine reads carries a conclusion" if no_verdict
        else "verdict encoded in %s" % (", ".join(offending_keys) or "a structural field")))

    reasoning_flagged = (_contains_verdict(raw["reasoning_summary"])
                        or _contains_verdict(raw["summary"]))

    spec = None
    if all(c.passed for c in checks):
        spec = FixSpec(
            hypothesis_id=hypothesis,
            summary=raw["summary"],
            operation=FixOperation(type=op_type, target=target, before=before, after=after),
            reasoning_summary=raw["reasoning_summary"],
        )
    return FixValidationReport(tuple(checks), spec, reasoning_flagged)
