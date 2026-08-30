import type { CodeHypothesis } from '../types'

interface Props {
  hypotheses: CodeHypothesis[]
  detectors: string[]
  sources: string[]
}

/**
 * What Causeway found by reading the repository's own source.
 *
 * These are not candidates A and B. Each row is a real file, a real line, the
 * exact text a deterministic detector found there, and the counterfactual it
 * derived - and the panel says plainly that being on this list is not
 * evidence of anything. Static analysis can say a pattern is present; only
 * the experiment below can say what it costs.
 */
export default function HypothesisPanel({ hypotheses, detectors, sources }: Props) {
  if (hypotheses.length === 0) return null
  const testable = hypotheses.filter((h) => h.testable)

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Suspect code locations</h2>
        <span className="card-note">
          {hypotheses.length} found in {sources.join(', ')} · {testable.length} testable
        </span>
      </div>

      <p className="small faint hyp-caveat">
        Found by static analysis, not by measurement. Every location below is
        the same shape as every other one — nothing here says which is causal,
        and nothing here is a verdict. That is what the experiments decide.
      </p>

      <div className="hyp-list">
        {hypotheses.map((hypothesis) => (
          <div key={hypothesis.id} className="hyp-row">
            <div className="hyp-head">
              <span className="hyp-label mono">{hypothesis.label}</span>
              {hypothesis.line_end !== hypothesis.line && (
                <span className="small faint">(lines {hypothesis.line}–{hypothesis.line_end})</span>
              )}
              <div className="spacer" />
              {hypothesis.category !== 'UNKNOWN' && (
                <span className="hyp-category mono">{hypothesis.category}</span>
              )}
              <span className={`hyp-flag ${hypothesis.testable ? 'ok' : 'off'}`}>
                {hypothesis.testable ? 'TESTABLE' : 'NOT TESTABLE'}
              </span>
            </div>

            <div className="hyp-code">
              <span className="hyp-code-label">found</span>
              <code className="hyp-observed">{hypothesis.observed}</code>
            </div>
            {hypothesis.counterfactual && (
              <div className="hyp-code">
                <span className="hyp-code-label">would test</span>
                <code className="hyp-counterfactual">{hypothesis.counterfactual}</code>
              </div>
            )}

            <div className="small faint hyp-reason">{hypothesis.reason}</div>
            <div className="small faint hyp-detector mono">
              detector: {hypothesis.detector} · id: {hypothesis.id}
            </div>
          </div>
        ))}
      </div>

      {detectors.length > 0 && (
        <div className="small faint hyp-footer">
          Detectors run: {detectors.join(', ')}. Causeway&apos;s detector set is
          narrow by design; a repository with no pattern it recognises is told
          so rather than investigated as something else.
        </div>
      )}
    </section>
  )
}
