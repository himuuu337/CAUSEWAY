/**
 * Pure state logic for one investigation.
 *
 * Split out from useInvestigation.ts so it can be unit tested without a DOM,
 * an EventSource, or React. Nothing in this file talks to the network. The
 * reducer sorts events into the shape the page wants; it never decides
 * anything. There is no verdict arithmetic here, no ratio, no threshold - a
 * verdict appears on screen only because the backend emitted one, and the
 * import-graph test in the backend suite is the other half of that promise.
 */
import type {
  Assessment, Candidate, CausewayEvent, Exclusion, Plan,
  Provenance, RunState, Validation, Verdict,
} from './types'
import { plannerDetail } from './provenance'

export type Connection = 'closed' | 'connecting' | 'open' | 'reconnecting'
export type StageStatus = 'pending' | 'active' | 'done'

export interface PhaseRow {
  phase: string
  role: 'control' | 'evidence'
  p95_ms?: number
  p50_ms?: number
  reps?: number
  /** Present only once the control on the far side has also been measured. */
  state?: string
  ratio?: number | null
  localControlMs?: number
  drift?: number
  running: boolean
}

export interface HypothesisView {
  id: string
  phases: PhaseRow[]
  started: boolean
  plan?: Plan
  provenance?: Provenance
  validation?: Validation
  intervention?: { flag: string; value: boolean }
  holdingFixed?: string[]
  verdict?: Verdict
  reason?: string
}

export interface PipelineStage {
  key: string
  label: string
  detail: string
  /** Which side of the boundary this stage sits on. */
  kind: 'code' | 'ai'
  status: StageStatus
}

export interface InvestigationState {
  runId: string | null
  runState: RunState | 'idle'
  connection: Connection
  error: string | null
  errorKind: 'backend-unreachable' | 'run-failed' | 'validation-rejected' | 'generic' | null
  stages: Record<string, 'running' | 'done'>
  incident?: Extract<CausewayEvent, { type: 'incident' }>
  candidates: Candidate[]
  excluded: Exclusion[]
  deploysConsidered: number
  assessments: Assessment[]
  topSuspect: string | null
  hypotheses: Record<string, HypothesisView>
  order: string[]
  activeHypothesis: string | null
  conclusion?: Extract<CausewayEvent, { type: 'conclusion' }>
  events: CausewayEvent[]
}

export const EMPTY: InvestigationState = {
  runId: null,
  runState: 'idle',
  connection: 'closed',
  error: null,
  errorKind: null,
  stages: {},
  candidates: [],
  excluded: [],
  deploysConsidered: 0,
  assessments: [],
  topSuspect: null,
  hypotheses: {},
  order: [],
  activeHypothesis: null,
  events: [],
}

function ensure(state: InvestigationState, id: string): HypothesisView {
  return state.hypotheses[id] ?? { id, phases: [], started: false }
}

function withPhases(view: HypothesisView, phase: string,
                    change: (row: PhaseRow) => PhaseRow): HypothesisView {
  return { ...view, phases: view.phases.map((row) => row.phase === phase ? change(row) : row) }
}

/** Fold one event into the view. No event is interpreted beyond being filed. */
export function reduce(state: InvestigationState, event: CausewayEvent): InvestigationState {
  const next: InvestigationState = { ...state, events: [...state.events, event] }

  switch (event.type) {
    case 'stage':
      next.stages = { ...state.stages, [event.stage]: event.status }
      return next

    case 'incident':
      next.incident = event
      return next

    case 'candidates':
      next.candidates = event.candidates
      next.excluded = event.excluded
      next.deploysConsidered = event.deploys_considered
      return next

    case 'observational':
      next.assessments = event.assessments
      next.topSuspect = event.top_suspect
      return next

    case 'plan': {
      const view: HypothesisView = {
        ...ensure(state, event.hypothesis),
        plan: event.plan,
        provenance: event.provenance,
      }
      next.hypotheses = { ...state.hypotheses, [event.hypothesis]: view }
      next.order = state.order.includes(event.hypothesis)
        ? state.order
        : [...state.order, event.hypothesis]
      return next
    }

    case 'validation': {
      const { checks, passed, total, accepted, reasoning_flagged } = event
      next.hypotheses = {
        ...state.hypotheses,
        [event.hypothesis]: {
          ...ensure(state, event.hypothesis),
          validation: { checks, passed, total, accepted, reasoning_flagged },
        },
      }
      if (!accepted) {
        next.errorKind = next.errorKind ?? 'validation-rejected'
      }
      return next
    }

    case 'experiment_start': {
      const view: HypothesisView = {
        ...ensure(state, event.hypothesis),
        started: true,
        intervention: event.intervention,
        holdingFixed: event.holding_fixed,
        phases: event.phases.map((phase) => ({
          phase,
          role: phase.indexOf('control') === 0 ? ('control' as const) : ('evidence' as const),
          running: false,
        })),
      }
      next.hypotheses = { ...state.hypotheses, [event.hypothesis]: view }
      next.activeHypothesis = event.hypothesis
      return next
    }

    case 'phase_start':
      next.hypotheses = {
        ...state.hypotheses,
        [event.hypothesis]: withPhases(ensure(state, event.hypothesis), event.phase,
          (row) => ({ ...row, running: true })),
      }
      next.activeHypothesis = event.hypothesis
      return next

    case 'phase_result':
      next.hypotheses = {
        ...state.hypotheses,
        [event.hypothesis]: withPhases(ensure(state, event.hypothesis), event.phase,
          (row) => ({
            ...row, running: false, role: event.role,
            p95_ms: event.p95_ms, p50_ms: event.p50_ms, reps: event.reps,
          })),
      }
      return next

    case 'phase_judged':
      next.hypotheses = {
        ...state.hypotheses,
        [event.hypothesis]: withPhases(ensure(state, event.hypothesis), event.phase,
          (row) => ({
            ...row, state: event.state, ratio: event.ratio,
            localControlMs: event.local_control_ms, drift: event.drift,
          })),
      }
      return next

    case 'verdict':
      next.hypotheses = {
        ...state.hypotheses,
        [event.hypothesis]: {
          ...ensure(state, event.hypothesis),
          verdict: event.verdict,
          reason: event.reason,
        },
      }
      return next

    case 'conclusion':
      next.conclusion = event
      next.activeHypothesis = null
      return next

    case 'error':
      next.error = event.message
      next.errorKind = next.errorKind ?? 'generic'
      return next

    case 'end':
      next.runState = event.state
      if (event.error) {
        next.error = event.error
        next.errorKind = event.state === 'failed' ? 'run-failed' : (next.errorKind ?? 'generic')
      }
      return next

    default:
      return next
  }
}

/**
 * The trust pipeline, derived only from events that have actually arrived.
 *
 * The planner's label is whatever the backend said its provenance was. A
 * deterministic run is never called a fallback, and nothing is ever called
 * Gemini unless the backend reported `kind: "gemini"`. See provenance.ts for
 * the single shared rule this and PlanPanel both use.
 */
export function pipelineOf(state: InvestigationState): PipelineStage[] {
  const stage = (name: string): StageStatus =>
    state.stages[name] === 'done' ? 'done'
      : state.stages[name] === 'running' ? 'active' : 'pending'

  const views = state.order.map((id) => state.hypotheses[id]).filter(Boolean)
  const measured = views.some((view) => view.phases.some((row) => row.p95_ms !== undefined))
  const verdicts = views.filter((view) => view.verdict !== undefined).length
  const expected = state.candidates.length

  const provenance = views.find((view) => view.provenance)?.provenance
  let plannerText = 'Awaiting plan'
  let plannerKind: 'code' | 'ai' = 'ai'
  if (provenance) {
    const detail = plannerDetail(provenance)
    plannerText = detail.label
    plannerKind = detail.isAi ? 'ai' : 'code'
  }

  return [
    { key: 'localizer', label: 'LOCALIZER', detail: 'Deterministic', kind: 'code',
      status: stage('localization') },
    { key: 'planner', label: 'PLANNER', detail: plannerText, kind: plannerKind,
      status: stage('planning') },
    { key: 'validator', label: 'VALIDATOR', detail: 'Deterministic', kind: 'code',
      status: stage('validation') },
    { key: 'sandbox', label: 'SANDBOX', detail: 'Controlled execution', kind: 'code',
      status: stage('experiment') },
    { key: 'measurements', label: 'MEASUREMENTS', detail: 'Deterministic', kind: 'code',
      status: stage('experiment') === 'done' ? 'done' : measured ? 'active' : 'pending' },
    { key: 'verdict', label: 'VERDICT', detail: 'Deterministic', kind: 'code',
      status: expected > 0 && verdicts >= expected ? 'done' : verdicts > 0 ? 'active' : 'pending' },
  ]
}
