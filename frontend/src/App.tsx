/**
 * Causeway — experimental root-cause verification.
 *
 * The page builds itself from Server-Sent Events as a real investigation runs.
 * Every latency, phase state, planner provenance, validator result and verdict
 * on screen arrived from the backend; nothing here is computed, inferred or
 * revealed before the event that carries it.
 */
import Header from './components/Header'
import IncidentCard from './components/IncidentCard'
import Pipeline from './components/Pipeline'
import Candidates from './components/Candidates'
import PlanPanel from './components/PlanPanel'
import ExperimentPanel from './components/ExperimentPanel'
import Conclusion from './components/Conclusion'
import Roadmap from './components/Roadmap'
import EventFeed from './components/EventFeed'
import type { HypothesisView } from './useInvestigation'
import { useInvestigation } from './useInvestigation'
import './styles.css'

export default function App() {
  const { state, health, busy, starting, start, pipeline } = useInvestigation()

  const notSeeded = health !== null && !health.seeded
  const views: HypothesisView[] = state.order
    .map((id) => state.hypotheses[id])
    .filter((view): view is HypothesisView => view !== undefined)

  const buttonLabel = starting
    ? 'STARTING…'
    : state.runState === 'running'
      ? 'INVESTIGATION RUNNING…'
      : 'RUN CAUSAL INVESTIGATION'

  return (
    <div className="wrap">
      <Header
        health={health}
        runState={state.runState}
        connection={state.connection}
        runId={state.runId}
      />

      <div className="action">
        <button className="run-btn" onClick={start} disabled={busy || notSeeded}>
          {buttonLabel}
        </button>
        <span className="hint">
          {notSeeded
            ? health?.hint ?? 'this machine is not seeded yet'
            : state.runState === 'running'
              ? `streaming — ${state.events.length} events received`
              : state.runState === 'completed'
                ? 'complete — run it again to measure this machine afresh'
                : 'reproduces the incident in a sandbox, removes one change at a time, and measures'}
        </span>
      </div>

      {state.connection === 'reconnecting' && (
        <div className="banner soft">
          stream dropped — reconnecting and resuming from the last event received
        </div>
      )}
      {state.error && <div className="banner">{state.error}</div>}

      <IncidentCard
        incident={state.incident}
        title={health ? health.incident.title : 'Order service latency incident'}
        service={health ? health.incident.service : 'order-service'}
      />

      <Pipeline stages={pipeline} />

      <Candidates
        candidates={state.candidates}
        excluded={state.excluded}
        assessments={state.assessments}
        topSuspect={state.topSuspect}
        deploysConsidered={state.deploysConsidered}
      />

      <PlanPanel views={views} />

      {views.some((view) => view.started) && (
        <h2 className="section-title">Controlled experiments</h2>
      )}
      {views.map((view) => (
        <ExperimentPanel
          key={view.id}
          view={view}
          candidate={state.candidates.find((c) => c.change_id === view.id)}
          active={state.activeHypothesis === view.id}
        />
      ))}

      {state.conclusion && (
        <Conclusion
          conclusion={state.conclusion}
          assessments={state.assessments}
          candidates={state.candidates}
        />
      )}

      <Roadmap causeVerified={(state.conclusion?.proven.length ?? 0) > 0} />

      <EventFeed events={state.events} />
    </div>
  )
}
