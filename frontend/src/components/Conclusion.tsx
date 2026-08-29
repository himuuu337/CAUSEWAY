import { memo } from 'react'
import type { Assessment, CausewayEvent, Candidate } from '../types'
import { seconds } from '../format'

type ConclusionEvent = Extract<CausewayEvent, { type: 'conclusion' }>

interface Props {
  conclusion: ConclusionEvent
  assessments: Assessment[]
  candidates: Candidate[]
}

function Conclusion({ conclusion, assessments, candidates }: Props) {
  const branchOf: Record<string, string> = {}
  candidates.forEach((candidate) => { branchOf[candidate.change_id] = candidate.branch })
  const proven = conclusion.proven
  const causal = proven.length > 0 ? proven[0] : null

  return (
    <>
      <h2 className="section-title">Correlation versus intervention</h2>

      <div className="contrast" style={{ marginTop: 'var(--gap)' }}>
        <div className="left">
          <h3>Observational ranking</h3>
          {assessments.map((assessment, index) => (
            <div className="rank-row" key={assessment.change_id}>
              <span className="rank-n">#{index + 1}</span>
              <span className="rank-id">{assessment.change_id}</span>
              <span className="rank-branch">{assessment.branch}</span>
              <span
                className="rank-score"
                style={{
                  color: assessment.change_id === conclusion.observational_top_suspect
                    ? 'var(--amber)' : 'var(--text-dim)',
                }}
              >
                {assessment.score.toFixed(3)}
              </span>
            </div>
          ))}
          <div className="suspect-tag">
            TOP SUSPECT: {conclusion.observational_top_suspect}
          </div>
        </div>

        <div className="right">
          <h3>Controlled experiment</h3>
          {Object.keys(conclusion.verdicts).sort().map((id) => (
            <div className="verdict-row" key={id}>
              <span className="rank-id">{id}</span>
              <span className="rank-branch">{branchOf[id] ?? ''}</span>
              <span className={`verdict-pill ${conclusion.verdicts[id]}`}>
                {conclusion.verdicts[id]}
              </span>
            </div>
          ))}
          <div className="small faint" style={{ marginTop: 12 }}>
            measured in {seconds(conclusion.elapsed_s)} on this machine
          </div>
        </div>
      </div>

      <div className="headline">
        {conclusion.correlation_selected_decoy ? (
          <div className="lead">
            <span className="decoy">Correlation selected the decoy.</span>{' '}
            <span className="cause">Controlled intervention identified the causal change.</span>
          </div>
        ) : proven.length > 0 ? (
          <div className="lead">
            <span className="cause">
              Correlation and intervention agree: {proven.join(', ')}.
            </span>
          </div>
        ) : (
          <div className="lead">No candidate survived its experiment. Nothing is claimed.</div>
        )}
        <div className="sub">
          Controlled demo baseline — not a representation of every commercial RCA system.
          The verdicts above were computed from measurements taken during this run, by a
          module no model can reach.
        </div>
      </div>

      {causal && (
        <section className="card">
          <div className="card-head">
            <h2 className="card-title">Causal chain</h2>
            <span className="card-note">fixed explanation for this seeded scenario, not derived from the run</span>
          </div>
          <div className="chain">
            <div className="chain-step first">
              <div className="k">DEPLOYMENT {causal}</div>
              <div className="v">{branchOf[causal] ?? causal}</div>
            </div>
            <div className="chain-link">&darr;</div>
            <div className="chain-step">
              <div className="v">The audit predicate wraps <code>order_id</code> in an expression</div>
            </div>
            <div className="chain-link">&darr;</div>
            <div className="chain-step">
              <div className="v">The index on <code>order_audit(order_id)</code> can no longer be used</div>
            </div>
            <div className="chain-link">&darr;</div>
            <div className="chain-step">
              <div className="v">Every lookup degrades into a full table scan</div>
            </div>
            <div className="chain-link">&darr;</div>
            <div className="chain-step">
              <div className="v">p95 latency on the order audit endpoint rises</div>
            </div>
            <div className="chain-link">&darr;</div>
            <div className="chain-step last">
              <div className="k">INCIDENT</div>
              <div className="v">Order service latency incident</div>
            </div>
          </div>
          <p className="small faint" style={{ marginBottom: 0 }}>
            This chain describes the code path inside the demo service. Causeway measured
            that removing {causal} removes the failure and restoring it brings the failure
            back; the mechanism above is the demo's own explanation of why, not something
            extracted from production telemetry.
          </p>
        </section>
      )}
    </>
  )
}

export default memo(Conclusion)
