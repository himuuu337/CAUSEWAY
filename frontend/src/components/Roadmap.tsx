const STEPS = [
  { label: 'GITHUB INGESTION', state: 'Coming next' },
  { label: 'REAL REPOSITORY CANDIDATES', state: 'Coming next' },
  { label: 'REAL PRODUCTION TELEMETRY', state: 'Coming next' },
]

/**
 * What Causeway closes next, shown as roadmap rather than pretended. Nothing
 * here is wired to a backend, and it says so. The causal experiment and the
 * fix loop above are both real; this is only what comes after them.
 */
export default function Roadmap() {
  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">What's next</h2>
        <span className="card-note">not implemented yet — Milestone 6</span>
      </div>
      <div className="roadmap">
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
