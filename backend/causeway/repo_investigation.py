"""The repository-backed investigation.

    GitHub URL + user intent
      -> validated clone, repository-owned database, hypotheses read from
         the repository's own source
      -> Gemini designs an experiment per hypothesis
      -> deterministic validator
      -> a disposable SOURCE VARIANT per phase, launched and measured
      -> deterministic verdict
      -> (if the intent allows) a fix, validated, applied to another
         disposable copy, and verified against the same workload

This module is deliberately separate from causeway.orchestrator's bundled
demo path, and deliberately does not import causeway.incident,
causeway.localizer or causeway.observational. A repository investigation must
not be able to reach the A/B fixture even by accident, and
tests/test_repo_path_isolation.py walks this module's import graph to keep it
that way - the same technique that already keeps a model away from the
verdict.
"""
from __future__ import annotations

import time
from typing import Iterator, Mapping, Sequence

from causeway import fix_verdict, fixer, phasing, planner, repository, verdict
from causeway.sandbox.actuator import SourceActuator
from causeway.sandbox.runner import REPETITIONS

_COMPLETES = {after: phase for phase, (_, after) in verdict.BRACKETS.items()}
_FIX_COMPLETES = {after: phase for phase, (_, after) in fix_verdict.FIX_BRACKETS.items()}


def _stage(name: str, status: str, **extra) -> dict:
    return dict({"type": "stage", "stage": name, "status": status,
                 "t": round(time.time(), 3)}, **extra)


def acquire(repository_url: str):
    """Validate, clone and load, yielding lifecycle events.

    Yields a RepositoryContext as its last item on success, or None after a
    `repository_rejected` event - so a caller can tell the two apart without
    inspecting event dicts. Nothing before that point touches a sandbox.
    """
    yield {"type": "repository_validating", "url": repository_url}
    try:
        ref = repository.validate_url(repository_url)
    except repository.RepositoryRejected as exc:
        yield {"type": "repository_rejected", "stage": exc.stage, "reason": exc.reason}
        yield None
        return

    yield {"type": "repository_cloning", "owner": ref.owner, "name": ref.name,
           "url": ref.url}
    try:
        cloned = repository.clone(ref)
    except repository.RepositoryRejected as exc:
        yield {"type": "repository_rejected", "stage": exc.stage, "reason": exc.reason}
        yield None
        return

    try:
        context = repository.load(cloned, ref)
    except repository.RepositoryRejected as exc:
        cloned.cleanup()
        yield {"type": "repository_rejected", "stage": exc.stage, "reason": exc.reason}
        yield None
        return

    yield dict({"type": "repository_loaded"}, **context.as_event())
    yield context


def _experiment(actuator, context, hypothesis_id: str, state: Mapping[str, bool],
                specs: Sequence, reps: int):
    """Measure the seven phases for one hypothesis. Yields events; the last
    item yielded is the list of PhaseResults."""
    results = []
    for spec in specs:
        yield {"type": "phase_start", "hypothesis": hypothesis_id,
               "phase": spec.phase,
               "intervention": actuator.describe(spec.flags)}
        observed = actuator.measure(context.workload, spec.flags, reps)
        results.append(verdict.PhaseResult(spec, observed))
        yield {"type": "phase_result", "hypothesis": hypothesis_id,
               "phase": spec.phase,
               "role": "control" if spec.phase in verdict.CONTROL_PHASES else "evidence",
               "p95_ms": observed["p95_ms"], "p50_ms": observed["p50_ms"],
               "reps": observed.get("reps", 1),
               "error_rate": observed.get("error_rate", 0.0),
               "applied": [e.as_dict() for e in actuator.last_applied]}

        judged = _COMPLETES.get(spec.phase)
        if judged:
            yield phasing.judged_event(results, judged, verdict.BRACKETS,
                                       verdict.METRIC, hypothesis_id, "phase_judged")
    yield results


def investigate(context, intent, reps: int = None, offline: bool = None
                ) -> Iterator[dict]:
    """Run the investigation against an already-loaded RepositoryContext."""
    reps = REPETITIONS if reps is None else reps
    started = time.time()

    incident = dict(context.incident)
    yield _stage("incident_detected", "done")
    yield {"type": "incident", "incident": incident,
           "workload": {"id": context.workload["id"],
                        "requests": len(context.workload["requests"]),
                        "concurrency": context.workload["concurrency"]},
           "database": context.as_event()["database"],
           "repetitions": reps, "verification": context.verification}

    # ---- 1. analysis: hypotheses read out of the repository's own source ----
    yield _stage("analysis", "running")
    hypotheses = list(context.hypotheses)
    testable = [h for h in hypotheses if h.testable]
    yield {"type": "hypotheses",
           "hypotheses": [h.as_dict() for h in hypotheses],
           "testable": [h.id for h in testable],
           "sources": list(context.sources),
           "detectors": sorted({h.detector for h in hypotheses})}
    yield _stage("analysis", "done")

    if len(testable) < 2:
        yield {"type": "error",
               "message": ("only %d testable hypothesis was found - an experiment "
                           "needs at least two to discriminate between"
                           % len(testable))}
        return

    state = {h.id: True for h in testable}
    order = [h.id for h in testable]

    # ---- 2. planning + validation (AI proposes, code validates) -------------
    provider = planner.default_provider(offline=offline)
    outcomes = {}
    yield _stage("planning", "running", planner=provider.name)
    for hypothesis_id in order:
        request = planner.build_request_for_code(
            incident, testable, state, [context.workload["id"]], hypothesis_id)
        outcome = planner.plan_experiment(request, provider)
        outcomes[hypothesis_id] = outcome
        yield dict({"type": "plan", "hypothesis": hypothesis_id}, **outcome.as_dict())
    yield _stage("planning", "done")

    yield _stage("validation", "running")
    for hypothesis_id in order:
        yield {"type": "validation", "hypothesis": hypothesis_id,
               **outcomes[hypothesis_id].report.as_dict()}
    yield _stage("validation", "done")

    # ---- 3. the experiments -------------------------------------------------
    verdicts, details, reasons = {}, {}, {}
    actuator = SourceActuator(context.workspace, context.entrypoint,
                              context.database_path, context.work_db, testable)
    yield _stage("experiment", "running", actuator=actuator.kind)
    for hypothesis_id in order:
        plan = outcomes[hypothesis_id].plan
        specs = planner.phases_for(plan, state)
        hypothesis = context.hypothesis(hypothesis_id)
        yield {"type": "experiment_start", "hypothesis": hypothesis_id,
               "label": hypothesis.label, "file": hypothesis.file,
               "line": hypothesis.line, "symbol": hypothesis.symbol,
               "observed": hypothesis.observed,
               "counterfactual": hypothesis.counterfactual,
               "phases": [s.phase for s in specs],
               "holding_fixed": [h for h in order if h != hypothesis_id]}

        results = None
        for item in _experiment(actuator, context, hypothesis_id, state, specs, reps):
            if isinstance(item, list):
                results = item
            else:
                yield item

        results = verdict.annotate(results)
        decision = verdict.decide(results)
        verdicts[hypothesis_id] = decision
        details[hypothesis_id] = verdict.explain(results)
        reasons[hypothesis_id] = verdict.reason(results)
        yield {"type": "verdict", "hypothesis": hypothesis_id, "verdict": decision,
               "reason": reasons[hypothesis_id], "detail": details[hypothesis_id],
               "phases": [{"phase": r.spec.phase,
                           "p95_ms": r.observed[verdict.METRIC],
                           "passed": r.passed} for r in results]}
    yield _stage("experiment", "done")

    proven = [h for h in order if verdicts[h] == verdict.PROVEN]
    refuted = [h for h in order if verdicts[h] == verdict.REFUTED]
    yield {"type": "conclusion", "verdicts": verdicts, "proven": proven,
           "refuted": refuted,
           "proven_labels": [context.hypothesis(h).label for h in proven],
           "elapsed_s": round(time.time() - started, 1), "details": details}

    # ---- 4. the fix loop, only if the intent allows a persistent change -----
    if not intent.allows_fix:
        yield {"type": "fix_skipped", "reason": intent.no_fix_reason,
               "mode": intent.mode}
        yield {"type": "done", "elapsed_s": round(time.time() - started, 1)}
        return

    for hypothesis_id in proven:
        for event in _fix(context, intent, actuator, hypothesis_id,
                          verdicts[hypothesis_id], reasons[hypothesis_id],
                          state, reps, offline):
            yield event

    yield {"type": "done", "elapsed_s": round(time.time() - started, 1)}


def _fix(context, intent, actuator, hypothesis_id, decision, reason_text,
         state, reps, offline):
    """Propose, validate, apply and verify a persistent repair for a proven
    hypothesis - always against a disposable copy, never the clone."""
    hypothesis = context.hypothesis(hypothesis_id)
    yield {"type": "root_cause_proven", "hypothesis": hypothesis_id,
           "label": hypothesis.label, "verdict": decision}

    # Two gates, both deterministic, both before a planner is asked anything.
    # A file the repository did not declare patchable, or that the user's own
    # instruction put out of scope, is not a file a fix may be proposed for -
    # and saying so is more useful than proposing one and refusing to apply it.
    if hypothesis.file not in context.patchable:
        yield {"type": "fix_blocked", "hypothesis": hypothesis_id,
               "file": hypothesis.file, "scope": "repository",
               "reason": ("%s is not declared patchable by this repository's "
                          "manifest (%s)"
                          % (hypothesis.file, ", ".join(context.patchable) or "none"))}
        return
    permitted, why = intent.permits_file(hypothesis.file)
    if not permitted:
        yield {"type": "fix_blocked", "hypothesis": hypothesis_id,
               "file": hypothesis.file, "scope": "intent", "reason": why}
        return

    yield _stage("fix_planning", "running", hypothesis=hypothesis_id)
    try:
        request = fixer.build_code_fix_request(hypothesis, decision, reason_text,
                                               context.workspace, intent)
    except fixer.FixSurfaceUnavailable as exc:
        yield {"type": "fix_blocked", "hypothesis": hypothesis_id,
               "file": hypothesis.file, "scope": "repository", "reason": str(exc)}
        yield _stage("fix_planning", "done")
        return
    provider = fixer.default_fix_provider(offline=offline)
    outcome = fixer.plan_fix(request, provider)
    yield dict({"type": "fix_plan", "hypothesis": hypothesis_id}, **outcome.as_dict())
    yield _stage("fix_planning", "done")

    yield _stage("fix_validation", "running")
    yield {"type": "fix_validation", "hypothesis": hypothesis_id,
           **outcome.report.as_dict()}
    yield _stage("fix_validation", "done")
    if not outcome.report.accepted:
        yield {"type": "error",
               "message": "no safe fix could be validated for %s" % hypothesis.label}
        return

    op = outcome.spec.operation
    edit = fixer.edit_for(op, hypothesis)
    yield {"type": "fix_apply", "hypothesis": hypothesis_id,
           "file": hypothesis.file, "label": hypothesis.label,
           "summary": outcome.spec.summary, "operation": op.as_dict(),
           "diff": fixer.unified_diff(context.workspace, hypothesis, edit),
           "applied_to": "a disposable copy of the repository - the clone and "
                         "the original repository are never written to"}

    yield _stage("fix_experiment", "running")
    fix_specs = fix_verdict.fix_phase_specs(hypothesis_id, state,
                                            context.workload["id"])
    yield {"type": "fix_experiment_start", "hypothesis": hypothesis_id,
           "phases": [s.phase for s in fix_specs]}

    results = []
    for spec in fix_specs:
        patched = spec.phase in fix_verdict.FIX_PATCHED_PHASES
        extra = (edit,) if patched else ()
        yield {"type": "fix_phase_start", "hypothesis": hypothesis_id,
               "phase": spec.phase, "patched": patched,
               "intervention": actuator.describe(spec.flags, extra)}
        observed = actuator.measure(context.workload, spec.flags, reps,
                                    extra_edits=extra)
        results.append(verdict.PhaseResult(spec, observed))
        yield {"type": "fix_phase_result", "hypothesis": hypothesis_id,
               "phase": spec.phase, "patched": patched,
               "role": ("control" if spec.phase in fix_verdict.FIX_CONTROL_PHASES
                        else "evidence"),
               "p95_ms": observed["p95_ms"], "p50_ms": observed["p50_ms"],
               "reps": observed.get("reps", 1),
               "error_rate": observed.get("error_rate", 0.0),
               "applied": [e.as_dict() for e in actuator.last_applied]}
        judged = _FIX_COMPLETES.get(spec.phase)
        if judged:
            yield phasing.judged_event(results, judged, fix_verdict.FIX_BRACKETS,
                                       fix_verdict.METRIC, hypothesis_id,
                                       "fix_phase_judged")

    results = fix_verdict.annotate(results)
    yield {"type": "fix_verdict", "hypothesis": hypothesis_id,
           "verdict": fix_verdict.decide(results),
           "reason": fix_verdict.reason(results),
           "phases": [{"phase": r.spec.phase,
                       "p95_ms": r.observed[fix_verdict.METRIC],
                       "passed": r.passed} for r in results]}
    yield _stage("fix_experiment", "done")
