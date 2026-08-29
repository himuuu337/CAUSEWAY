import { memo } from 'react'
import type { HypothesisView } from '../useInvestigation'
import { plannerDetail, plannerTagClass } from '../provenance'

interface Props { views: HypothesisView[] }

function PlanPanel({ views }: Props) {
  const withPlans = views.filter((view) => view.plan)
  if (withPlans.length === 0) return null

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Experiment plan</h2>
        <span className="card-note">the planner proposes; it never decides</span>
      </div>

      <div className="plan-grid">
        {withPlans.map((view) => {
          const plan = view.plan!
          const signature = plan.expected_signature
          const validation = view.validation
          return (
            <div className="cand" key={view.id}>
              <div className="cand-top">
                <span className="cand-id">{view.id}</span>
                <span className="cand-branch">HYPOTHESIS {view.id}</span>
                <div className="spacer" style={{ flex: '1 1 auto' }} />
                {view.provenance && (
                  <span className={plannerTagClass(view.provenance)}>
                    {plannerDetail(view.provenance).label.toUpperCase()}
                  </span>
                )}
              </div>

              {view.provenance?.used_fallback && (
                <p className="small faint" style={{ marginBottom: 0 }}>
                  fell back from {view.provenance.proposed_by} — {view.provenance.fallback_reason}
                </p>
              )}

              <dl className="kv" style={{ marginTop: 14 }}>
                <dt>Intervention</dt>
                <dd>
                  set {plan.intervention.flag} = {plan.intervention.value ? 'on' : 'off'}
                  {view.holdingFixed && view.holdingFixed.length > 0
                    ? `, hold ${view.holdingFixed.join(', ')} fixed`
                    : ', hold every other flag fixed'}
                </dd>
                <dt>Replay</dt>
                <dd>{plan.fixture_id}</dd>
                <dt>Expects</dt>
                <dd>
                  {signature.metric} {signature.op} {signature.factor}× the {signature.relative_to}{' '}
                  measured beside the phase
                </dd>
                <dt>Separates</dt>
                <dd>{plan.discriminates_between.join(' vs ')}</dd>
              </dl>

              <blockquote className="quote">{plan.reasoning_summary}</blockquote>
              <div className="notice">
                {view.provenance && plannerDetail(view.provenance).isAi
                  ? 'AI reasoning does not determine the verdict — it is quoted here and never read by the engine.'
                  : 'Planner reasoning does not determine the verdict — it is quoted here and never read by the engine.'}
              </div>

              {validation && (
                <>
                  <div className="validator-line">
                    <span className="card-title" style={{ margin: 0 }}>Experiment validator</span>
                    <span className="validator-count">
                      {validation.passed} / {validation.total} PASSED
                    </span>
                  </div>
                  <details className="phases">
                    <summary>Validator checks</summary>
                    <div className="checks">
                      {validation.checks.map((check) => (
                        <div className="check" key={check.name}>
                          <span className={check.passed ? 'ok' : 'no'}>
                            {check.passed ? '✓' : '✕'}
                          </span>
                          <span className="cname">{check.name}</span>
                          <span className="cdetail">{check.detail}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                </>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

export default memo(PlanPanel)
