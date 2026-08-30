/**
 * buildCausalGraph is a pure function of InvestigationState (and, for the
 * one prediction case, MonitorState) - every test here constructs that
 * state directly rather than running the app, the same way useInvestigation
 * itself is exercised nowhere but through the events it folds.
 */
import { describe, expect, it } from 'vitest'
import { buildCausalGraph, layoutGraph } from './graph'
import type { InvestigationState } from './useInvestigation'
import type { MonitorState } from './useMonitor'
import type { Candidate, CodeHypothesis, Incident } from './types'

function baseState(overrides: Partial<InvestigationState> = {}): InvestigationState {
  return {
    runId: 'run-1',
    runState: 'running',
    connection: 'open',
    error: null,
    stages: {},
    candidates: [],
    excluded: [],
    deploysConsidered: 0,
    assessments: [],
    topSuspect: null,
    hypotheses: {},
    order: [],
    activeHypothesis: null,
    fixes: {},
    fixOrder: [],
    activeFix: null,
    found: [],
    detectors: [],
    events: [],
    ...overrides,
  }
}

function incidentEvent(): InvestigationState['incident'] {
  return {
    type: 'incident',
    incident: {
      id: 'inc-1', service: 'order-service', title: 'Order latency regression',
      symptom: 'p95 latency up 15x', detected_at: '2026-08-30T00:00:00Z',
    },
    repetitions: 3,
  }
}

function candidate(id: string): Candidate {
  return {
    change_id: id, sha: `${id}sha00000000`, branch: `feature/${id}`, service: 'order-service',
    summary: `Change ${id}`, deployed_at: '2026-08-29T23:00:00Z',
    seconds_before_detection: 120, files_changed: 3, lines_changed: 40,
    changed_files: [`src/${id}.py`],
  }
}

function codeHypothesis(id: string): CodeHypothesis {
  return {
    id, label: `Hypothesis ${id}`, file: 'order_service/db.py', line: 144, line_end: 144,
    symbol: 'get_order_audit', kind: 'predicate', category: 'DATABASE ISSUE',
    observed: 'WHERE normalize(order_id) = ?', counterfactual: 'WHERE order_id = ?',
    evidence: 'predicate wraps an indexed column', reason: 'this can defeat the index',
    detector: 'predicate-wrap', testable: true, context: [],
  }
}

describe('buildCausalGraph', () => {
  it('returns an empty graph when there is nothing to show', () => {
    const graph = buildCausalGraph(baseState())
    expect(graph.nodes).toHaveLength(0)
    expect(graph.edges).toHaveLength(0)
  })

  it('builds only an incident node when nothing else has arrived yet', () => {
    const graph = buildCausalGraph(baseState({ incident: incidentEvent() }))
    expect(graph.nodes).toHaveLength(1)
    expect(graph.nodes[0]).toMatchObject({ id: 'incident', type: 'incident' })
    expect(graph.edges).toHaveLength(0)
  })

  it('marks a not-yet-tested candidate as a suspected cause, never a proven one', () => {
    const graph = buildCausalGraph(baseState({
      incident: incidentEvent(),
      candidates: [candidate('A')],
    }))
    const edge = graph.edges.find((e) => e.source === 'candidate:A')
    expect(edge).toMatchObject({ target: 'incident', strength: 'candidate' })
    expect(edge?.label).not.toMatch(/verified/i)
  })

  it('inserts an experiment node once a hypothesis has started, and labels its outcome by verdict', () => {
    const state = baseState({
      incident: incidentEvent(),
      candidates: [candidate('B')],
      order: ['B'],
      hypotheses: {
        B: { id: 'B', started: true, verdict: 'PROVEN', reason: 'measured recovery and recurrence', phases: [] },
      },
    })
    const graph = buildCausalGraph(state)
    const experiment = graph.nodes.find((n) => n.id === 'experiment:B')
    expect(experiment).toMatchObject({ type: 'experiment', status: 'PROVEN' })

    const testedBy = graph.edges.find((e) => e.source === 'candidate:B' && e.target === 'experiment:B')
    expect(testedBy?.strength).toBe('link')

    const verdictEdge = graph.edges.find((e) => e.source === 'experiment:B' && e.target === 'incident')
    expect(verdictEdge).toMatchObject({ strength: 'proven', label: 'verified causal relationship' })
    // no direct candidate -> incident edge once an experiment exists
    expect(graph.edges.find((e) => e.source === 'candidate:B' && e.target === 'incident')).toBeUndefined()
  })

  it('labels a refuted hypothesis as refuted, not as a cause', () => {
    const state = baseState({
      incident: incidentEvent(),
      candidates: [candidate('A')],
      order: ['A'],
      hypotheses: { A: { id: 'A', started: true, verdict: 'REFUTED', phases: [] } },
    })
    const graph = buildCausalGraph(state)
    const verdictEdge = graph.edges.find((e) => e.target === 'incident')
    expect(verdictEdge).toMatchObject({ strength: 'refuted', label: 'refuted' })
  })

  it('wires a repository code hypothesis through the repository node', () => {
    const state = baseState({
      incident: incidentEvent(),
      found: [codeHypothesis('h1')],
      repository: {
        url: 'https://github.com/acme/order-service', owner: 'acme', name: 'order-service',
        sources: ['order_service/db.py'], patchable: ['order_service/db.py'], status: 'loaded',
      },
    })
    const graph = buildCausalGraph(state)
    expect(graph.nodes.find((n) => n.id === 'repository')).toBeDefined()
    expect(graph.nodes.find((n) => n.id === 'code:h1')).toMatchObject({
      type: 'code_change', description: 'order_service/db.py:144',
    })
    expect(graph.edges.find((e) => e.source === 'repository' && e.target === 'code:h1')).toMatchObject({
      strength: 'link',
    })
  })

  it('carries the finding’s engineering-insight category and line range', () => {
    const graph = buildCausalGraph(baseState({
      incident: incidentEvent(),
      found: [codeHypothesis('h1')],
    }))
    const node = graph.nodes.find((n) => n.id === 'code:h1')
    expect(node?.metadata).toMatchObject({ category: 'DATABASE ISSUE', lineEnd: 144 })
  })

  it('shows a line range in the description when the finding spans more than one line', () => {
    const graph = buildCausalGraph(baseState({
      incident: incidentEvent(),
      found: [{ ...codeHypothesis('h2'), line: 20, line_end: 23 }],
    }))
    expect(graph.nodes.find((n) => n.id === 'code:h2')).toMatchObject({
      description: 'order_service/db.py:20-23',
    })
  })

  it('attaches a fix node to its experiment when a fix was proposed', () => {
    const state = baseState({
      incident: incidentEvent(),
      candidates: [candidate('B')],
      order: ['B'],
      hypotheses: { B: { id: 'B', started: true, verdict: 'PROVEN', phases: [] } },
      fixOrder: ['B'],
      fixes: { B: { hypothesis: 'B', started: true, verdict: 'VERIFIED', phases: [], label: 'Change B' } },
    })
    const graph = buildCausalGraph(state)
    expect(graph.nodes.find((n) => n.id === 'fix:B')).toMatchObject({ type: 'fix', status: 'VERIFIED' })
    expect(graph.edges.find((e) => e.source === 'experiment:B' && e.target === 'fix:B')).toBeDefined()
  })

  it('never fabricates a fix node when nothing was proposed', () => {
    const graph = buildCausalGraph(baseState({ incident: incidentEvent() }))
    expect(graph.nodes.find((n) => n.type === 'fix')).toBeUndefined()
  })

  it('only shows a prediction node when the backend itself tied it to this run', () => {
    const linkedIncident: Incident = {
      incident_id: 'pred-1', service: 'order-service', kind: 'latency', detector: 'latency_degradation',
      predicted_failure: 'latency degradation', risk_score: 82, evidence: ['p95 up 60% over 30 min'],
      telemetry_window: { current_values: {}, trends: {}, eta_seconds: 900, sample_count: 12 },
      created_at: 0, status: 'INVESTIGATION_STARTED', run_id: 'run-1', detail: '',
    }
    const monitor: MonitorState = {
      connection: 'open', latestTelemetry: {}, risk: {}, incidents: [linkedIncident], events: [],
    }
    const graph = buildCausalGraph(baseState({ incident: incidentEvent() }), monitor)
    const node = graph.nodes.find((n) => n.type === 'prediction')
    expect(node).toBeDefined()
    expect(graph.edges.find((e) => e.source === 'prediction' && e.target === 'incident')).toBeDefined()
  })

  it('does not show a prediction node for an unrelated run', () => {
    const otherIncident: Incident = {
      incident_id: 'pred-2', service: 'order-service', kind: 'latency', detector: 'latency_degradation',
      predicted_failure: 'latency degradation', risk_score: 82, evidence: [],
      telemetry_window: { current_values: {}, trends: {}, eta_seconds: null, sample_count: 12 },
      created_at: 0, status: 'INVESTIGATION_STARTED', run_id: 'some-other-run', detail: '',
    }
    const monitor: MonitorState = {
      connection: 'open', latestTelemetry: {}, risk: {}, incidents: [otherIncident], events: [],
    }
    const graph = buildCausalGraph(baseState({ incident: incidentEvent() }), monitor)
    expect(graph.nodes.find((n) => n.type === 'prediction')).toBeUndefined()
  })
})

describe('layoutGraph', () => {
  it('assigns every node a finite position', () => {
    const state = baseState({
      incident: incidentEvent(),
      candidates: [candidate('A'), candidate('B')],
      order: ['A', 'B'],
      hypotheses: {
        A: { id: 'A', started: true, verdict: 'REFUTED', phases: [] },
        B: { id: 'B', started: true, verdict: 'PROVEN', phases: [] },
      },
    })
    const { nodes } = layoutGraph(buildCausalGraph(state))
    expect(nodes.length).toBeGreaterThan(0)
    for (const node of nodes) {
      expect(Number.isFinite(node.x)).toBe(true)
      expect(Number.isFinite(node.y)).toBe(true)
    }
  })

  it('lays out an empty graph without throwing', () => {
    const { nodes, edges } = layoutGraph(buildCausalGraph(baseState()))
    expect(nodes).toHaveLength(0)
    expect(edges).toHaveLength(0)
  })
})
