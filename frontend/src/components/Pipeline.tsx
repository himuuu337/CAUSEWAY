import { Fragment, memo } from 'react'
import type { PipelineStage } from '../useInvestigation'

interface Props { stages: PipelineStage[] }

/**
 * The trust pipeline. Every node's status comes from events that have actually
 * arrived, and the planner node's label is whatever provenance the backend
 * reported - a deterministic run is never called a fallback, and nothing is
 * ever called Gemini unless the backend said so.
 */
function Pipeline({ stages }: Props) {
  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Provenance</h2>
        <span className="card-note">where the model is allowed to sit</span>
      </div>

      <div className="pipeline" aria-live="polite">
        {stages.map((stage, index) => (
          <Fragment key={stage.key}>
            {index > 0 && <div className="pipe-arrow">&rarr;</div>}
            <div className={`pipe-node ${stage.kind} ${stage.status}`}>
              <div className="n-label">{stage.label}</div>
              <div className="n-detail">{stage.detail}</div>
            </div>
          </Fragment>
        ))}
      </div>

      <div className="boundary-note">
        <span><b className="ai-word">AI proposes.</b></span>
        <span><b>Code validates.</b></span>
        <span><b>Measurements decide.</b></span>
        <span className="faint small">
          The verdict is computed by a module that cannot import a model — enforced
          by a test that walks its import graph.
        </span>
      </div>
    </section>
  )
}

export default memo(Pipeline)
