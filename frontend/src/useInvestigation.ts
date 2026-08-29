/**
 * One investigation, followed over Server-Sent Events.
 *
 * Everything rendered comes out of the event buffer below. The reducer sorts
 * events into the shape the page wants; it never decides anything. There is no
 * verdict arithmetic here, no ratio, no threshold - a verdict appears on screen
 * only because the backend emitted one, and the "no client-side verdict" test
 * in the backend suite is the other half of that promise.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  Assessment, Candidate, CausewayEvent, Exclusion, Health, Plan,
  Provenance, RunState, Validation, Verdict,
} from './types'

export type Connection = 'closed' | 'connecting' | 'open' | 'reconnecting'

export interface PhaseRow {
  phase: string
  role: 'control' | 'evidence'
  p95_ms?: number
  reps?: number
  /** Present only once the control on the far side has also been measured. */
  state?: string
  ratio?: number | null
  localControlMs?: number
  running: boolean
}

export interface HypothesisView {
  id: string
  phases: PhaseRow[]
  plan?: Plan
  provenance?: Provenance
  validation?: Validation
  verdict?: Verdict
  reason?: string
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
  events: [],
}

function ensure(state: InvestigationState, id: string): HypothesisView {
  return state.hypotheses[id] ?? { id, phases: [] }
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
      const view = { ...ensure(state, event.hypothesis), plan: event.plan, provenance: event.provenance }
      next.hypotheses = { ...state.hypotheses, [event.hypothesis]: view }
      next.order = state.order.includes(event.hypothesis) ? state.order : [...state.order, event.hypothesis]
      return next
    }

    case 'validation': {
      const { checks, passed, total, accepted, reasoning_flagged } = event
      const view = {
        ...ensure(state, event.hypothesis),
        validation: { checks, passed, total, accepted, reasoning_flagged },
      }
      next.hypotheses = { ...state.hypotheses, [event.hypothesis]: view }
      return next
    }

    case 'experiment_start': {
      const view = {
        ...ensure(state, event.hypothesis),
        phases: event.phases.map((phase) => ({
          phase,
          role: phase.startsWith('control') ? ('control' as const) : ('evidence' as const),
          running: false,
        })),
      }
      next.hypotheses = { ...state.hypotheses, [event.hypothesis]: view }
      return next
    }

    case 'phase_start': {
      const view = ensure(state, event.hypothesis)
      const phases = view.phases.map((row) =>
        row.phase === event.phase ? { ...row, running: true } : row,
      )
      next.hypotheses = { ...state.hypotheses, [event.hypothesis]: { ...view, phases } }
      return next
    }

    case 'phase_result': {
      const view = ensure(state, event.hypothesis)
      const phases = view.phases.map((row) =>
        row.phase === event.phase
          ? { ...row, running: false, p95_ms: event.p95_ms, reps: event.reps, role: event.role }
          : row,
      )
      next.hypotheses = { ...state.hypotheses, [event.hypothesis]: { ...view, phases } }
      return next
    }

    case 'phase_judged': {
      const view = ensure(state, event.hypothesis)
      const phases = view.phases.map((row) =>
        row.phase === event.phase
          ? { ...row, state: event.state, ratio: event.ratio, localControlMs: event.local_control_ms }
          : row,
      )
      next.hypotheses = { ...state.hypotheses, [event.hypothesis]: { ...view, phases } }
      return next
    }

    case 'verdict': {
      const view = { ...ensure(state, event.hypothesis), verdict: event.verdict, reason: event.reason }
      next.hypotheses = { ...state.hypotheses, [event.hypothesis]: view }
      return next
    }

    case 'conclusion':
      next.conclusion = event
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

export function useInvestigation() {
  const [state, setState] = useState<InvestigationState>(EMPTY)
  const [health, setHealth] = useState<Health | null>(null)
  const [starting, setStarting] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)

  const closeStream = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
  }, [])

  const attach = useCallback((runId: string, resume = false) => {
    closeStream()
    setState((previous) => ({
      ...(resume ? previous : EMPTY),
      runId,
      runState: 'running',
      connection: 'connecting',
      error: null,
    }))

    const source = new EventSource(`/api/investigation/stream?run_id=${encodeURIComponent(runId)}`)
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
      // backend can hand back exactly the events this client missed.
      setState((previous) =>
        previous.runState === 'running'
          ? { ...previous, connection: source.readyState === EventSource.CLOSED ? 'closed' : 'reconnecting' }
          : previous,
      )
    }
  }, [closeStream])

  const start = useCallback(async () => {
    setStarting(true)
    try {
      const response = await fetch('/api/investigation', { method: 'POST' })
      const body = await response.json().catch(() => ({}))

      if (response.status === 202) {
        attach(body.run_id)
        return
      }
      if (response.status === 409) {
        // Somebody (or another tab) already started one. Follow it from the
        // beginning rather than refusing.
        attach(body.run_id)
        return
      }
      const detail = body?.detail ?? body
      setState((previous) => ({
        ...previous,
        error: detail?.message
          ? `${detail.message}${detail.hint ? ` - ${detail.hint}` : ''}`
          : `the backend refused to start an investigation (HTTP ${response.status})`,
      }))
    } catch (problem) {
      setState((previous) => ({
        ...previous,
        error: `cannot reach the backend: ${String(problem)}`,
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
          setState((previous) => ({ ...previous, error: 'cannot reach the backend on /api/health' }))
        }
      })
    fetch('/api/status')
      .then((response) => response.json())
      .then((body) => {
        if (!cancelled && body?.state === 'running' && body.run_id) attach(body.run_id)
      })
      .catch(() => undefined)
    return () => { cancelled = true; }
  }, [attach])

  useEffect(() => closeStream, [closeStream])

  const busy = starting || state.runState === 'running'
  const stageList = useMemo(() => Object.entries(state.stages), [state.stages])

  return { state, health, busy, starting, start, stageList }
}
