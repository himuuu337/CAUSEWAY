/**
 * The causal graph, built entirely from state the page already holds.
 *
 * Nothing here calls the backend and nothing here decides a verdict. Every
 * node and edge is a rendering of a fact InvestigationState (or MonitorState)
 * already folded from real backend events - the same discipline
 * useInvestigation.ts and useMonitor.ts apply to their own state. A relationship
 * is only ever labelled with causal language ("verified causal relationship")
 * once causeway.verdict actually decided one; before that it is a candidate,
 * never a cause.
 */
import dagre from 'dagre'
import type { InvestigationState } from './useInvestigation'
import type { MonitorState } from './useMonitor'
import type { Verdict } from './types'

export type GraphNodeType =
  | 'incident' | 'repository' | 'candidate' | 'code_change' | 'experiment' | 'fix' | 'prediction'

/** Correlation is not causation: every edge into an incident carries one of
 * these, and only 'proven' is ever described on screen as a cause. */
export type CausalStrength = 'candidate' | 'proven' | 'refuted' | 'supported' | 'unresolved' | 'link'

export interface GraphNode {
  id: string
  type: GraphNodeType
  label: string
  description: string
  status: string
  metadata: Record<string, unknown>
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  label: string
  strength: CausalStrength
}

export interface CausalGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export const CAUSAL_LABEL: Record<CausalStrength, string> = {
  candidate: 'suspected cause',
  proven: 'verified causal relationship',
  refuted: 'refuted',
  supported: 'supported (one-sided)',
  unresolved: 'unresolved',
  link: '',
}

function strengthFor(verdict: Verdict | undefined): CausalStrength {
  switch (verdict) {
    case 'PROVEN': return 'proven'
    case 'REFUTED': return 'refuted'
    case 'SUPPORTED': return 'supported'
    case 'UNRESOLVED': return 'unresolved'
    default: return 'candidate'
  }
}

/** Build the causal graph from the same InvestigationState the rest of the
 * page renders from. Deterministic: the same state always produces the same
 * graph, node for node and edge for edge. */
export function buildCausalGraph(state: InvestigationState, monitor?: MonitorState): CausalGraph {
  const nodes: GraphNode[] = []
  const edges: GraphEdge[] = []

  const repo = state.repository
  if (repo && repo.status === 'loaded') {
    nodes.push({
      id: 'repository',
      type: 'repository',
      label: repo.name ?? repo.url,
      description: repo.owner && repo.name ? `${repo.owner}/${repo.name}` : repo.url,
      status: repo.contract === 'standard' ? 'standard repository' : 'loaded',
      metadata: {
        url: repo.url,
        commitSha: repo.commitSha,
        runtime: repo.runtime,
        primaryLanguage: repo.primaryLanguage,
        entrypoint: repo.entrypoint,
        sources: repo.sources,
      },
    })
  }

  if (state.incident) {
    const incident = state.incident.incident
    const resolved = state.conclusion !== undefined
    nodes.push({
      id: 'incident',
      type: 'incident',
      label: String(incident.title ?? incident.id),
      description: String(incident.symptom ?? ''),
      status: resolved
        ? ((state.conclusion?.proven.length ?? 0) > 0 ? 'root cause proven' : 'no cause proven')
        : 'under investigation',
      metadata: {
        id: incident.id,
        service: incident.service,
        detectedAt: incident.detected_at,
      },
    })
  }

  const hasIncident = state.incident !== undefined

  /** One suspected-cause node (a bundled-demo candidate or a repository code
   * hypothesis), wired through to its experiment and, once one exists, to
   * the incident with the verdict's own strength - never a stronger one. */
  function wireSuspect(sourceId: string, hypothesisId: string) {
    const view = state.hypotheses[hypothesisId]
    if (!hasIncident) return
    if (view && view.started) {
      edges.push({
        id: `e:${sourceId}->experiment:${hypothesisId}`,
        source: sourceId, target: `experiment:${hypothesisId}`,
        label: 'tested by', strength: 'link',
      })
      const strength = strengthFor(view.verdict)
      edges.push({
        id: `e:experiment:${hypothesisId}->incident`,
        source: `experiment:${hypothesisId}`, target: 'incident',
        label: CAUSAL_LABEL[strength], strength,
      })
    } else {
      edges.push({
        id: `e:${sourceId}->incident`,
        source: sourceId, target: 'incident',
        label: CAUSAL_LABEL.candidate, strength: 'candidate',
      })
    }
  }

  for (const candidate of state.candidates) {
    const id = `candidate:${candidate.change_id}`
    nodes.push({
      id,
      type: 'candidate',
      label: candidate.summary,
      description: `${candidate.branch} @ ${candidate.sha.slice(0, 7)}`,
      status: 'deployed',
      metadata: {
        changeId: candidate.change_id,
        sha: candidate.sha,
        branch: candidate.branch,
        service: candidate.service,
        deployedAt: candidate.deployed_at,
        secondsBeforeDetection: candidate.seconds_before_detection,
        filesChanged: candidate.files_changed,
        linesChanged: candidate.lines_changed,
        changedFiles: candidate.changed_files,
      },
    })
    wireSuspect(id, candidate.change_id)
  }

  for (const hypothesis of state.found) {
    const id = `code:${hypothesis.id}`
    const location = hypothesis.line_end && hypothesis.line_end !== hypothesis.line
      ? `${hypothesis.line}-${hypothesis.line_end}` : `${hypothesis.line}`
    nodes.push({
      id,
      type: 'code_change',
      label: hypothesis.label,
      description: `${hypothesis.file}:${location}`,
      status: hypothesis.testable ? 'testable' : 'not testable',
      metadata: {
        file: hypothesis.file,
        line: hypothesis.line,
        lineEnd: hypothesis.line_end,
        symbol: hypothesis.symbol,
        kind: hypothesis.kind,
        category: hypothesis.category,
        observed: hypothesis.observed,
        counterfactual: hypothesis.counterfactual,
        evidence: hypothesis.evidence,
        reason: hypothesis.reason,
        detector: hypothesis.detector,
        excerpt: hypothesis.excerpt,
      },
    })
    if (repo) {
      edges.push({
        id: `e:repository->${id}`, source: 'repository', target: id,
        label: 'contains', strength: 'link',
      })
    }
    wireSuspect(id, hypothesis.id)
  }

  for (const id of state.order) {
    const view = state.hypotheses[id]
    if (!view || !view.started) continue
    nodes.push({
      id: `experiment:${id}`,
      type: 'experiment',
      label: 'Controlled experiment',
      description: view.reason ?? (view.verdict ? '' : 'running'),
      status: view.verdict ?? 'running',
      metadata: {
        phases: view.phases.map((phase) => ({
          phase: phase.phase, role: phase.role, p95_ms: phase.p95_ms,
          state: phase.state, ratio: phase.ratio, drift: phase.drift,
        })),
        plan: view.plan ?? null,
        provenance: view.provenance ?? null,
        validation: view.validation ?? null,
      },
    })
  }

  for (const id of state.fixOrder) {
    const fix = state.fixes[id]
    if (!fix) continue
    const nodeId = `fix:${id}`
    nodes.push({
      id: nodeId,
      type: 'fix',
      label: fix.label ? `Fix: ${fix.label}` : 'Proposed fix',
      description: fix.fix?.summary ?? fix.blocked?.reason ?? '',
      status: fix.verdict ?? (fix.blocked ? 'blocked' : 'proposed'),
      metadata: {
        file: fix.file ?? null,
        diff: fix.diff ?? null,
        operation: fix.operation ?? null,
        reason: fix.reason ?? null,
        provenance: fix.provenance ?? null,
        blocked: fix.blocked ?? null,
      },
    })
    const experimentId = `experiment:${id}`
    if (nodes.some((node) => node.id === experimentId)) {
      edges.push({
        id: `e:${experimentId}->${nodeId}`, source: experimentId, target: nodeId,
        label: 'remediation proposed', strength: 'link',
      })
    }
  }

  // A prediction node appears only when the backend itself has already tied
  // a risk episode to this run (production.ingest's handoff, carried on the
  // Incident record as run_id). Never inferred from matching service names.
  if (monitor && state.runId) {
    const linked = monitor.incidents.find((incident) => incident.run_id === state.runId)
    if (linked && hasIncident) {
      nodes.push({
        id: 'prediction',
        type: 'prediction',
        label: linked.predicted_failure,
        description: `${linked.detector} · risk ${Math.round(linked.risk_score)}/100`,
        status: 'predicted before incident',
        metadata: {
          service: linked.service,
          detector: linked.detector,
          riskScore: linked.risk_score,
          evidence: linked.evidence,
          telemetryWindow: linked.telemetry_window,
          createdAt: linked.created_at,
        },
      })
      edges.push({
        id: 'e:prediction->incident', source: 'prediction', target: 'incident',
        label: 'predicted before incident', strength: 'link',
      })
    }
  }

  return { nodes, edges }
}

export interface PositionedNode extends GraphNode {
  x: number
  y: number
  width: number
  height: number
}

const NODE_WIDTH = 220
const NODE_HEIGHT = 76

/** A deterministic top-to-bottom layered layout. dagre is the only thing
 * doing layout math here; it never touches what a node means or how it is
 * connected. */
export function layoutGraph(graph: CausalGraph): { nodes: PositionedNode[]; edges: GraphEdge[] } {
  const layout = new dagre.graphlib.Graph()
  layout.setGraph({ rankdir: 'TB', nodesep: 48, ranksep: 72, marginx: 20, marginy: 20 })
  layout.setDefaultEdgeLabel(() => ({}))

  for (const node of graph.nodes) {
    layout.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  }
  for (const edge of graph.edges) {
    if (layout.hasNode(edge.source) && layout.hasNode(edge.target)) {
      layout.setEdge(edge.source, edge.target)
    }
  }
  dagre.layout(layout)

  const nodes: PositionedNode[] = graph.nodes.map((node) => {
    const position = layout.node(node.id)
    return {
      ...node,
      x: position ? position.x - NODE_WIDTH / 2 : 0,
      y: position ? position.y - NODE_HEIGHT / 2 : 0,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    }
  })
  return { nodes, edges: graph.edges }
}
