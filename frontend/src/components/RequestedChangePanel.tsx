import { modelOf } from '../useInvestigation'
import type { RequestedChangeView } from '../useInvestigation'
import type { VerificationCase } from '../types'

interface Props {
  view: RequestedChangeView
}

const VERDICT_WORD: Record<string, string> = {
  VERIFIED: 'CHANGE VERIFIED', FAILED: 'CHANGE FAILED', UNRESOLVED: 'UNRESOLVED',
}

function plannerLabel(view: RequestedChangeView): string {
  const p = view.provenance
  if (!p) return 'AWAITING PATCH'
  if (p.used_fallback) return 'DETERMINISTIC FALLBACK'
  if (p.kind === 'gemini') return `GEMINI · ${modelOf(p.source)}`
  return 'DETERMINISTIC PLANNER'
}

function plannerClass(view: RequestedChangeView): string {
  const p = view.provenance
  if (!p) return 'planner-tag'
  if (p.used_fallback) return 'planner-tag fallback'
  if (p.kind === 'gemini') return 'planner-tag ai'
  return 'planner-tag'
}

function CaseRow({ item }: { item: VerificationCase }) {
  return (
    <div className={`probe-case ${item.passed ? 'pass' : 'fail'}`}>
      <span className={`probe-dot ${item.passed ? 'pass' : 'fail'}`} aria-hidden="true" />
      <span className="mono probe-request">{item.method} {item.path}</span>
      <span className="small faint">{item.case}</span>
      <span className="spacer" />
      <span className="mono probe-status">
        {item.error ? `ERROR: ${item.error}`
          : `${item.status ?? '—'} (expected ${item.expected_status.join('/')})`}
      </span>
    </div>
  )
}

/**
 * "Make requested change" mode's own loop: an instruction goes in, a
 * Gemini-authored (or deterministic fallback) CodePatch comes out, code
 * validates it, and real HTTP requests against a disposable, patched copy of
 * the service - not Gemini's own say-so - decide whether it actually did
 * what was asked. Everything on this panel arrived on an event the backend
 * emitted.
 */
export default function RequestedChangePanel({ view }: Props) {
  return (
    <section className="card fix-card">
      <div className="card-head">
        <h2 className="card-title">Requested change</h2>
        <span className="card-note">
          instruction → patch proposed → code validates → sandbox verifies with real requests
        </span>
        <div className="spacer" />
        {view.rejected
          ? <span className="verdict-pill waiting">NO PATCH APPLIED</span>
          : view.verdict
            ? <span className={`verdict-pill fix-${view.verdict}`}>{VERDICT_WORD[view.verdict]}</span>
            : <span className="verdict-pill waiting">
                {view.applied ? 'VERIFYING…' : 'PLANNING…'}
              </span>}
      </div>

      <blockquote className="intent-quote">{view.instruction || view.goal}</blockquote>

      {view.rejected && (
        <div className="notice repo-rejected">
          <div className="repo-rejected-title">NO SAFE PATCH COULD BE APPLIED</div>
          <div>{view.rejected}</div>
          <div className="small faint" style={{ marginTop: 6 }}>
            Nothing was applied to any copy of the repository, and nothing was verified.
          </div>
        </div>
      )}

      {view.patch && (
        <>
          <div className="fix-chain">
            <div className="fix-chain-step">
              <div className="k">PROPOSED PATCH</div>
              <div className="v">{view.patch.summary}</div>
              <span className={plannerClass(view)}>{plannerLabel(view)}</span>
            </div>
          </div>

          {view.applied?.diff && (
            <details className="patch-diff" open>
              <summary>
                The patch, as it was applied to a disposable copy
                {view.applied.files.length ? ` of ${view.applied.files.join(', ')}` : ''}
              </summary>
              <pre className="diff">
                {view.applied.diff.split('\n').map((line, index) => (
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

          {view.patch.reasoning_summary && (
            <blockquote className="quote">{view.patch.reasoning_summary}</blockquote>
          )}
        </>
      )}

      {view.validation && (
        <div className="validator-line">
          <span className="card-title" style={{ margin: 0 }}>Patch validator</span>
          <span className="validator-count">
            {view.validation.passed} / {view.validation.total} PASSED
          </span>
        </div>
      )}

      {view.applied && (view.before.length > 0 || view.after.length > 0) && (
        <div className="probe-section">
          <div className="probe-group">
            <div className="constraint-title">BEFORE — unpatched, disposable copy</div>
            {view.before.map((item, index) => <CaseRow key={`before-${index}`} item={item} />)}
          </div>
          <div className="probe-group">
            <div className="constraint-title">AFTER — patched, disposable copy</div>
            {view.after.map((item, index) => <CaseRow key={`after-${index}`} item={item} />)}
          </div>
        </div>
      )}

      {view.reason && <div className="verdict-reason">{view.reason}</div>}

      {view.verdict && (
        <div className="notice sandbox-notice">
          Verified in sandbox only — human review required. Nothing has been deployed,
          and no production system has been changed.
        </div>
      )}
    </section>
  )
}
