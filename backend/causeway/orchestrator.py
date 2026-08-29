"""The investigation, start to finish, as a stream of events.

    AI PROPOSES.  CODE VALIDATES.  SYSTEM EXPERIMENTS.  MEASUREMENTS DECIDE.

Each of those four is a separate stage below, and each emits its own events so
the interface can show the boundary rather than describe it. The orchestrator
sequences the stages and carries data between them; it makes no judgements of
its own. The only place a candidate is confirmed or refuted is
causeway.verdict.decide, which is called with measurements and nothing else.

Events are plain dicts. The CLI prints them; the API will stream the same
objects over SSE, so what a judge sees in the browser is what the engine
actually emitted.

Milestone 6 adds one more optional front door: `repository_url`. When given,
the investigation runs against a repository cloned into a disposable
workspace instead of the bundled demo - see causeway.repository for what
"supported" means and why a repository that fails any check there never
reaches the sandbox. Every other stage below is unchanged either way; they
read `fixture`/`record`/`service_path`/`surfaces`, which are simply sourced
from the repository when one was loaded, and from the bundled demo otherwise.
"""
from __future__ import annotations

import json
import os
import time
from typing import Iterator

from causeway import config, fix_verdict, fixer, observational, planner, repository, verdict
from causeway.incident import deploy_record
from causeway.localizer import localize
from causeway.sandbox import fixapply
from causeway.sandbox.replay import load_fixture
from causeway.sandbox.runner import REPETITIONS, Sandbox

# The phase whose measurement completes the bracket around an evidence phase,
# i.e. the moment that evidence phase becomes judgeable.
_COMPLETES = {after: phase for phase, (_, after) in verdict.BRACKETS.items()}
_FIX_COMPLETES = {after: phase for phase, (_, after) in fix_verdict.FIX_BRACKETS.items()}

_STATE_LABEL = {
    verdict.PHASE_CONTROL_1: "control",
    verdict.PHASE_CONTROL_2: "control",
    verdict.PHASE_CONTROL_3: "control",
    verdict.PHASE_CONTROL_4: "control",
}


def _stage(name: str, status: str, **extra) -> dict:
    return dict({"type": "stage", "stage": name, "status": status,
                 "t": round(time.time(), 3)}, **extra)


def _fix_phase_events(sandbox, spec, fixture, reps, change_id, fix_results):
    """Measure one fix-protocol phase and return the events it produces, in
    order. A plain function rather than another generator, so the two
    sandbox `with` blocks in the fix loop below can share one flat list of
    events without nesting generators inside a generator."""
    events = [{"type": "fix_phase_start", "hypothesis": change_id,
               "phase": spec.phase, "flags": dict(spec.flags)}]
    observed = sandbox.measure(fixture, spec.flags, reps)
    fix_results.append(verdict.PhaseResult(spec, observed))
    events.append({"type": "fix_phase_result", "hypothesis": change_id,
                   "phase": spec.phase,
                   "role": ("control" if spec.phase in fix_verdict.FIX_CONTROL_PHASES
                            else "evidence"),
                   "p95_ms": observed["p95_ms"], "p50_ms": observed["p50_ms"],
                   "reps": observed.get("reps", 1),
                   "error_rate": observed.get("error_rate", 0.0)})

    judged = _FIX_COMPLETES.get(spec.phase)
    if judged:
        before, after = fix_verdict.FIX_BRACKETS[judged]
        values = {r.spec.phase: r.observed[fix_verdict.METRIC] for r in fix_results}
        control = verdict.local_control(values[before], values[after])
        value = values[judged]
        stable = verdict.controls_agree(values[before], values[after])
        if not stable:
            state = "unstable"
        elif verdict.failure_present(value, control):
            state = "broken"
        elif verdict.recovered(value, control):
            state = "healthy"
        else:
            state = "inconclusive"
        events.append({"type": "fix_phase_judged", "hypothesis": change_id,
                       "phase": judged, "state": state, "p95_ms": value,
                       "local_control_ms": round(control, 3),
                       "ratio": round(value / control, 2) if control else None,
                       "controls_agree": stable,
                       "drift": round(verdict.drift(values[before], values[after]), 3)})
    return events


def _acquire_repository(repository_url: str):
    """Validate, clone and load a repository against the Causeway demo
    contract, yielding the lifecycle events as it goes.

    The last thing this yields is either a `repository_loaded` event
    followed by the built `RepositoryContext` itself, or a
    `repository_rejected` event followed by `None` - so a caller can tell
    the two apart without inspecting event dicts. Nothing before that point
    touches the sandbox: a rejected repository never gets that far.
    """
    yield {"type": "repository_validating", "url": repository_url}
    try:
        ref = repository.validate_url(repository_url)
    except repository.RepositoryRejected as exc:
        yield {"type": "repository_rejected", "stage": exc.stage, "reason": exc.reason}
        yield None
        return

    yield {"type": "repository_cloning", "owner": ref.owner, "name": ref.name, "url": ref.url}
    try:
        cloned = repository.clone(ref)
    except repository.RepositoryRejected as exc:
        yield {"type": "repository_rejected", "stage": exc.stage, "reason": exc.reason}
        yield None
        return

    try:
        repo_ctx = repository.load(cloned, ref)
    except repository.RepositoryRejected as exc:
        cloned.cleanup()
        yield {"type": "repository_rejected", "stage": exc.stage, "reason": exc.reason}
        yield None
        return

    yield dict({"type": "repository_loaded"}, **repo_ctx.as_event())
    yield repo_ctx


def investigate(repetitions: int = None, offline: bool = None,
                repository_url: str = None) -> Iterator[dict]:
    """Run the whole investigation, yielding events as they happen."""
    reps = config.repetitions(REPETITIONS if repetitions is None else repetitions)
    # Gemini when a key is configured, the deterministic planner otherwise.
    # CAUSEWAY_OFFLINE=1 forces deterministic whatever is in the environment.
    use_offline = config.offline() if offline is None else bool(offline)
    started = time.time()

    repo_ctx = None
    try:
        if repository_url:
            rejected = True
            for event in _acquire_repository(repository_url):
                if isinstance(event, repository.RepositoryContext):
                    repo_ctx = event
                    rejected = False
                elif event is None:
                    pass
                else:
                    yield event
            if rejected:
                return

        if not config.is_ready():
            yield {"type": "error",
                   "message": "this machine is not seeded yet - run: python -m causeway.cli seed"}
            return

        fixture = repo_ctx.fixture if repo_ctx else load_fixture(config.FIXTURE_PATH)
        calibration = {}
        if os.path.exists(config.CALIBRATION_PATH):
            with open(config.CALIBRATION_PATH, "r", encoding="utf-8") as handle:
                calibration = json.load(handle)

        # ---- 1. the incident ----------------------------------------------
        record = repo_ctx.incident_record if repo_ctx else deploy_record()
        incident = record["incident"]
        yield _stage("incident_detected", "done")
        yield {"type": "incident", "incident": incident, "calibration": calibration,
               "fixture": {"id": fixture["id"],
                           "requests": len(fixture["requests"]),
                           "concurrency": fixture["concurrency"],
                           "recorded_from": fixture.get("recorded_from", "")},
               "repetitions": reps}

        # ---- 2. localisation (deterministic) -------------------------------
        yield _stage("localization", "running")
        candidates, excluded = localize(record)
        yield {"type": "candidates",
               "candidates": [c.as_dict() for c in candidates],
               "excluded": [e.as_dict() for e in excluded],
               "deploys_considered": len(record["deploys"])}
        yield _stage("localization", "done")

        if len(candidates) < 2:
            yield {"type": "error", "message": "fewer than two candidates - nothing to discriminate"}
            return

        # ---- 3. observational baseline (no experiment, no measurement) -----
        yield _stage("observational", "running")
        assessments = observational.rank(candidates, incident)
        suspect = observational.top_suspect(assessments)
        yield {"type": "observational",
               "assessments": [a.as_dict() for a in assessments],
               "top_suspect": suspect,
               "weights": observational.WEIGHTS,
               "margin": round(assessments[0].score - assessments[-1].score, 3)}
        yield _stage("observational", "done")

        incident_state = {c.change_id: True for c in candidates}
        order = [c.change_id for c in candidates]

        # ---- 4. planning + validation (AI proposes, code validates) --------
        provider = planner.default_provider(offline=use_offline)
        outcomes = {}
        yield _stage("planning", "running", planner=provider.name)
        for change_id in order:
            # the observational scores go in as pre-experiment evidence; nothing
            # measured during an experiment can, and PlanRequest has no field for it
            request = planner.build_request(incident, candidates, incident_state,
                                            [fixture["id"]], change_id,
                                            observational=assessments)
            outcome = planner.plan_experiment(request, provider)
            outcomes[change_id] = outcome
            yield dict({"type": "plan", "hypothesis": change_id}, **outcome.as_dict())
        yield _stage("planning", "done")

        yield _stage("validation", "running")
        for change_id in order:
            yield {"type": "validation", "hypothesis": change_id,
                   **outcomes[change_id].report.as_dict()}
        yield _stage("validation", "done")

        # ---- 5. the experiments (system experiments, measurements decide) --
        service_path = repo_ctx.entrypoint_path if repo_ctx else None
        surfaces = repo_ctx.repair_surfaces if repo_ctx else None

        verdicts, details, reasons = {}, {}, {}
        yield _stage("experiment", "running")
        with Sandbox(config.TEMPLATE_DB, config.WORK_DB, service_path=service_path) as sandbox:
            for change_id in order:
                plan = outcomes[change_id].plan
                specs = planner.phases_for(plan, incident_state)
                yield {"type": "experiment_start", "hypothesis": change_id,
                       "phases": [s.phase for s in specs],
                       "intervention": plan.intervention,
                       "holding_fixed": [c for c in order if c != change_id]}

                results = []
                for spec in specs:
                    yield {"type": "phase_start", "hypothesis": change_id,
                           "phase": spec.phase, "flags": dict(spec.flags)}
                    observed = sandbox.measure(fixture, spec.flags, reps)
                    results.append(verdict.PhaseResult(spec, observed))
                    yield {"type": "phase_result", "hypothesis": change_id,
                           "phase": spec.phase, "role": _STATE_LABEL.get(spec.phase, "evidence"),
                           "p95_ms": observed["p95_ms"], "p50_ms": observed["p50_ms"],
                           "reps": observed.get("reps", 1),
                           "error_rate": observed.get("error_rate", 0.0)}

                    # The moment a bracket closes, its evidence phase becomes
                    # judgeable - so say so, rather than making the UI wait.
                    judged = _COMPLETES.get(spec.phase)
                    if judged:
                        before, after = verdict.BRACKETS[judged]
                        values = {r.spec.phase: r.observed[verdict.METRIC] for r in results}
                        control = verdict.local_control(values[before], values[after])
                        value = values[judged]
                        stable = verdict.controls_agree(values[before], values[after])
                        if not stable:
                            state = "unstable"
                        elif verdict.failure_present(value, control):
                            state = "broken"
                        elif verdict.recovered(value, control):
                            state = "healthy"
                        else:
                            state = "inconclusive"
                        yield {"type": "phase_judged", "hypothesis": change_id,
                               "phase": judged, "state": state,
                               "p95_ms": value,
                               "local_control_ms": round(control, 3),
                               "ratio": round(value / control, 2) if control else None,
                               "controls_agree": stable,
                               "drift": round(verdict.drift(values[before], values[after]), 3)}

                results = verdict.annotate(results)
                decision = verdict.decide(results)
                detail = verdict.explain(results)
                reason_text = verdict.reason(results)
                verdicts[change_id] = decision
                details[change_id] = detail
                reasons[change_id] = reason_text
                yield {"type": "verdict", "hypothesis": change_id,
                       "verdict": decision, "reason": reason_text,
                       "detail": detail,
                       "phases": [{"phase": r.spec.phase,
                                   "p95_ms": r.observed[verdict.METRIC],
                                   "passed": r.passed,
                                   "expected": r.spec.expected or None}
                                  for r in results]}
        yield _stage("experiment", "done")

        # ---- 6. the contrast -------------------------------------------------
        proven = [c for c in order if verdicts[c] == verdict.PROVEN]
        refuted = [c for c in order if verdicts[c] == verdict.REFUTED]
        yield {"type": "conclusion",
               "observational_top_suspect": suspect,
               "verdicts": verdicts,
               "proven": proven,
               "refuted": refuted,
               "correlation_selected_decoy": bool(proven) and suspect in refuted,
               "elapsed_s": round(time.time() - started, 1),
               "details": details}

        # ---- 7. the fix loop, only for what was PROVEN ------------------------
        #
        #   root_cause_proven -> Gemini FixSpec -> deterministic fix validator ->
        #   sandbox fix application (disposable copy) -> identical workload
        #   replay -> deterministic measurements -> deterministic fix verdict.
        #
        # A REFUTED candidate never reaches this loop at all: `proven` is built
        # from the same verdicts dict the conclusion above already rendered, so
        # there is no separate policy decision here to drift out of sync with it.
        fix_provider = None
        for change_id in proven:
            if not fixer.fixable(verdicts[change_id], change_id, surfaces=surfaces):
                continue
            yield {"type": "root_cause_proven", "hypothesis": change_id,
                   "verdict": verdicts[change_id]}

            candidate = next(c.as_dict() for c in candidates if c.change_id == change_id)
            fix_request = fixer.build_fix_request(candidate, change_id,
                                                  verdicts[change_id], reasons[change_id],
                                                  surfaces=surfaces)
            if fix_provider is None:
                fix_provider = fixer.default_fix_provider(offline=use_offline)

            yield _stage("fix_planning", "running", hypothesis=change_id,
                        planner=fix_provider.name)
            fix_outcome = fixer.plan_fix(fix_request, fix_provider)
            yield dict({"type": "fix_plan", "hypothesis": change_id}, **fix_outcome.as_dict())
            yield _stage("fix_planning", "done")

            yield _stage("fix_validation", "running")
            yield {"type": "fix_validation", "hypothesis": change_id,
                   **fix_outcome.report.as_dict()}
            yield _stage("fix_validation", "done")

            if not fix_outcome.report.accepted:
                # Should never happen - the deterministic fallback is the last
                # resort and is itself validated - but nothing is applied and
                # nothing further is claimed if it somehow does.
                yield {"type": "error",
                       "message": "no safe fix could be validated for %s" % change_id}
                continue

            op = fix_outcome.spec.operation
            yield _stage("fix_application", "running")
            applied = fixapply.apply(op.target, op.after, source_path=service_path)
            try:
                yield {"type": "fix_apply", "hypothesis": change_id,
                       "summary": fix_outcome.spec.summary, "operation": op.as_dict()}
                yield _stage("fix_application", "done")

                yield _stage("fix_experiment", "running")
                fix_specs = fix_verdict.fix_phase_specs(change_id, incident_state,
                                                        fixture["id"])
                yield {"type": "fix_experiment_start", "hypothesis": change_id,
                       "phases": [s.phase for s in fix_specs], "operation": op.as_dict()}
                fix_results = []

                # fix-control-1, fix-before: the ORIGINAL, unpatched sandbox
                # service - the same incident this candidate was proven against.
                with Sandbox(config.TEMPLATE_DB, config.WORK_DB,
                            service_path=service_path) as unpatched:
                    for spec in fix_specs[:2]:
                        for fx_event in _fix_phase_events(unpatched, spec, fixture, reps,
                                                          change_id, fix_results):
                            yield fx_event

                # fix-control-2, fix-after, fix-control-3: a disposable, patched
                # COPY of the service - the real source tree is never touched.
                with Sandbox(config.TEMPLATE_DB, config.WORK_DB,
                            service_path=applied.service_path) as patched:
                    for spec in fix_specs[2:]:
                        for fx_event in _fix_phase_events(patched, spec, fixture, reps,
                                                          change_id, fix_results):
                            yield fx_event

                fix_results = fix_verdict.annotate(fix_results)
                fix_decision = fix_verdict.decide(fix_results)
                yield {"type": "fix_verdict", "hypothesis": change_id,
                       "verdict": fix_decision, "reason": fix_verdict.reason(fix_results),
                       "phases": [{"phase": r.spec.phase,
                                   "p95_ms": r.observed[fix_verdict.METRIC],
                                   "passed": r.passed}
                                  for r in fix_results]}
                yield _stage("fix_experiment", "done")
            finally:
                # The disposable copy is removed whether the run succeeded,
                # failed validation of its own, or raised - never left behind.
                applied.cleanup()

        yield {"type": "done", "elapsed_s": round(time.time() - started, 1)}
    finally:
        # The cloned repository workspace is removed whether the run finished,
        # was rejected, or raised - never left behind, exactly like a fix
        # loop's own disposable patched copy above.
        if repo_ctx is not None:
            repo_ctx.cleanup()
