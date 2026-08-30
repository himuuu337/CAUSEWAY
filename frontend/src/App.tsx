/**
 * Causeway — experimental root-cause verification.
 *
 * The page builds itself from Server-Sent Events as a real investigation runs.
 * Every latency, phase state, planner provenance, validator result and verdict
 * on screen arrived from the backend; nothing here is computed, inferred or
 * revealed before the event that carries it.
 *
 * There are two investigations behind one form, and the page renders whichever
 * one the backend actually ran. A repository investigation shows the locations
 * a detector found in that repository's own source; the bundled demonstration
 * shows its own A/B candidates and says, in its own panel, that it is a
 * demonstration. Neither set of components is ever shown for the other run.
 */
import { useState } from 'react'
import Header from './components/Header'
import IntentPanel from './components/IntentPanel'
import RepositoryPanel from './components/RepositoryPanel'
import IncidentCard from './components/IncidentCard'
import Pipeline from './components/Pipeline'
import Candidates from './components/Candidates'
import HypothesisPanel from './components/HypothesisPanel'
import PlanPanel from './components/PlanPanel'
import ExperimentPanel from './components/ExperimentPanel'
import Conclusion from './components/Conclusion'
import RepositoryConclusion from './components/RepositoryConclusion'
import FixPanel from './components/FixPanel'
import RequestedChangePanel from './components/RequestedChangePanel'
import MonitorPanel from './components/MonitorPanel'
import CausalGraph from './components/CausalGraph'
import Roadmap from './components/Roadmap'
import EventFeed from './components/EventFeed'
import type { HypothesisView } from './useInvestigation'
import { useInvestigation } from './useInvestigation'
import { useMonitor } from './useMonitor'
import './styles.css'

/** The three things a user can ask for, plus letting the words decide. */
const MODES = [
  { value: '', label: 'Read my instruction' },
  { value: 'diagnose_only', label: 'Diagnose only' },
  { value: 'diagnose_and_fix', label: 'Diagnose and fix' },
  { value: 'requested_change', label: 'Requested change' },
] as const

export default function App() {
  const { state, health, busy, starting, start, attach, pipeline } = useInvestigation()
  const monitor = useMonitor()
  const [repoUrl, setRepoUrl] = useState('')
  const [instruction, setInstruction] = useState('')
  const [mode, setMode] = useState<string>('')

  const notSeeded = health !== null && !health.seeded
  const views: HypothesisView[] = state.order
    .map((id) => state.hypotheses[id])
    .filter((view): view is HypothesisView => view !== undefined)

  const hasRepoInput = repoUrl.trim().length > 0
  // Which run this is, decided by what the backend emitted - never by what
  // was typed into the form.
  const isRepositoryRun = state.repository !== undefined
  const isRequestedChangeRun = state.requestedChange !== undefined

  const buttonLabel = starting
    ? 'STARTING…'
    : state.runState === 'running'
      ? 'INVESTIGATION RUNNING…'
      : hasRepoInput
        ? 'ANALYZE REPOSITORY & RUN CAUSAL INVESTIGATION'
        : 'RUN THE BUNDLED DEMONSTRATION'

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

      <MonitorPanel
        monitor={monitor.state}
        onConnect={monitor.connect}
        onOpenInvestigation={(runId) => attach(runId)}
      />

      <div className="repo-input-row">
        <label className="repo-label" htmlFor="repo-url">GitHub repository</label>
        <input
          id="repo-url"
          className="repo-input"
          type="text"
          inputMode="url"
          placeholder="https://github.com/owner/repo  (leave blank for the bundled demonstration)"
          value={repoUrl}
          onChange={(event) => setRepoUrl(event.target.value)}
          disabled={busy}
        />
      </div>

      <div className="repo-input-row">
        <label className="repo-label" htmlFor="instruction">What should Causeway do?</label>
        <textarea
          id="instruction"
          className="repo-input instruction-input"
          rows={2}
          placeholder="e.g. Fix the bug in this repository and explain the changes"
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          disabled={busy || !hasRepoInput}
        />
        <select
          className="mode-select"
          value={mode}
          onChange={(event) => setMode(event.target.value)}
          disabled={busy || !hasRepoInput}
          aria-label="Mode"
        >
          {MODES.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>
      {!hasRepoInput && (
        <div className="hint indent">
          instructions apply to a repository investigation — the bundled
          demonstration always runs its own fixed scenario
        </div>
      )}

      <div className="action">
        <button
          className="run-btn"
          onClick={() => start(repoUrl.trim() || undefined, instruction, mode || undefined)}
          disabled={busy || (!hasRepoInput && notSeeded)}
        >
          {buttonLabel}
        </button>
        <span className="hint">
          {!hasRepoInput && notSeeded
            ? health?.hint ?? 'this machine is not seeded yet'
            : state.runState === 'running'
              ? `streaming — ${state.events.length} events received`
              : state.runState === 'completed'
                ? 'complete — run it again to measure this machine afresh'
                : hasRepoInput
                  ? 'clones the repository, builds its database from its own schema, reads its source for suspects, then settles them by measuring'
                  : 'reproduces a fabricated incident in a sandbox, removes one change at a time, and measures'}
        </span>
      </div>

      {state.connection === 'reconnecting' && (
        <div className="banner soft">
          stream dropped — reconnecting and resuming from the last event received
        </div>
      )}
      {state.error && <div className="banner">{state.error}</div>}

      {state.intent && (
        <IntentPanel intent={state.intent} clarification={state.clarification} />
      )}

      {state.repository && <RepositoryPanel view={state.repository} />}

      {!isRequestedChangeRun && (
        <>
          <IncidentCard
            incident={state.incident}
            title={incidentTitle}
            service={incidentService}
          />
          <Pipeline stages={pipeline} />
          {state.incident && <CausalGraph state={state} monitor={monitor.state} />}
        </>
      )}

      {isRequestedChangeRun ? (
        <RequestedChangePanel view={state.requestedChange!} />
      ) : (
        <>
          {isRepositoryRun ? (
            <HypothesisPanel
              hypotheses={state.found}
              detectors={state.detectors}
              sources={state.repository?.sources ?? []}
            />
          ) : (
            <Candidates
              candidates={state.candidates}
              excluded={state.excluded}
              assessments={state.assessments}
              topSuspect={state.topSuspect}
              deploysConsidered={state.deploysConsidered}
            />
          )}

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
            isRepositoryRun
              ? <RepositoryConclusion
                  conclusion={state.conclusion}
                  found={state.found}
                  fixSkipped={state.fixSkipped}
                />
              : <Conclusion
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
        </>
      )}

      <Roadmap />

      <EventFeed events={state.events} />
    </div>
  )
}
