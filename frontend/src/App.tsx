/**
 * Causeway — experimental root-cause verification.
 *
 * The page builds itself from Server-Sent Events as a real investigation runs.
 * Every latency, phase state, planner provenance, validator result and verdict
 * on screen arrived from the backend; nothing here is computed, inferred or
 * revealed before the event that carries it.
 */
import { useState } from 'react'
import Header from './components/Header'
import RepositoryPanel from './components/RepositoryPanel'
import IncidentCard from './components/IncidentCard'
import Pipeline from './components/Pipeline'
import Candidates from './components/Candidates'
import PlanPanel from './components/PlanPanel'
import ExperimentPanel from './components/ExperimentPanel'
import Conclusion from './components/Conclusion'
import FixPanel from './components/FixPanel'
import Roadmap from './components/Roadmap'
import EventFeed from './components/EventFeed'
import type { HypothesisView } from './useInvestigation'
import { useInvestigation } from './useInvestigation'
import './styles.css'

export default function App() {
  const { state, health, busy, starting, start, pipeline } = useInvestigation()
  const [repoUrl, setRepoUrl] = useState('')

  const notSeeded = health !== null && !health.seeded
  const views: HypothesisView[] = state.order
    .map((id) => state.hypotheses[id])
    .filter((view): view is HypothesisView => view !== undefined)

  const hasRepoInput = repoUrl.trim().length > 0
  const buttonLabel = starting
    ? 'STARTING…'
    : state.runState === 'running'
      ? 'INVESTIGATION RUNNING…'
      : hasRepoInput
        ? 'ANALYZE & RUN CAUSAL INVESTIGATION'
        : 'RUN CAUSAL INVESTIGATION'

  // The live investigation's own incident (from the manifest, in repository
  // mode) takes priority over the bundled demo's health-endpoint defaults,
  // which are only ever a pre-run placeholder.
  const incidentTitle = state.incident
    ? state.incident.incident.title
    : health ? health.incident.title : 'Order service latency incident'
  const incidentService = state.incident
    ? state.incident.incident.service
    : health ? health.incident.service : 'order-service'

  return (
    <div className="wrap">
      <Header
        health={health}
        runState={state.runState}
        connection={state.connection}
        runId={state.runId}
        incidentId={state.incident?.incident.id}
        incidentService={state.incident?.incident.service}
      />

      <div className="repo-input-row">
        <label className="repo-label" htmlFor="repo-url">GitHub repository</label>
        <input
          id="repo-url"
          className="repo-input"
          type="text"
          inputMode="url"
          placeholder="https://github.com/owner/repo  (leave blank for the bundled demo)"
          value={repoUrl}
          onChange={(event) => setRepoUrl(event.target.value)}
          disabled={busy}
        />
      </div>

      <div className="action">
        <button className="run-btn" onClick={() => start(repoUrl.trim() || undefined)}
               disabled={busy || notSeeded}>
          {buttonLabel}
        </button>
        <span className="hint">
          {notSeeded
            ? health?.hint ?? 'this machine is not seeded yet'
            : state.runState === 'running'
              ? `streaming — ${state.events.length} events received`
              : state.runState === 'completed'
                ? 'complete — run it again to measure this machine afresh'
                : hasRepoInput
                  ? 'clones the repository, validates it against the Causeway demo contract, then runs the same investigation'
                  : 'reproduces the incident in a sandbox, removes one change at a time, and measures'}
        </span>
      </div>

      {state.connection === 'reconnecting' && (
        <div className="banner soft">
          stream dropped — reconnecting and resuming from the last event received
        </div>
      )}
      {state.error && <div className="banner">{state.error}</div>}

      {state.repository && <RepositoryPanel view={state.repository} />}

      <IncidentCard
        incident={state.incident}
        title={incidentTitle}
        service={incidentService}
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

      {state.fixOrder.length > 0 && (
        <h2 className="section-title">Verified fix</h2>
      )}
      {state.fixOrder.map((id) => (
        <FixPanel
          key={id}
          view={state.fixes[id]}
          candidate={state.candidates.find((c) => c.change_id === id)}
        />
      ))}

      <Roadmap />

      <EventFeed events={state.events} />
    </div>
  )
}
