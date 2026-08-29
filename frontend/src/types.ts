/**
 * The event contract, as the backend emits it.
 *
 * Every field here is produced by causeway/orchestrator.py. Nothing in the
 * frontend derives a verdict, a ratio or a phase state - those arrive already
 * decided by the engine, and this file exists to describe what arrives rather
 * than to reproduce any of the reasoning behind it.
 */

export type Verdict = 'PROVEN' | 'REFUTED' | 'SUPPORTED' | 'UNRESOLVED'
export type PhaseState = 'broken' | 'healthy' | 'inconclusive' | 'unstable'

export interface Candidate {
  change_id: string
  sha: string
  branch: string
  service: string
  summary: string
  deployed_at: string
  seconds_before_detection: number
  files_changed: number
  lines_changed: number
  changed_files: string[]
}

export interface Exclusion {
  change_id: string
  branch: string
  reason: string
}

export interface Assessment {
  change_id: string
  branch: string
  score: number
  components: Record<string, number>
  reason: string
}

/** Where an accepted plan actually came from. Rendered verbatim. */
export interface Provenance {
  source: string
  kind: 'gemini' | 'deterministic' | string
  proposed_by: string
  used_fallback: boolean
  fallback_reason: string
}

export interface Plan {
  hypothesis_id: string
  intervention: { flag: string; value: boolean }
  fixture_id: string
  expected_signature: {
    metric: string
    op: string
    relative_to: string
    factor: number
  }
  discriminates_between: string[]
  reasoning_summary: string
}

export interface Check {
  name: string
  passed: boolean
  detail: string
}

export interface Validation {
  checks: Check[]
  passed: number
  total: number
  accepted: boolean
  reasoning_flagged: boolean
}

export type CausewayEvent =
  | { type: 'stage'; stage: string; status: 'running' | 'done'; t: number }
  | {
      type: 'incident'
      incident: Record<string, unknown> & { id: string; service: string; title: string; symptom: string; detected_at: string }
      calibration: { healthy_p95_ms: number; incident_p95_ms: number; ratio: number; audit_rows: number }
      fixture: { id: string; requests: number; concurrency: number; recorded_from: string }
      repetitions: number
    }
  | { type: 'candidates'; candidates: Candidate[]; excluded: Exclusion[]; deploys_considered: number }
  | { type: 'observational'; assessments: Assessment[]; top_suspect: string; weights: Record<string, number>; margin: number }
  | ({ type: 'plan'; hypothesis: string; plan: Plan; validation: Validation; provenance: Provenance })
  | ({ type: 'validation'; hypothesis: string } & Validation)
  | { type: 'experiment_start'; hypothesis: string; phases: string[]; intervention: { flag: string; value: boolean }; holding_fixed: string[] }
  | { type: 'phase_start'; hypothesis: string; phase: string; flags: Record<string, boolean> }
  | { type: 'phase_result'; hypothesis: string; phase: string; role: 'control' | 'evidence'; p95_ms: number; p50_ms: number; reps: number; error_rate: number }
  | {
      type: 'phase_judged'
      hypothesis: string
      phase: string
      state: PhaseState
      p95_ms: number
      local_control_ms: number
      ratio: number | null
      controls_agree: boolean
      drift: number
    }
  | { type: 'verdict'; hypothesis: string; verdict: Verdict; reason: string; detail: Record<string, unknown>; phases: unknown[] }
  | {
      type: 'conclusion'
      observational_top_suspect: string
      verdicts: Record<string, Verdict>
      proven: string[]
      refuted: string[]
      correlation_selected_decoy: boolean
      elapsed_s: number
    }
  | { type: 'done'; elapsed_s: number }
  | { type: 'error'; message: string }
  | { type: 'end'; run_id: string; state: RunState; error: string; event_count: number; elapsed_s: number }

export type RunState = 'running' | 'completed' | 'failed'

export interface RunSummary {
  run_id: string | null
  state: RunState | 'idle'
  error?: string
  event_count: number
  elapsed_s?: number
}

export interface Health {
  status: string
  seeded: boolean
  hint: string | null
  incident: { id: string; service: string; title: string }
  engine: {
    phases: string[]
    verdicts: Verdict[]
    failure_factor: number
    recovery_factor: number
  }
  frontend_built: boolean
}
