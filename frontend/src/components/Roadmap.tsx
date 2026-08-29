interface Props { causeVerified: boolean }

const STEPS = [
  { label: 'PROPOSE FIX', state: 'Coming next' },
  { label: 'APPLY IN SANDBOX', state: 'Coming next' },
  { label: 'REPLAY INCIDENT', state: 'Coming next' },
  { label: 'VERIFY RECOVERY', state: 'Coming next' },
]

/**
 * The rest of the loop, shown as roadmap rather than pretended. Nothing here
 * is wired to a backend, and it says so.
 */
export default function Roadmap({ causeVerified }: Props) {
  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Fix verification</h2>
        <span className="card-note">not implemented yet — the loop Causeway closes next</span>
      </div>
      <div className="roadmap">
        <div className={`road${causeVerified ? ' done' : ''}`}>
          <div className="r-label">CAUSE VERIFIED</div>
          <div className="r-state">{causeVerified ? '✓ this run' : 'Run the investigation'}</div>
        </div>
        {STEPS.map((step) => (
          <div className="road" key={step.label}>
            <div className="r-label">{step.label}</div>
            <div className="r-state">{step.state}</div>
          </div>
        ))}
      </div>
    </section>
  )
}
