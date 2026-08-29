import type { Assessment, Candidate, Exclusion } from '../types'

interface Props {
  candidates: Candidate[]
  excluded: Exclusion[]
  assessments: Assessment[]
  topSuspect: string | null
  deploysConsidered: number
}

/**
 * Localisation, then the observational ranking over it.
 *
 * No verdict appears here even after the experiments finish: this section is
 * the state of belief BEFORE any intervention, and letting a result leak back
 * into it would flatten the whole point of the demo.
 */
export default function Candidates({
  candidates, excluded, assessments, topSuspect, deploysConsidered,
}: Props) {
  if (candidates.length === 0) return null
  const scores: Record<string, Assessment> = {}
  assessments.forEach((assessment) => { scores[assessment.change_id] = assessment })

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Suspected changes</h2>
        <span className="card-note">
          {deploysConsidered} deploys considered · {candidates.length} inside the service
          and window · ranked by a controlled-demo observational baseline
        </span>
      </div>

      <div className="cand-grid">
        {candidates.map((candidate) => {
          const assessment = scores[candidate.change_id]
          const suspect = candidate.change_id === topSuspect
          return (
            <div className={`cand${suspect ? ' suspect' : ''}`} key={candidate.change_id}>
              <div className="cand-top">
                <span className="cand-id">{candidate.change_id}</span>
                <span className="cand-branch">{candidate.branch}</span>
              </div>
              <div className="cand-meta">
                <span>{candidate.files_changed} file{candidate.files_changed === 1 ? '' : 's'}</span>
                <span>{candidate.lines_changed} lines</span>
                <span className="mono">{candidate.sha}</span>
              </div>
              {assessment && (
                <div className="cand-score">
                  <div className="small faint">
                    Observational score{' '}
                    <span className="mono" style={{ color: 'var(--text)' }}>
                      {assessment.score.toFixed(3)}
                    </span>
                  </div>
                  <div className="score-track">
                    <div
                      className={`score-fill${suspect ? '' : ' low'}`}
                      style={{ width: `${Math.round(assessment.score * 100)}%` }}
                    />
                  </div>
                </div>
              )}
              {suspect && <div className="suspect-tag">TOP OBSERVATIONAL SUSPECT</div>}
            </div>
          )
        })}
      </div>

      {excluded.length > 0 && (
        <div className="excluded">
          {excluded.map((item) => (
            <div key={item.change_id}>
              <span className="mono">{item.change_id}</span> {item.branch} — {item.reason}
            </div>
          ))}
        </div>
      )}

      <p className="small faint" style={{ marginBottom: 0 }}>
        Controlled-demo observational baseline. It ranks changes on the evidence a
        correlation-only view has — service, recency, diff size, hot-path overlap — and
        it is not a model of any commercial product.
      </p>
    </section>
  )
}
