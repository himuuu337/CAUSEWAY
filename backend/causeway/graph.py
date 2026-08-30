"""THE CAUSAL GRAPH: a deterministic rendering of an investigation's own
event buffer, and nothing else.

    AI PROPOSES.  CODE VALIDATES.  SYSTEM EXPERIMENTS.  MEASUREMENTS DECIDE.

This module decides nothing. It is the backend twin of
frontend/src/graph.ts's buildCausalGraph: the same fold-then-render
discipline causeway.orchestrator's events already carry, replayed into
nodes and edges instead of into a browser's InvestigationState. Given the
same events, it always returns the same graph - the same guarantee
causeway.verdict makes for a verdict.

A relationship is only ever labelled with causal language ("verified causal
relationship") once causeway.verdict.decide has actually decided one. Before
that, every hypothesis - a deployed change or a repository code location -
is wired to the incident as a *suspected* cause, dashed rather than solid on
screen. Correlation is not causation, and this module is not where that
line gets crossed.

Node and edge shapes (and node/edge ids) are kept identical to
frontend/src/graph.ts on purpose: the same JSON this module returns from
`GET /api/investigation/{run_id}/graph` can be rendered by the exact same
React components the frontend already uses for its own live, event-by-event
derivation - two implementations of one contract, not two products.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

GraphNode = Dict[str, Any]
GraphEdge = Dict[str, Any]

# Correlation is not causation: every edge into an incident carries one of
# these, and only 'proven' is ever described on screen as a cause.
CAUSAL_LABEL: Dict[str, str] = {
    "candidate": "suspected cause",
    "proven": "verified causal relationship",
    "refuted": "refuted",
    "supported": "supported (one-sided)",
    "unresolved": "unresolved",
    "link": "",
}

_STRENGTH_FOR_VERDICT = {
    "PROVEN": "proven", "REFUTED": "refuted",
    "SUPPORTED": "supported", "UNRESOLVED": "unresolved",
}


def _strength_for(verdict: Optional[str]) -> str:
    return _STRENGTH_FOR_VERDICT.get(verdict or "", "candidate")


class _HypothesisView:
    """The narrow slice of HypothesisView the graph reads - not a copy of
    everything useInvestigation.ts tracks, only what a node or an edge is
    built from."""

    __slots__ = ("started", "verdict", "reason", "phases", "plan", "provenance", "validation")

    def __init__(self) -> None:
        self.started = False
        self.verdict: Optional[str] = None
        self.reason: Optional[str] = None
        self.phases: Dict[str, dict] = {}
        self.plan: Optional[dict] = None
        self.provenance: Optional[dict] = None
        self.validation: Optional[dict] = None


class _FixView:
    __slots__ = ("label", "fix", "provenance", "operation", "diff", "file", "verdict",
                "reason", "blocked")

    def __init__(self) -> None:
        self.label: Optional[str] = None
        self.fix: Optional[dict] = None
        self.provenance: Optional[dict] = None
        self.operation: Optional[dict] = None
        self.diff: Optional[str] = None
        self.file: Optional[str] = None
        self.verdict: Optional[str] = None
        self.reason: Optional[str] = None
        self.blocked: Optional[dict] = None


def _fold(events: Sequence[Mapping[str, Any]]) -> dict:
    """Replay the event buffer into exactly the state build_graph() needs.
    No event is interpreted beyond being filed - the same rule
    frontend/src/useInvestigation.ts's reduce() applies to the browser's own
    copy of this same buffer."""
    incident: Optional[dict] = None
    conclusion: Optional[dict] = None
    repository: Optional[dict] = None
    candidates: List[dict] = []
    found: List[dict] = []
    hypotheses: Dict[str, _HypothesisView] = {}
    order: List[str] = []
    fixes: Dict[str, _FixView] = {}
    fix_order: List[str] = []

    def hyp(hid: str) -> _HypothesisView:
        return hypotheses.setdefault(hid, _HypothesisView())

    def fix(hid: str) -> _FixView:
        return fixes.setdefault(hid, _FixView())

    for event in events:
        etype = event.get("type")

        if etype == "incident":
            incident = event.get("incident")
        elif etype == "candidates":
            candidates = list(event.get("candidates") or [])
        elif etype == "hypotheses":
            found = list(event.get("hypotheses") or [])
        elif etype == "plan":
            hid = event["hypothesis"]
            view = hyp(hid)
            view.plan = event.get("plan")
            view.provenance = event.get("provenance")
            if hid not in order:
                order.append(hid)
        elif etype == "validation":
            hyp(event["hypothesis"]).validation = {
                "checks": event.get("checks"), "passed": event.get("passed"),
                "total": event.get("total"), "accepted": event.get("accepted"),
                "reasoning_flagged": event.get("reasoning_flagged"),
            }
        elif etype == "experiment_start":
            view = hyp(event["hypothesis"])
            view.started = True
            view.phases = {
                phase: {"phase": phase, "role": "control" if phase.startswith("control") else "evidence"}
                for phase in (event.get("phases") or [])
            }
        elif etype == "phase_result":
            view = hyp(event["hypothesis"])
            row = view.phases.setdefault(event["phase"], {"phase": event["phase"]})
            row["role"] = event.get("role")
            row["p95_ms"] = event.get("p95_ms")
        elif etype == "phase_judged":
            view = hyp(event["hypothesis"])
            row = view.phases.setdefault(event["phase"], {"phase": event["phase"]})
            row["state"] = event.get("state")
            row["ratio"] = event.get("ratio")
            row["drift"] = event.get("drift")
        elif etype == "verdict":
            view = hyp(event["hypothesis"])
            view.verdict = event.get("verdict")
            view.reason = event.get("reason")
        elif etype == "conclusion":
            conclusion = dict(event)
        elif etype == "root_cause_proven":
            hid = event["hypothesis"]
            f = fix(hid)
            f.label = event.get("label")
            if hid not in fix_order:
                fix_order.append(hid)
        elif etype == "fix_blocked":
            f = fix(event["hypothesis"])
            f.blocked = {"scope": event.get("scope"), "reason": event.get("reason")}
            f.file = event.get("file")
        elif etype == "fix_plan":
            f = fix(event["hypothesis"])
            f.fix = event.get("fix")
            f.provenance = event.get("provenance")
        elif etype == "fix_apply":
            f = fix(event["hypothesis"])
            f.operation = event.get("operation")
            f.diff = event.get("diff")
            f.file = event.get("file") or f.file
            f.label = event.get("label") or f.label
        elif etype == "fix_verdict":
            f = fix(event["hypothesis"])
            f.verdict = event.get("verdict")
            f.reason = event.get("reason")
        elif etype == "repository_validating":
            repository = {"url": event.get("url"), "sources": [], "status": "validating"}
        elif etype == "repository_cloning":
            repository = dict(repository or {}, owner=event.get("owner"),
                              name=event.get("name"), status="cloning")
        elif etype == "repository_loaded":
            repository = {
                "url": event.get("url"), "owner": event.get("owner"), "name": event.get("name"),
                "commit_sha": event.get("commit_sha"), "runtime": event.get("runtime"),
                "primary_language": event.get("primary_language"), "entrypoint": event.get("entrypoint"),
                "sources": event.get("sources") or [], "status": "loaded",
            }
        elif etype == "repository_rejected":
            repository = dict(repository or {}, status="rejected")

    return {
        "incident": incident, "conclusion": conclusion, "repository": repository,
        "candidates": candidates, "found": found, "hypotheses": hypotheses,
        "order": order, "fixes": fixes, "fix_order": fix_order,
    }


def build_graph(events: Sequence[Mapping[str, Any]], incidents: Sequence[Any] = (),
                run_id: Optional[str] = None) -> dict:
    """The causal graph for one investigation.

    `events` is the run's own event buffer - the same list
    `GET /api/investigation/{run_id}/events` already returns. `incidents`,
    when given, is causeway.incidents.manager.all(): the live-monitoring
    incident list, used for exactly one thing - a `prediction` node, and
    only when one of those incidents' own `run_id` (set by
    IncidentManager's handoff, never guessed at here) matches this run.
    """
    view = _fold(events)
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []

    repo = view["repository"]
    if repo and repo.get("status") == "loaded":
        nodes.append({
            "id": "repository", "type": "repository",
            "label": repo.get("name") or repo.get("url"),
            "description": ("%s/%s" % (repo["owner"], repo["name"])
                            if repo.get("owner") and repo.get("name") else repo.get("url")),
            "status": "loaded",
            "metadata": {
                "url": repo.get("url"), "commitSha": repo.get("commit_sha"),
                "runtime": repo.get("runtime"), "primaryLanguage": repo.get("primary_language"),
                "entrypoint": repo.get("entrypoint"), "sources": repo.get("sources") or [],
            },
        })

    incident = view["incident"]
    has_incident = incident is not None
    if has_incident:
        conclusion = view["conclusion"]
        resolved = conclusion is not None
        proven_count = len(conclusion.get("proven") or []) if conclusion else 0
        nodes.append({
            "id": "incident", "type": "incident",
            "label": str(incident.get("title") or incident.get("id")),
            "description": str(incident.get("symptom") or ""),
            "status": ("root cause proven" if resolved and proven_count > 0
                      else "no cause proven" if resolved else "under investigation"),
            "metadata": {
                "id": incident.get("id"), "service": incident.get("service"),
                "detectedAt": incident.get("detected_at"),
            },
        })

    def wire_suspect(source_id: str, hypothesis_id: str) -> None:
        """One suspected-cause node wired through to its experiment and,
        once one exists, to the incident with the verdict's own strength -
        never a stronger one."""
        if not has_incident:
            return
        hview = view["hypotheses"].get(hypothesis_id)
        if hview is not None and hview.started:
            edges.append({
                "id": "e:%s->experiment:%s" % (source_id, hypothesis_id),
                "source": source_id, "target": "experiment:%s" % hypothesis_id,
                "label": "tested by", "strength": "link",
            })
            strength = _strength_for(hview.verdict)
            edges.append({
                "id": "e:experiment:%s->incident" % hypothesis_id,
                "source": "experiment:%s" % hypothesis_id, "target": "incident",
                "label": CAUSAL_LABEL[strength], "strength": strength,
            })
        else:
            edges.append({
                "id": "e:%s->incident" % source_id, "source": source_id, "target": "incident",
                "label": CAUSAL_LABEL["candidate"], "strength": "candidate",
            })

    for candidate in view["candidates"]:
        node_id = "candidate:%s" % candidate["change_id"]
        nodes.append({
            "id": node_id, "type": "candidate", "label": candidate.get("summary"),
            "description": "%s @ %s" % (candidate.get("branch"), (candidate.get("sha") or "")[:7]),
            "status": "deployed",
            "metadata": {
                "changeId": candidate.get("change_id"), "sha": candidate.get("sha"),
                "branch": candidate.get("branch"), "service": candidate.get("service"),
                "deployedAt": candidate.get("deployed_at"),
                "secondsBeforeDetection": candidate.get("seconds_before_detection"),
                "filesChanged": candidate.get("files_changed"),
                "linesChanged": candidate.get("lines_changed"),
                "changedFiles": candidate.get("changed_files") or [],
            },
        })
        wire_suspect(node_id, candidate["change_id"])

    for hypothesis in view["found"]:
        node_id = "code:%s" % hypothesis["id"]
        nodes.append({
            "id": node_id, "type": "code_change", "label": hypothesis.get("label"),
            "description": "%s:%s" % (hypothesis.get("file"), hypothesis.get("line")),
            "status": "testable" if hypothesis.get("testable") else "not testable",
            "metadata": {
                "file": hypothesis.get("file"), "line": hypothesis.get("line"),
                "symbol": hypothesis.get("symbol"), "kind": hypothesis.get("kind"),
                "observed": hypothesis.get("observed"), "counterfactual": hypothesis.get("counterfactual"),
                "evidence": hypothesis.get("evidence"), "reason": hypothesis.get("reason"),
                "detector": hypothesis.get("detector"),
            },
        })
        if repo:
            edges.append({
                "id": "e:repository->%s" % node_id, "source": "repository", "target": node_id,
                "label": "contains", "strength": "link",
            })
        wire_suspect(node_id, hypothesis["id"])

    for hid in view["order"]:
        hview = view["hypotheses"].get(hid)
        if hview is None or not hview.started:
            continue
        nodes.append({
            "id": "experiment:%s" % hid, "type": "experiment", "label": "Controlled experiment",
            "description": hview.reason or ("" if hview.verdict else "running"),
            "status": hview.verdict or "running",
            "metadata": {
                "phases": [
                    {"phase": p.get("phase"), "role": p.get("role"), "p95_ms": p.get("p95_ms"),
                     "state": p.get("state"), "ratio": p.get("ratio"), "drift": p.get("drift")}
                    for p in hview.phases.values()
                ],
                "plan": hview.plan, "provenance": hview.provenance, "validation": hview.validation,
            },
        })

    node_ids = {n["id"] for n in nodes}
    for hid in view["fix_order"]:
        f = view["fixes"].get(hid)
        if f is None:
            continue
        node_id = "fix:%s" % hid
        nodes.append({
            "id": node_id, "type": "fix",
            "label": ("Fix: %s" % f.label) if f.label else "Proposed fix",
            "description": ((f.fix or {}).get("summary") if f.fix
                            else (f.blocked or {}).get("reason") or ""),
            "status": f.verdict or ("blocked" if f.blocked else "proposed"),
            "metadata": {
                "file": f.file, "diff": f.diff, "operation": f.operation, "reason": f.reason,
                "provenance": f.provenance, "blocked": f.blocked,
            },
        })
        experiment_id = "experiment:%s" % hid
        if experiment_id in node_ids:
            edges.append({
                "id": "e:%s->%s" % (experiment_id, node_id),
                "source": experiment_id, "target": node_id,
                "label": "remediation proposed", "strength": "link",
            })

    # A prediction node appears only when the incident manager itself has
    # already tied a confirmed risk episode to this run (its handoff sets
    # Incident.run_id) - never inferred here from matching service names.
    if run_id and has_incident:
        linked = next((i for i in incidents if getattr(i, "run_id", None) == run_id), None)
        if linked is not None:
            nodes.append({
                "id": "prediction", "type": "prediction", "label": linked.predicted_failure,
                "description": "%s · risk %s/100" % (linked.detector, round(linked.risk_score)),
                "status": "predicted before incident",
                "metadata": {
                    "service": linked.service, "detector": linked.detector,
                    "riskScore": linked.risk_score, "evidence": list(linked.evidence),
                    "telemetryWindow": {
                        "current_values": dict(linked.current_values), "trends": dict(linked.trends),
                        "eta_seconds": linked.eta_seconds, "sample_count": linked.sample_count,
                    },
                    "createdAt": linked.created_at,
                },
            })
            edges.append({
                "id": "e:prediction->incident", "source": "prediction", "target": "incident",
                "label": "predicted before incident", "strength": "link",
            })

    return {"nodes": nodes, "edges": edges}
