/**
 * Milestone 2: the wire, made visible.
 *
 * Deliberately unstyled. The only thing this page has to prove is that a real
 * investigation runs in the backend and every number, phase state and verdict
 * on screen arrived from it - so the raw event feed is on the page too, and
 * nothing here computes a result.
 */
import type { Provenance } from './types'
import { useInvestigation } from './useInvestigation'
import './styles.css'

/** Never call the fallback "Gemini". Never call the configured planner a fallback. */
function plannerLabel(provenance: Provenance): string {
  if (provenance.used_fallback) return 'Deterministic fallback'
  if (provenance.kind === 'gemini') return `Gemini (${provenance.source})`
  return 'Deterministic planner'
}

const VERDICT_CLASS: Record<string, string> = {
  PROVEN: 'proven', REFUTED: 'refuted', SUPPORTED: 'supported', UNRESOLVED: 'unresolved',
}

export default function App() {
  const { state, health, busy, starting, start } = useInvestigation()

  return (
    <main>
      <header>
        <h1>CAUSEWAY</h1>
        <p className="sub">Experimental root-cause verification</p>
        <p className="sub">
          backend {health ? `${health.status}` : 'unknown'}
          {health && !health.seeded && ` - ${health.hint}`}
          {' · '}stream {state.connection}
          {state.runId && <> · run {state.runId}</>}
          {' · '}state {state.runState}
        </p>
      </header>

      <button onClick={start} disabled={busy || (health ? !health.seeded : false)}>
        {starting ? 'STARTING...' : busy ? 'INVESTIGATION RUNNING...' : 'RUN CAUSAL INVESTIGATION'}
      </button>

      {state.connection === 'reconnecting' && (
        <p className="warn">stream dropped - reconnecting and resuming from the last event received</p>
      )}
      {state.error && <p className="error">{state.error}</p>}

      {state.incident && (
        <section>
          <h2>Incident</h2>
          <p>
            {state.incident.incident.id} · {state.incident.incident.service} ·{' '}
            {state.incident.incident.title}
          </p>
          <p className="sub">
            healthy p95 {state.incident.calibration.healthy_p95_ms} ms · incident p95{' '}
            {state.incident.calibration.incident_p95_ms} ms ·{' '}
            {state.incident.calibration.ratio}x · replay {state.incident.fixture.id}{' '}
            ({state.incident.fixture.requests} requests, concurrency{' '}
            {state.incident.fixture.concurrency}, {state.incident.repetitions} repetitions
            per phase)
          </p>
        </section>
      )}

      {state.candidates.length > 0 && (
        <section>
          <h2>Localisation <span className="sub">deterministic, no model</span></h2>
          <p className="sub">
            {state.deploysConsidered} deploys considered, {state.candidates.length} survived
          </p>
          <table>
            <tbody>
              {state.candidates.map((candidate) => (
                <tr key={candidate.change_id}>
                  <td className="id">{candidate.change_id}</td>
                  <td>{candidate.branch}</td>
                  <td className="num">{candidate.files_changed} files</td>
                  <td className="num">{candidate.lines_changed} lines</td>
                </tr>
              ))}
              {state.excluded.map((exclusion) => (
                <tr key={exclusion.change_id} className="sub">
                  <td className="id">{exclusion.change_id}</td>
                  <td colSpan={3}>excluded - {exclusion.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {state.assessments.length > 0 && (
        <section>
          <h2>Observational ranking <span className="sub">correlation only, no experiment</span></h2>
          <table>
            <tbody>
              {state.assessments.map((assessment, index) => (
                <tr key={assessment.change_id}>
                  <td className="num">#{index + 1}</td>
                  <td className="id">{assessment.change_id}</td>
                  <td>{assessment.branch}</td>
                  <td className="num">{assessment.score.toFixed(3)}</td>
                  <td className="sub">
                    {assessment.change_id === state.topSuspect ? 'TOP OBSERVATIONAL SUSPECT' : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="sub">
            A stand-in for correlation-only reasoning, built for this controlled demo. Not a
            model of any commercial product.
          </p>
        </section>
      )}

      {state.order.map((id) => {
        const view = state.hypotheses[id]
        if (!view) return null
        return (
          <section key={id}>
            <h2>
              Hypothesis {id}
              {view.verdict && (
                <span className={`verdict ${VERDICT_CLASS[view.verdict] ?? ''}`}>
                  {view.verdict}
                </span>
              )}
            </h2>

            {view.provenance && (
              <p>
                planner: <strong>{plannerLabel(view.provenance)}</strong>
                <span className="sub">
                  {' '}(source={view.provenance.source}, kind={view.provenance.kind},
                  used_fallback={String(view.provenance.used_fallback)})
                </span>
                {view.provenance.fallback_reason && (
                  <span className="sub"> - {view.provenance.fallback_reason}</span>
                )}
              </p>
            )}

            {view.plan && (
              <>
                <p className="sub">
                  intervention: set {view.plan.intervention.flag} ={' '}
                  {view.plan.intervention.value ? 'on' : 'off'}, holding every other flag fixed ·
                  fixture {view.plan.fixture_id} · expects {view.plan.expected_signature.metric}{' '}
                  {view.plan.expected_signature.op} {view.plan.expected_signature.factor}x the
                  local control
                </p>
                <blockquote>
                  {view.plan.reasoning_summary}
                  <span className="sub"> — planner reasoning, never read by the engine</span>
                </blockquote>
              </>
            )}

            {view.validation && (
              <p className="sub">
                validator: {view.validation.passed}/{view.validation.total} checks passed —{' '}
                {view.validation.checks.map((check) => check.name).join(', ')}
              </p>
            )}

            {view.phases.length > 0 && (
              <table>
                <tbody>
                  {view.phases.map((row) => (
                    <tr key={row.phase} className={row.role === 'control' ? 'sub' : ''}>
                      <td>{row.phase}</td>
                      <td className="num">
                        {row.p95_ms !== undefined
                          ? `${row.p95_ms.toFixed(2)} ms`
                          : row.running
                            ? 'measuring...'
                            : ''}
                      </td>
                      <td className="num">{row.reps ? `${row.reps} reps` : ''}</td>
                      <td className={row.state ? `state ${row.state}` : ''}>
                        {row.state ? row.state.toUpperCase() : ''}
                      </td>
                      <td className="num sub">
                        {row.ratio != null ? `${row.ratio}x of ${row.localControlMs} ms` : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {view.reason && <p className="sub">{view.reason}</p>}
          </section>
        )
      })}

      {state.conclusion && (
        <section>
          <h2>Conclusion</h2>
          <p>
            observational ranking put {state.conclusion.observational_top_suspect} first ·{' '}
            {Object.entries(state.conclusion.verdicts)
              .map(([id, verdict]) => `${id} ${verdict}`)
              .join(' · ')}
          </p>
          {state.conclusion.correlation_selected_decoy && (
            <p>
              Correlation selected the decoy. Controlled intervention identified the causal
              change: {state.conclusion.proven.join(', ')}.
            </p>
          )}
          <p className="sub">completed in {state.conclusion.elapsed_s}s</p>
        </section>
      )}

      <section>
        <h2>Event feed <span className="sub">{state.events.length} events, as received</span></h2>
        <pre>
          {state.events
            .slice(-40)
            .map((event, index) => `${index}  ${JSON.stringify(event).slice(0, 150)}`)
            .join('\n')}
        </pre>
      </section>
    </main>
  )
}
