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
  AppliedEdit, Assessment, Candidate, CausewayEvent, CodeHypothesis, CodePatch,
  DatabaseSummary, Exclusion, Fix, FixOperation, FixVerdict, Health,
  IntentMode, IntentSpec, Intervention, Plan, Provenance, RequestedChangeVerdict,
  RunState, Validation, Verdict, VerificationCase, WorkloadSummary,
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
  /** How this phase's state was put into effect: flags, or source edits. */
  intervention?: Intervention
  applied?: AppliedEdit[]
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
  /** Present only on the repository path: the location under test. */
  code?: CodeHypothesis
}

/** The fix loop's own phase row - same shape as PhaseRow, kept separate so a
 * causal phase and a fix phase are never accidentally interchangeable. */
export interface FixPhaseRow {
  phase: string
  role: 'control' | 'evidence'
  p95_ms?: number
  p50_ms?: number
  reps?: number
  state?: string
  ratio?: number | null
  localControlMs?: number
  drift?: number
  running: boolean
  /** Whether this phase ran against the patched build. Backend-supplied. */
  patched?: boolean
}

export interface FixView {
  hypothesis: string
  causalVerdict?: Verdict
  fix?: Fix
  provenance?: Provenance
  validation?: Validation
  operation?: FixOperation
  applySummary?: string
  started: boolean
  phases: FixPhaseRow[]
  verdict?: FixVerdict
  reason?: string
  /** Repository path only: the patch as a human would review it. */
  diff?: string
  file?: string
  label?: string
  /** Set when a fix was never proposed, and why. */
  blocked?: { scope: 'intent' | 'repository'; reason: string }
}

/** The requested-change loop, folded from the patch_, verification_ and
 * requested_change_ events only. Present only when the backend actually
 * ran this mode - never fabricated on the frontend. */
export interface RequestedChangeView {
  instruction: string
  goal: string
  filesConsidered: string[]
  patch?: CodePatch
  provenance?: Provenance
  validation?: Validation
  rejected?: string
  applied?: { summary: string; files: string[]; diff: string; reasoningSummary: string }
  before: VerificationCase[]
  after: VerificationCase[]
  verdict?: RequestedChangeVerdict
  reason?: string
}

/** The repository lifecycle, folded from repository_* events only. `status`
 * moves forward through validating -> cloning -> loaded, or stops at
 * rejected - never both loaded and rejected at once. */
export interface RepositoryView {
  url: string
  owner?: string
  name?: string
  commitSha?: string
  service?: string
  runtime?: string
  verification?: string
  entrypoint?: string
  sources: string[]
  patchable: string[]
  database?: DatabaseSummary
  workload?: WorkloadSummary
  status: 'validating' | 'cloning' | 'loaded' | 'rejected'
  rejection?: { stage: string; reason: string }
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
  fixes: Record<string, FixView>
  fixOrder: string[]
  activeFix: string | null
  repository?: RepositoryView
  /** Repository path only. */
  intent?: IntentSpec
  clarification?: { question: string; modes: string[] }
  found: CodeHypothesis[]
  detectors: string[]
  fixSkipped?: { reason: string; mode: IntentMode }
  requestedChange?: RequestedChangeView
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
  fixes: {},
  fixOrder: [],
  activeFix: null,
  found: [],
  detectors: [],
  events: [],
}

function ensure(state: InvestigationState, id: string): HypothesisView {
  return state.hypotheses[id] ?? { id, phases: [], started: false }
}

function withPhases(view: HypothesisView, phase: string,
                    change: (row: PhaseRow) => PhaseRow): HypothesisView {
  return { ...view, phases: view.phases.map((row) => row.phase === phase ? change(row) : row) }
}

function ensureFix(state: InvestigationState, hypothesis: string): FixView {
  return state.fixes[hypothesis] ?? { hypothesis, phases: [], started: false }
}

function withFixPhases(view: FixView, phase: string,
                       change: (row: FixPhaseRow) => FixPhaseRow): FixView {
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

    case 'intent':
      // Filed exactly as parsed. The interface never re-reads the
      // instruction and never decides a mode of its own.
      next.intent = event
      return next

    case 'needs_clarification':
      next.clarification = { question: event.question, modes: event.modes }
      return next

    case 'hypotheses':
      next.found = event.hypotheses
      next.detectors = event.detectors
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
        code: state.found.find((h) => h.id === event.hypothesis),
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
          (row) => ({ ...row, running: true, intervention: event.intervention })),
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
            applied: event.applied,
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

    case 'fix_skipped':
      next.fixSkipped = { reason: event.reason, mode: event.mode }
      return next

    case 'fix_blocked':
      next.fixes = {
        ...state.fixes,
        [event.hypothesis]: {
          ...ensureFix(state, event.hypothesis),
          blocked: { scope: event.scope, reason: event.reason },
          file: event.file,
        },
      }
      return next

    case 'root_cause_proven': {
      const view: FixView = {
        ...ensureFix(state, event.hypothesis),
        causalVerdict: event.verdict,
        label: event.label,
      }
      next.fixes = { ...state.fixes, [event.hypothesis]: view }
      next.fixOrder = state.fixOrder.includes(event.hypothesis)
        ? state.fixOrder
        : [...state.fixOrder, event.hypothesis]
      return next
    }

    case 'fix_plan': {
      const view: FixView = {
        ...ensureFix(state, event.hypothesis),
        fix: event.fix,
        provenance: event.provenance,
      }
      next.fixes = { ...state.fixes, [event.hypothesis]: view }
      return next
    }

    case 'fix_validation': {
      const { checks, passed, total, accepted, reasoning_flagged } = event
      next.fixes = {
        ...state.fixes,
        [event.hypothesis]: {
          ...ensureFix(state, event.hypothesis),
          validation: { checks, passed, total, accepted, reasoning_flagged },
        },
      }
      return next
    }

    case 'fix_apply':
      next.fixes = {
        ...state.fixes,
        [event.hypothesis]: {
          ...ensureFix(state, event.hypothesis),
          operation: event.operation,
          applySummary: event.summary,
          diff: event.diff,
          file: event.file,
          label: event.label ?? state.fixes[event.hypothesis]?.label,
        },
      }
      return next

    case 'fix_experiment_start': {
      const view: FixView = {
        ...ensureFix(state, event.hypothesis),
        started: true,
        operation: event.operation,
        phases: event.phases.map((phase) => ({
          phase,
          role: phase.indexOf('control') >= 0 ? ('control' as const) : ('evidence' as const),
          running: false,
        })),
      }
      next.fixes = { ...state.fixes, [event.hypothesis]: view }
      next.activeFix = event.hypothesis
      return next
    }

    case 'fix_phase_start':
      next.fixes = {
        ...state.fixes,
        [event.hypothesis]: withFixPhases(ensureFix(state, event.hypothesis), event.phase,
          (row) => ({ ...row, running: true, patched: event.patched })),
      }
      next.activeFix = event.hypothesis
      return next

    case 'fix_phase_result':
      next.fixes = {
        ...state.fixes,
        [event.hypothesis]: withFixPhases(ensureFix(state, event.hypothesis), event.phase,
          (row) => ({
            ...row, running: false, role: event.role,
            p95_ms: event.p95_ms, p50_ms: event.p50_ms, reps: event.reps,
            patched: event.patched ?? row.patched,
          })),
      }
      return next

    case 'fix_phase_judged':
      next.fixes = {
        ...state.fixes,
        [event.hypothesis]: withFixPhases(ensureFix(state, event.hypothesis), event.phase,
          (row) => ({
            ...row, state: event.state, ratio: event.ratio,
            localControlMs: event.local_control_ms, drift: event.drift,
          })),
      }
      return next

    case 'fix_verdict':
      next.fixes = {
        ...state.fixes,
        [event.hypothesis]: {
          ...ensureFix(state, event.hypothesis),
          verdict: event.verdict,
          reason: event.reason,
        },
      }
      next.activeFix = null
      return next

    case 'repository_validating':
      next.repository = { url: event.url, sources: [], patchable: [], status: 'validating' }
      return next

    case 'repository_cloning':
      next.repository = {
        ...(state.repository ?? { url: event.url, sources: [], patchable: [] }),
        owner: event.owner, name: event.name, status: 'cloning',
      }
      return next

    case 'repository_loaded':
      next.repository = {
        ...(state.repository ?? { url: event.url, sources: [], patchable: [] }),
        owner: event.owner, name: event.name, commitSha: event.commit_sha,
        service: event.service, runtime: event.runtime,
        verification: event.verification, entrypoint: event.entrypoint,
        sources: event.sources, patchable: event.patchable,
        database: event.database, workload: event.workload,
        status: 'loaded',
      }
      return next

    case 'repository_rejected':
      // Surfaced by RepositoryPanel, not the generic error banner - it
      // already shows the stage and reason clearly, and a rejection is not
      // an engine crash.
      next.repository = {
        ...(state.repository ?? { url: '', sources: [], patchable: [] }),
        status: 'rejected', rejection: { stage: event.stage, reason: event.reason },
      }
      return next

    case 'requested_change_start':
      next.requestedChange = {
        instruction: event.instruction, goal: event.goal,
        filesConsidered: event.files_considered, before: [], after: [],
      }
      return next

    case 'patch_plan':
      next.requestedChange = {
        ...(state.requestedChange ?? { instruction: '', goal: '', filesConsidered: [], before: [], after: [] }),
        patch: event.patch, provenance: event.provenance,
      }
      return next

    case 'patch_validation': {
      const { checks, passed, total, accepted, reasoning_flagged } = event
      next.requestedChange = {
        ...(state.requestedChange ?? { instruction: '', goal: '', filesConsidered: [], before: [], after: [] }),
        validation: { checks, passed, total, accepted, reasoning_flagged },
      }
      return next
    }

    case 'patch_rejected':
      next.requestedChange = {
        ...(state.requestedChange ?? { instruction: '', goal: '', filesConsidered: [], before: [], after: [] }),
        rejected: event.reason,
      }
      return next

    case 'patch_apply':
      next.requestedChange = {
        ...(state.requestedChange ?? { instruction: '', goal: '', filesConsidered: [], before: [], after: [] }),
        applied: { summary: event.summary, files: event.files, diff: event.diff,
                  reasoningSummary: event.reasoning_summary },
      }
      return next

    case 'verification_case': {
      const base = state.requestedChange
        ?? { instruction: '', goal: '', filesConsidered: [], before: [], after: [] }
      const key = event.phase === 'before' ? 'before' : 'after'
      next.requestedChange = { ...base, [key]: [...base[key], event] }
      return next
    }

    case 'requested_change_verdict':
      next.requestedChange = {
        ...(state.requestedChange ?? { instruction: '', goal: '', filesConsidered: [], before: [], after: [] }),
        before: event.before, after: event.after,
        verdict: event.verdict, reason: event.reason,
      }
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
  // On the repository path the suspects come from the detectors; on the
  // bundled path they come from the localizer. Neither number is computed
  // here - both are counts of what the backend already emitted.
  const repositoryRun = state.repository !== undefined
  const expected = repositoryRun ? state.found.filter((h) => h.testable).length
                                 : state.candidates.length

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

  const firstStage: PipelineStage = repositoryRun
    ? { key: 'analysis', label: 'ANALYSIS',
        detail: state.detectors.length ? state.detectors.join(', ') : 'Deterministic detectors',
        kind: 'code', status: stage('analysis') }
    : { key: 'localizer', label: 'LOCALIZER', detail: 'Deterministic', kind: 'code',
        status: stage('localization') }

  return [
    firstStage,
    { key: 'planner', label: 'PLANNER', detail: plannerDetail, kind: plannerKind,
      status: stage('planning') },
    { key: 'validator', label: 'VALIDATOR', detail: 'Deterministic', kind: 'code',
      status: stage('validation') },
    { key: 'sandbox', label: 'SANDBOX',
      detail: repositoryRun ? 'Disposable source variants' : 'Controlled execution',
      kind: 'code', status: stage('experiment') },
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

  const start = useCallback(async (
    repositoryUrl?: string, instruction?: string, mode?: string,
  ) => {
    setStarting(true)
    try {
      const trimmed = repositoryUrl?.trim()
      // The instruction is sent verbatim. The frontend never parses it, never
      // rewrites it and never picks a mode on the user's behalf - it sends
      // what was typed and what was chosen, and causeway.intent reads it.
      const request: Record<string, string> = {}
      if (trimmed) request.repository_url = trimmed
      if (instruction?.trim()) request.instruction = instruction.trim()
      if (mode) request.mode = mode

      const response = await fetch('/api/investigation', {
        method: 'POST',
        ...(Object.keys(request).length
          ? {
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(request),
            }
          : {}),
      })
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
