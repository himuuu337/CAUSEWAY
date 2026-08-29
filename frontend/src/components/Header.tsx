import type { Health, RunState } from '../types'

interface Props {
  health: Health | null
  runState: RunState | 'idle'
  connection: string
  runId: string | null
}

const LABEL: Record<string, string> = {
  idle: 'IDLE', running: 'RUNNING', completed: 'COMPLETED', failed: 'FAILED',
}

export default function Header({ health, runState, connection, runId }: Props) {
  const state = LABEL[runState] ?? String(runState).toUpperCase()
  return (
    <header className="masthead">
      <div className="brand">
        <h1>CAUSEWAY</h1>
        <p>Experimental root-cause verification</p>
      </div>

      <div className="spacer" />

      <div className="badge-row">
        <span className="badge solid">{health ? health.incident.id : 'INCIDENT-001'}</span>
        <span className="badge">{health ? health.incident.service : 'order-service'}</span>
        <span className={`badge ${runState}`}>
          {runState === 'running' && <span className="pulse" />}
          {state}
        </span>
        {runId && <span className="badge">run {runId.slice(0, 8)}</span>}
        {connection === 'reconnecting' && <span className="badge running">reconnecting</span>}
      </div>
    </header>
  )
}
