/**
 * One investigation, followed over Server-Sent Events.
 *
 * Everything rendered comes out of the event buffer below. The reducer sorts
 * events into the shape the page wants; it never decides anything. There is no
 * verdict arithmetic here, no ratio, no threshold - a verdict appears on screen
 * only because the backend emitted one, and the import-graph test in the
 * backend suite is the other half of that promise.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  Assessment, Candidate, CausewayEvent, Exclusion, Health, Plan,
  Provenance, RunState, Validation, Verdict,
} from './types'

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

const EMPTY: InvestigationState = {
  runId: null,
  runState: 'idle',
  connection: 'closed',
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
function reduce(state: InvestigationState, event: CausewayEvent): InvestigationState {
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
      return next

    case 'end':
      next.runState = event.state
      if (event.error) next.error = event.error
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
 * Gemini unless the backend reported `kind: "gemini"`.
 */
/** `gemini:gemini-3.6-flash` reads better as just the model. */
export function modelOf(source: string): string {
  return source.startsWith('gemini:') ? source.slice('gemini:'.length) : source
}

function pipelineOf(state: InvestigationState): PipelineStage[] {
  const stage = (name: string): StageStatus =>
    state.stages[name] === 'done' ? 'done'
      : state.stages[name] === 'running' ? 'active' : 'pending'

  const views = state.order.map((id) => state.hypotheses[id]).filter(Boolean)
  const measured = views.some((view) => view.phases.some((row) => row.p95_ms !== undefined))
  const verdicts = views.filter((view) => view.verdict !== undefined).length
  const expected = state.candidates.length

  const provenance = views.find((view) => view.provenance)?.provenance
  let plannerDetail = 'Awaiting plan'
  let plannerKind: 'code' | 'ai' = 'ai'
  if (provenance) {
    // Three states, three labels. A run that never had a key is a deterministic
    // RUN, not a fallback; and nothing is called Gemini unless the backend
    // reported that a Gemini plan was the one accepted.
    if (provenance.used_fallback) {
      plannerDetail = 'Deterministic Fallback'
    } else if (provenance.kind === 'gemini') {
      plannerDetail = `Gemini · ${modelOf(provenance.source)}`
    } else {
      plannerDetail = 'Deterministic Planner'
    }
    plannerKind = provenance.kind === 'gemini' && !provenance.used_fallback ? 'ai' : 'code'
  }

  return [
    { key: 'localizer', label: 'LOCALIZER', detail: 'Deterministic', kind: 'code',
      status: stage('localization') },
    { key: 'planner', label: 'PLANNER', detail: plannerDetail, kind: plannerKind,
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

export function useInvestigation() {
  const [state, setState] = useState<InvestigationState>(EMPTY)
  const [health, setHealth] = useState<Health | null>(null)
  const [starting, setStarting] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)

  const closeStream = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
  }, [])

  const attach = useCallback((runId: string) => {
    closeStream()
    setState({ ...EMPTY, runId, runState: 'running', connection: 'connecting' })

    const source = new EventSource(
      `/api/investigation/stream?run_id=${encodeURIComponent(runId)}`)
    sourceRef.current = source

    source.onopen = () => setState((previous) => ({ ...previous, connection: 'open' }))

    source.onmessage = (message) => {
      let event: CausewayEvent
      try {
        event = JSON.parse(message.data) as CausewayEvent
      } catch {
        return
      }
      setState((previous) => reduce(previous, event))
      if (event.type === 'end') {
        // The run is over. EventSource reconnects on any close, including a
        // clean one, so the client has to be the thing that stops.
        closeStream()
        setState((previous) => ({ ...previous, connection: 'closed' }))
      }
    }

    source.onerror = () => {
      // EventSource retries on its own and resends Last-Event-ID, so the
      // backend hands back exactly the events this client missed.
      setState((previous) =>
        previous.runState === 'running'
          ? {
              ...previous,
              connection: source.readyState === EventSource.CLOSED ? 'closed' : 'reconnecting',
            }
          : previous)
    }
  }, [closeStream])

  const start = useCallback(async () => {
    setStarting(true)
    try {
      const response = await fetch('/api/investigation', { method: 'POST' })
      const body = await response.json().catch(() => ({}))

      // 409 means somebody (or another tab) already started one - follow it
      // rather than refusing.
      if (response.status === 202 || response.status === 409) {
        attach(body.run_id)
        return
      }
      const detail = body?.detail ?? body
      setState((previous) => ({
        ...previous,
        error: detail?.message
          ? `${detail.message}${detail.hint ? ` — ${detail.hint}` : ''}`
          : `the backend refused to start an investigation (HTTP ${response.status})`,
      }))
    } catch (problem) {
      setState((previous) => ({
        ...previous, error: `cannot reach the backend: ${String(problem)}`,
      }))
    } finally {
      setStarting(false)
    }
  }, [attach])

  // On load: is the backend up, and is an investigation already in flight?
  useEffect(() => {
    let cancelled = false
    fetch('/api/health')
      .then((response) => response.json())
      .then((body: Health) => { if (!cancelled) setHealth(body) })
      .catch(() => {
        if (!cancelled) {
          setState((previous) => ({
            ...previous, error: 'cannot reach the backend on /api/health',
          }))
        }
      })
    fetch('/api/status')
      .then((response) => response.json())
      .then((body) => {
        if (!cancelled && body?.state === 'running' && body.run_id) attach(body.run_id)
      })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [attach])

  useEffect(() => closeStream, [closeStream])

  const pipeline = useMemo(() => pipelineOf(state), [state])
  const busy = starting || state.runState === 'running'

  return { state, health, busy, starting, start, pipeline }
}
