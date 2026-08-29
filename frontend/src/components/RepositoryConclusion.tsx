import type { CausewayEvent, CodeHypothesis, Verdict } from '../types'
import { seconds } from '../format'

type ConclusionEvent = Extract<CausewayEvent, { type: 'conclusion' }>

interface Props {
  conclusion: ConclusionEvent
  found: CodeHypothesis[]
  fixSkipped?: { reason: string; mode: string }
}

/**
 * The conclusion of a repository investigation.
 *
 * There is no correlation ranking here, and that absence is deliberate: a
 * repository has no fabricated deploy history to correlate against, so
 * Causeway does not invent one. What it has instead is a set of locations
 * that look identical to static analysis, and a measurement that separates
 * them. Every verdict below arrived from causeway.verdict; this component
 * arranges words around numbers it did not compute.
 */
export default function RepositoryConclusion({ conclusion, found, fixSkipped }: Props) {
  const labelOf: Record<string, string> = {}
  found.forEach((hypothesis) => { labelOf[hypothesis.id] = hypothesis.label })

  const ids = Object.keys(conclusion.verdicts)
  const proven = conclusion.proven
  const refuted = conclusion.refuted
  const separated = proven.length > 0 && refuted.length > 0

  return (
    <>
      <h2 className="section-title">What the experiments settled</h2>

      <section className="card">
        <div className="card-head">
          <h2 className="card-title">Verdicts</h2>
          <span className="card-note">
            measured in {seconds(conclusion.elapsed_s)} on this machine
          </span>
        </div>

        {ids.map((id) => (
          <div className="verdict-row" key={id}>
            <span className="rank-id mono">{labelOf[id] ?? id}</span>
            <div className="spacer" />
            <span className={`verdict-pill ${conclusion.verdicts[id] as Verdict}`}>
              {conclusion.verdicts[id]}
            </span>
          </div>
        ))}

        <div className="headline">
          {separated ? (
            <div className="lead">
              <span className="decoy">
                Static analysis could not tell these apart.
              </span>{' '}
              <span className="cause">
                The experiment did: {(conclusion.proven_labels ?? proven).join(', ')}.
              </span>
            </div>
          ) : proven.length > 0 ? (
            <div className="lead">
              <span className="cause">
                {(conclusion.proven_labels ?? proven).join(', ')} reproduced the failure,
                recovered when removed, and failed again when restored.
              </span>
            </div>
          ) : (
            <div className="lead">
              No location survived its experiment. Nothing is claimed.
            </div>
          )}
          <div className="sub">
            Each verdict was computed from measurements taken during this run, by a
            module no model can reach. Removing a location and measuring is the only
            evidence used; nothing was inferred from how the code looks.
          </div>
        </div>
      </section>

      {fixSkipped && (
        <section className="card">
          <div className="card-head">
            <h2 className="card-title">No fix was generated</h2>
            <div className="spacer" />
            <span className="verdict-pill waiting">{fixSkipped.mode.toUpperCase()}</span>
          </div>
          <p className="small" style={{ marginBottom: 0 }}>
            {fixSkipped.reason}. The experiments above still edited source — but only
            inside disposable copies, which is a diagnostic intervention, not a
            change anyone is being asked to keep. The repository was never written to.
          </p>
        </section>
      )}
    </>
  )
}
