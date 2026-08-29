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
"""
from __future__ import annotations

import json
import time
from typing import Iterator

from causeway import config, observational, planner, verdict
from causeway.incident import deploy_record
from causeway.localizer import localize
from causeway.sandbox.replay import load_fixture
from causeway.sandbox.runner import REPETITIONS, Sandbox

# The phase whose measurement completes the bracket around an evidence phase,
# i.e. the moment that evidence phase becomes judgeable.
_COMPLETES = {after: phase for phase, (_, after) in verdict.BRACKETS.items()}

_STATE_LABEL = {
    verdict.PHASE_CONTROL_1: "control",
    verdict.PHASE_CONTROL_2: "control",
    verdict.PHASE_CONTROL_3: "control",
    verdict.PHASE_CONTROL_4: "control",
}


def _stage(name: str, status: str, **extra) -> dict:
    return dict({"type": "stage", "stage": name, "status": status,
                 "t": round(time.time(), 3)}, **extra)


def investigate(repetitions: int = None, offline: bool = None) -> Iterator[dict]:
    """Run the whole investigation, yielding events as they happen."""
    reps = config.repetitions(REPETITIONS if repetitions is None else repetitions)
    # Gemini when a key is configured, the deterministic planner otherwise.
    # CAUSEWAY_OFFLINE=1 forces deterministic whatever is in the environment.
    use_offline = config.offline() if offline is None else bool(offline)
    started = time.time()

    if not config.is_ready():
        yield {"type": "error",
               "message": "this machine is not seeded yet - run: python -m causeway.cli seed"}
        return

    fixture = load_fixture(config.FIXTURE_PATH)
    with open(config.CALIBRATION_PATH, "r", encoding="utf-8") as handle:
        calibration = json.load(handle)

    # ---- 1. the incident ------------------------------------------------
    record = deploy_record()
    incident = record["incident"]
    yield _stage("incident_detected", "done")
    yield {"type": "incident", "incident": incident, "calibration": calibration,
           "fixture": {"id": fixture["id"],
                       "requests": len(fixture["requests"]),
                       "concurrency": fixture["concurrency"],
                       "recorded_from": fixture.get("recorded_from", "")},
           "repetitions": reps}

    # ---- 2. localisation (deterministic) --------------------------------
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

    # ---- 3. observational baseline (no experiment, no measurement) ------
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

    # ---- 4. planning + validation (AI proposes, code validates) ---------
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

    # ---- 5. the experiments (system experiments, measurements decide) ---
    verdicts, details = {}, {}
    yield _stage("experiment", "running")
    with Sandbox(config.TEMPLATE_DB, config.WORK_DB) as sandbox:
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
            verdicts[change_id] = decision
            details[change_id] = detail
            yield {"type": "verdict", "hypothesis": change_id,
                   "verdict": decision, "reason": verdict.reason(results),
                   "detail": detail,
                   "phases": [{"phase": r.spec.phase,
                               "p95_ms": r.observed[verdict.METRIC],
                               "passed": r.passed,
                               "expected": r.spec.expected or None}
                              for r in results]}
    yield _stage("experiment", "done")

    # ---- 6. the contrast ------------------------------------------------
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
    yield {"type": "done", "elapsed_s": round(time.time() - started, 1)}
