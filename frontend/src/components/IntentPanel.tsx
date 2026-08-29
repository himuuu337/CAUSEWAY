import type { IntentSpec } from '../types'

interface Props {
  intent: IntentSpec
  clarification?: { question: string; modes: string[] }
}

const MODE_WORD: Record<string, string> = {
  diagnose_only: 'DIAGNOSE ONLY',
  diagnose_and_fix: 'DIAGNOSE AND FIX',
  requested_change: 'REQUESTED CHANGE',
  needs_clarification: 'NEEDS CLARIFICATION',
}

/**
 * What the user asked for, as the backend parsed it.
 *
 * Two things this panel is careful about. It quotes the instruction rather
 * than paraphrasing it, because the goal belongs to the user. And it shows
 * enforced and advisory constraints as two different things, because they
 * are: "only modify db.py" is checked before a patch is applied, and "keep
 * it maintainable" is recorded and nothing more. Claiming to have enforced
 * the second would be the same dishonesty as labelling a fallback Gemini.
 */
export default function IntentPanel({ intent, clarification }: Props) {
  const ambiguous = intent.mode === 'needs_clarification'

  return (
    <section className={`card intent-card${ambiguous ? ' intent-ambiguous' : ''}`}>
      <div className="card-head">
        <h2 className="card-title">What you asked for</h2>
        <div className="spacer" />
        <span className={`verdict-pill intent-${intent.mode}`}>
          {MODE_WORD[intent.mode] ?? intent.mode}
        </span>
      </div>

      {intent.raw_instruction ? (
        <blockquote className="intent-quote">{intent.raw_instruction}</blockquote>
      ) : (
        <div className="small faint">
          No instruction was given, so Causeway defaulted to diagnosing and
          changing nothing.
        </div>
      )}

      {ambiguous && clarification && (
        <div className="notice intent-question">
          <div className="repo-rejected-title">CAUSEWAY NEEDS ONE ANSWER</div>
          <div>{clarification.question}</div>
          <div className="small faint" style={{ marginTop: 6 }}>
            Nothing was cloned and nothing was measured. Guessing at a mode is
            how a run that was told to change nothing ends up changing
            something.
          </div>
        </div>
      )}

      {!ambiguous && (
        <div className="intent-grid">
          <div className="repo-meta-row">
            <span className="k">GOAL</span>
            <span className="v">{intent.goal}</span>
          </div>
          <div className="repo-meta-row">
            <span className="k">PERSISTENT FIX</span>
            <span className={`v ${intent.allows_fix ? 'good' : 'faint'}`}>
              {intent.allows_fix
                ? 'allowed — a proposed repair will be verified before it is claimed'
                : intent.no_fix_reason || 'not permitted by this instruction'}
            </span>
          </div>
          <div className="repo-meta-row">
            <span className="k">READ BY</span>
            <span className="v mono">{intent.source}</span>
          </div>
        </div>
      )}

      {intent.enforced.length > 0 && (
        <div className="constraint-block">
          <div className="constraint-title">ENFORCED — checked in code before any change</div>
          {intent.enforced.map((constraint, index) => (
            <div className="constraint-row enforced" key={`${constraint.kind}-${index}`}>
              <span className="constraint-kind mono">{constraint.kind}</span>
              <span className="constraint-value mono">
                {Array.isArray(constraint.value)
                  ? constraint.value.join(', ')
                  : String(constraint.value)}
              </span>
              <span className="small faint">&ldquo;{constraint.source}&rdquo;</span>
            </div>
          ))}
        </div>
      )}

      {intent.advisory.length > 0 && (
        <div className="constraint-block">
          <div className="constraint-title faint">
            ADVISORY — recorded and shown, not mechanically checked
          </div>
          {intent.advisory.map((constraint, index) => (
            <div className="constraint-row advisory" key={`advisory-${index}`}>
              <span className="constraint-value">{String(constraint.value)}</span>
              <span className="small faint">&ldquo;{constraint.source}&rdquo;</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
