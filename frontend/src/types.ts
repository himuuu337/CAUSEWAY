/**
 * The event contract, as the backend emits it.
 *
 * Every field here is produced by causeway/orchestrator.py. Nothing in the
 * frontend derives a verdict, a ratio or a phase state - those arrive already
 * decided by the engine, and this file exists to describe what arrives rather
 * than to reproduce any of the reasoning behind it.
 */

export type Verdict = 'PROVEN' | 'REFUTED' | 'SUPPORTED' | 'UNRESOLVED'
export type FixVerdict = 'VERIFIED' | 'FAILED' | 'UNRESOLVED'
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

/**
 * One suspicious location a deterministic detector found in a repository's
 * own source. This is what replaces A and B on the repository path: not a
 * label on a fabricated deploy, but a file, a line, the exact text found
 * there and the counterfactual that would be written to test it.
 */
export interface CodeHypothesis {
  id: string
  label: string
  file: string
  line: number
  symbol: string
  kind: string
  observed: string
  counterfactual: string | null
  evidence: string
  reason: string
  detector: string
  testable: boolean
  context: string[]
}

/** What the user asked for, parsed into something enforceable. */
export type IntentMode =
  | 'diagnose_only' | 'diagnose_and_fix' | 'requested_change' | 'needs_clarification'

export interface Constraint {
  kind: string
  value: unknown
  source: string
  enforceable: boolean
}

export interface IntentSpec {
  raw_instruction: string
  mode: IntentMode
  goal: string
  question: string
  source: string
  allows_fix: boolean
  no_fix_reason: string
  constraints: Constraint[]
  enforced: Constraint[]
  advisory: Constraint[]
  allowed_scope: string[]
  prohibited_scope: string[]
}

/** How a phase's state was put into effect. `runtime_flags` is the bundled
 * demo; `source_variant` is a repository, where the edits below were applied
 * to a disposable copy. */
export interface Intervention {
  kind: 'runtime_flags' | 'source_variant' | string
  flags?: Record<string, boolean>
  edits?: { file: string; from: string; to: string; hypothesis: string }[]
  unmodified?: boolean
}

export interface AppliedEdit {
  file: string
  before: string
  after: string
  label: string
  line: number
}

export interface DatabaseSummary {
  engine: string
  tables: Record<string, number>
  bytes: number
}

export interface WorkloadSummary {
  id: string
  requests: number
  concurrency: number
}

/** A proposed fix, only ever requested for a hypothesis already PROVEN. */
export interface FixOperation {
  type: string
  target: string
  before: string
  after: string
}

export interface Fix {
  hypothesis_id: string
  summary: string
  operation: FixOperation
  reasoning_summary: string
}

/** A structured code patch: a small, bounded set of file+hunk edits, as
 * proposed by a requested-change planner. Replaces the single-target
 * FixOperation shape on this path - a requested change is not a repair for
 * an already-proven cause, so there is no single known-safe answer to check
 * a proposal against. */
export interface PatchHunk {
  before: string
  after: string
}

export interface PatchFile {
  path: string
  hunks: PatchHunk[]
}

export interface CodePatch {
  summary: string
  files: PatchFile[]
  reasoning_summary: string
}

/** One real HTTP request, sent against a disposable sandbox to check
 * whether a requested change actually did what was asked. */
export interface VerificationCase {
  phase: 'before' | 'after'
  probe: string
  case: string
  method: string
  path: string
  body: Record<string, unknown> | null
  status: number | null
  expected_status: number[]
  passed: boolean
  error: string | null
}

export type RequestedChangeVerdict =
  | 'VERIFIED' | 'FAILED' | 'UNRESOLVED'
  // The standard (manifest-less) path: a patch was applied to a disposable
  // copy and passed whatever cheap, safe check was available (syntax), but
  // there was no reliable way to run the repository or its tests, so
  // runtime behaviour was never actually verified. Never shown as VERIFIED.
  | 'IMPLEMENTED_VERIFICATION_INCOMPLETE'

export type CausewayEvent =
  | { type: 'stage'; stage: string; status: 'running' | 'done'; t: number }
  | {
      type: 'incident'
      incident: Record<string, unknown> & { id: string; service: string; title: string; symptom: string; detected_at: string }
      // the bundled demo carries a calibration and a recorded fixture;
      // a repository carries its own workload and its own database
      calibration?: { healthy_p95_ms: number; incident_p95_ms: number; ratio: number; audit_rows: number }
      fixture?: { id: string; requests: number; concurrency: number; recorded_from: string }
      workload?: WorkloadSummary
      database?: DatabaseSummary
      verification?: string
      repetitions: number
    }
  | { type: 'intent' } & IntentSpec
  | { type: 'needs_clarification'; question: string; raw_instruction: string; modes: string[] }
  | {
      type: 'hypotheses'
      hypotheses: CodeHypothesis[]
      testable: string[]
      sources: string[]
      detectors: string[]
    }
  | { type: 'candidates'; candidates: Candidate[]; excluded: Exclusion[]; deploys_considered: number }
  | { type: 'observational'; assessments: Assessment[]; top_suspect: string; weights: Record<string, number>; margin: number }
  | ({ type: 'plan'; hypothesis: string; plan: Plan; validation: Validation; provenance: Provenance })
  | ({ type: 'validation'; hypothesis: string } & Validation)
  | {
      type: 'experiment_start'
      hypothesis: string
      phases: string[]
      holding_fixed: string[]
      // bundled demo
      intervention?: { flag: string; value: boolean }
      // repository: the location under test
      label?: string
      file?: string
      line?: number
      symbol?: string
      observed?: string
      counterfactual?: string | null
    }
  | { type: 'phase_start'; hypothesis: string; phase: string; flags?: Record<string, boolean>; intervention?: Intervention }
  | { type: 'phase_result'; hypothesis: string; phase: string; role: 'control' | 'evidence'; p95_ms: number; p50_ms: number; reps: number; error_rate: number; applied?: AppliedEdit[] }
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
      verdicts: Record<string, Verdict>
      proven: string[]
      refuted: string[]
      elapsed_s: number
      // bundled demo only: there is no correlation baseline on the
      // repository path, because there is no deploy history to correlate
      observational_top_suspect?: string
      correlation_selected_decoy?: boolean
      // repository only
      proven_labels?: string[]
    }
  | { type: 'root_cause_proven'; hypothesis: string; verdict: Verdict; label?: string }
  | { type: 'fix_skipped'; reason: string; mode: IntentMode }
  | { type: 'fix_blocked'; hypothesis: string; file: string; scope: 'intent' | 'repository'; reason: string }
  | ({ type: 'fix_plan'; hypothesis: string; fix: Fix; validation: Validation; provenance: Provenance })
  | ({ type: 'fix_validation'; hypothesis: string } & Validation)
  | { type: 'fix_apply'; hypothesis: string; summary: string; operation: FixOperation; file?: string; label?: string; diff?: string; applied_to?: string }
  | { type: 'fix_experiment_start'; hypothesis: string; phases: string[]; operation?: FixOperation }
  | { type: 'fix_phase_start'; hypothesis: string; phase: string; flags?: Record<string, boolean>; patched?: boolean; intervention?: Intervention }
  | { type: 'fix_phase_result'; hypothesis: string; phase: string; role: 'control' | 'evidence'; p95_ms: number; p50_ms: number; reps: number; error_rate: number; patched?: boolean; applied?: AppliedEdit[] }
  | {
      type: 'fix_phase_judged'
      hypothesis: string
      phase: string
      state: PhaseState
      p95_ms: number
      local_control_ms: number
      ratio: number | null
      controls_agree: boolean
      drift: number
    }
  | { type: 'fix_verdict'; hypothesis: string; verdict: FixVerdict; reason: string; phases: unknown[] }
  | { type: 'repository_validating'; url: string }
  | { type: 'repository_cloning'; owner: string; name: string; url: string }
  | {
      type: 'repository_loaded'
      owner: string
      name: string
      url: string
      commit_sha: string
      service: string
      runtime: string
      verification: string
      entrypoint: string
      sources: string[]
      patchable: string[]
      // absent (null) on the standard path: no causeway.json means no
      // database built and no workload declared - never fabricated.
      database: DatabaseSummary | null
      workload: WorkloadSummary | null
      /** Which of the two repository paths this run is on. Absent on older
       * buffered events read as `undefined`, treated the same as 'causeway'. */
      contract?: 'causeway' | 'standard'
      primary_language?: string
      detected_languages?: string[]
      language_counts?: Record<string, number>
      tests_detected?: boolean
      tests_note?: string
      all_source_files?: number
    }
  | { type: 'repository_rejected'; stage: string; reason: string }
  | {
      type: 'language_detected'
      primary: string
      detected: string[]
      counts: Record<string, number>
    }
  | {
      type: 'source_selection'
      files: string[]
      all_source_files: number
      entrypoint: string | null
      tests_detected: boolean
      tests_note: string
    }
  | { type: 'verification_check'; language: string; tool: string; file: string; passed: boolean; detail: string }
  | { type: 'requested_change_start'; instruction: string; goal: string; files_considered: string[] }
  | ({ type: 'patch_plan'; patch: CodePatch; provenance: Provenance })
  | ({ type: 'patch_validation' } & Validation)
  | { type: 'patch_rejected'; reason: string }
  | { type: 'patch_apply'; summary: string; files: string[]; diff: string; reasoning_summary: string; applied_to: string }
  | { type: 'verification_start'; cases: { probe: string; method: string; path: string; cases: string[] }[] }
  | ({ type: 'verification_case' } & VerificationCase)
  | { type: 'requested_change_verdict'; verdict: RequestedChangeVerdict; reason: string; before: VerificationCase[]; after: VerificationCase[] }
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
