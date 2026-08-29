import { Fragment } from 'react'
import { modelOf } from '../useInvestigation'
import type { FixPhaseRow, FixView } from '../useInvestigation'
import type { Candidate } from '../types'
import { ms, share, times } from '../format'

interface Props {
  view: FixView
  candidate?: Candidate
}

/** The two phases that carry evidence, in the order they are measured. */
const EVIDENCE = ['fix-before', 'fix-after'] as const

const STAGE_WORD: Record<string, string> = {
  'fix-before': 'BEFORE FIX', 'fix-after': 'AFTER FIX',
}

const STATE_WORD: Record<string, string> = {
  broken: 'BROKEN', healthy: 'HEALTHY',
  inconclusive: 'INCONCLUSIVE', unstable: 'UNSTABLE',
}

const FIX_VERDICT_WORD: Record<string, string> = {
  VERIFIED: 'FIX VERIFIED', FAILED: 'FIX FAILED', UNRESOLVED: 'UNRESOLVED',
}

function rowOf(view: FixView, phase: string): FixPhaseRow | undefined {
  return view.phases.find((row) => row.phase === phase)
}

function plannerLabel(view: FixView): string {
  const p = view.provenance
  if (!p) return 'AWAITING PLAN'
  if (p.used_fallback) return 'DETERMINISTIC FALLBACK'
  if (p.kind === 'gemini') return `GEMINI · ${modelOf(p.source)}`
  return 'DETERMINISTIC PLANNER'
}

function plannerClass(view: FixView): string {
  const p = view.provenance
  if (!p) return 'planner-tag'
  if (p.used_fallback) return 'planner-tag fallback'
  if (p.kind === 'gemini') return 'planner-tag ai'
  return 'planner-tag'
}

/**
 * The verified fix loop, one card per PROVEN hypothesis. Compact by design -
 * this section exists to show the loop closed, not to repeat the causal
 * experiment above it. Every number, state word and verdict here arrived on
 * an event the backend emitted; bar width is the only thing derived, and
 * only as a proportion of the larger of the two measurements shown.
 */
export default function FixPanel({ view, candidate }: Props) {
  const title = view.label ?? view.hypothesis
  const rows = EVIDENCE.map((phase) => rowOf(view, phase))
  const values = rows
    .map((row) => row?.p95_ms)
    .filter((value): value is number => value !== undefined)
  const max = values.length > 0 ? Math.max(...values) : 0

  return (
    <section className="card fix-card">
      <div className="card-head">
        <h2 className="card-title">Verified fix — <span className="mono">{title}</span></h2>
        <span className="card-note">
          root cause proven → a fix is proposed → code validates → sandbox tests it
        </span>
        <div className="spacer" />
        {view.blocked
          ? <span className="verdict-pill waiting">NO FIX PROPOSED</span>
          : view.verdict
            ? <span className={`verdict-pill fix-${view.verdict}`}>{FIX_VERDICT_WORD[view.verdict]}</span>
            : <span className="verdict-pill waiting">
                {view.started ? 'MEASURING…' : 'PLANNING…'}
              </span>}
      </div>

      {view.blocked && (
        <div className="notice repo-rejected">
          <div className="repo-rejected-title">
            {view.blocked.scope === 'intent'
              ? 'BLOCKED BY YOUR INSTRUCTION'
              : 'BLOCKED BY THE REPOSITORY&apos;S OWN MANIFEST'}
          </div>
          <div>{view.blocked.reason}</div>
          <div className="small faint" style={{ marginTop: 6 }}>
            The cause above is still proven — it was established by measurement.
            What was refused is changing a file this run is not permitted to change.
          </div>
        </div>
      )}

      <div className="fix-chain">
        <div className="fix-chain-step">
          <div className="k">VERIFIED ROOT CAUSE</div>
          <div className="v mono">
            {title}{candidate?.branch ? ` — ${candidate.branch}` : ''}
          </div>
        </div>
        <div className="fix-chain-link" aria-hidden="true">&darr;</div>
        <div className="fix-chain-step">
          <div className="k">PROPOSED FIX</div>
          <div className="v">{view.fix?.summary ?? '—'}</div>
          {view.fix && (
            <span className={plannerClass(view)}>{plannerLabel(view)}</span>
          )}
        </div>
      </div>

      {view.fix?.operation && (
        <div className="patch-review">
          <div className="patch-label">
            change reviewed by the deterministic fix validator — {view.fix.operation.target}
          </div>
          <div className="patch-lines">
            <div className="patch-line before">- {view.fix.operation.before}</div>
            <div className="patch-line after">+ {view.fix.operation.after}</div>
          </div>
        </div>
      )}

      {view.diff && (
        <details className="patch-diff" open>
          <summary>
            The patch, as it was applied to a disposable copy
            {view.file ? ` of ${view.file}` : ''}
          </summary>
          <pre className="diff">
            {view.diff.split('\n').map((line, index) => (
              <div
                key={index}
                className={
                  line.startsWith('+') && !line.startsWith('+++') ? 'diff-add'
                    : line.startsWith('-') && !line.startsWith('---') ? 'diff-del'
                      : line.startsWith('@@') ? 'diff-hunk' : 'diff-ctx'
                }
              >
                {line}
              </div>
            ))}
          </pre>
        </details>
      )}

      {view.fix?.reasoning_summary && (
        <blockquote className="quote">{view.fix.reasoning_summary}</blockquote>
      )}

      {view.validation && (
        <div className="validator-line">
          <span className="card-title" style={{ margin: 0 }}>Fix validator</span>
          <span className="validator-count">
            {view.validation.passed} / {view.validation.total} PASSED
          </span>
        </div>
      )}

      {view.started && (
        <>
          <div className="stages fix-stages">
            {EVIDENCE.map((phase, index) => {
              const row = rows[index]
              const state = row?.state ?? 'pending'
              const width = share(row?.p95_ms, max)
              return (
                <Fragment key={phase}>
                  {index > 0 && <div className="stage-sep" aria-hidden="true">&rarr;</div>}
                  <div className={`stage ${state}`}>
                    <div className="stage-name">{STAGE_WORD[phase]}</div>
                    {row?.patched !== undefined && (
                      <div className="small faint">
                        {row.patched ? 'patched build' : 'unpatched build'}
                      </div>
                    )}
                    <div className={`stage-value${row?.p95_ms === undefined ? ' pending' : ''}`}>
                      {row?.p95_ms !== undefined
                        ? ms(row.p95_ms)
                        : row?.running ? 'measuring…' : '—'}
                    </div>
                    <div className="stage-track">
                      <div className={`stage-fill ${state}`} style={{ width: `${width}%` }} />
                    </div>
                    <div className={`stage-state ${state}`}>
                      {row?.state ? STATE_WORD[row.state] ?? row.state.toUpperCase() : '—'}
                    </div>
                    <div className="stage-ratio">
                      {row?.ratio != null ? `${times(row.ratio)} local control` : ''}
                    </div>
                  </div>
                </Fragment>
              )
            })}
          </div>

          {view.reason && <div className="verdict-reason">{view.reason}</div>}
        </>
      )}

      {view.verdict && (
        <div className="notice sandbox-notice">
          Verified in sandbox only — human review required. Nothing has been deployed,
          and no production system has been changed.
        </div>
      )}
    </section>
  )
}
