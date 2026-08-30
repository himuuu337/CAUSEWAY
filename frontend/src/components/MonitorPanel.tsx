import { useState } from 'react'
import type { MonitorState } from '../useMonitor'
import type { RiskLevel } from '../types'
import { ms } from '../format'
import SystemRiskPanel from './SystemRiskPanel'

interface Props {
  monitor: MonitorState
  onConnect: () => void
  onOpenInvestigation: (runId: string) => void
}

const LEVEL_WORD: Record<RiskLevel, string> = { LOW: 'LOW', MEDIUM: 'MEDIUM', HIGH: 'HIGH' }

function levelClass(level: RiskLevel, confirmed: boolean): string {
  if (level === 'HIGH') return confirmed ? 'risk-pill high confirmed' : 'risk-pill high'
  if (level === 'MEDIUM') return 'risk-pill medium'
  return 'risk-pill low'
}

function metric(values: Record<string, number | string> | undefined, key: string): number | undefined {
  const v = values?.[key]
  return typeof v === 'number' ? v : undefined
}

/**
 * Live production monitoring: real telemetry in, a deterministic risk
 * picture out, and - only once a detector's HIGH has actually been
 * confirmed by sustained evidence - the incidents it opened. Nothing here
 * computes a risk level; every number and every pill arrived on an event
 * causeway.prediction emitted.
 */
export default function MonitorPanel({ monitor, onConnect, onOpenInvestigation }: Props) {
  const [service, setService] = useState('order-service-pool')
  const [repoUrl, setRepoUrl] = useState('')
  const [registering, setRegistering] = useState(false)
  const [registerMessage, setRegisterMessage] = useState('')

  const services = Object.keys(monitor.latestTelemetry)
  const primaryService = services.includes(service) ? service : services[0]
  const values = primaryService ? monitor.latestTelemetry[primaryService] : undefined
  const risks = primaryService ? monitor.risk[primaryService] ?? [] : []
  const worst = risks.reduce<typeof risks[number] | undefined>((acc, r) => {
    const rank = { LOW: 0, MEDIUM: 1, HIGH: 2 }
    return !acc || rank[r.level] > rank[acc.level] ? r : acc
  }, undefined)

  const registerService = async () => {
    if (!service.trim() || !repoUrl.trim()) return
    setRegistering(true)
    setRegisterMessage('')
    try {
      const response = await fetch('/api/services/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: service.trim(), repository_url: repoUrl.trim() }),
      })
      const body = await response.json().catch(() => ({}))
      setRegisterMessage(response.ok
        ? `linked ${service.trim()} → ${repoUrl.trim()}`
        : (body?.detail?.message ?? 'registration failed'))
    } catch {
      setRegisterMessage('cannot reach the backend')
    } finally {
      setRegistering(false)
    }
  }

  return (
    <section className="card monitor-card">
      <div className="card-head">
        <h2 className="card-title">
          {primaryService ? `MONITORING ${primaryService}` : 'PRODUCTION MONITORING'}
        </h2>
        <span className="card-note">
          real telemetry → deterministic risk detection → confirmed incident → investigation
        </span>
        <div className="spacer" />
        <span className={`verdict-pill ${monitor.connection === 'open' ? 'repo-loaded' : 'waiting'}`}>
          {monitor.connection === 'open' ? 'LIVE' : monitor.connection.toUpperCase()}
        </span>
      </div>

      {monitor.connection !== 'open' && (
        <div className="action" style={{ marginTop: 0 }}>
          <button className="run-btn" onClick={onConnect}>CONNECT TO LIVE TELEMETRY</button>
          <span className="hint">
            then run: python -m causeway.cli telemetry-demo
          </span>
        </div>
      )}

      {monitor.connection === 'open' && (
        <SystemRiskPanel monitor={monitor} selectedService={primaryService} onSelectService={setService} />
      )}

      <div className="repo-input-row" style={{ marginTop: 14 }}>
        <label className="repo-label" htmlFor="monitor-service">Service</label>
        <input id="monitor-service" className="repo-input" style={{ flex: '0 1 220px' }}
              value={service} onChange={(e) => setService(e.target.value)} />
        <label className="repo-label" htmlFor="monitor-repo">GitHub repository</label>
        <input id="monitor-repo" className="repo-input" placeholder="https://github.com/owner/repo"
              value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} />
        <button className="run-btn" style={{ padding: '11px 16px' }} disabled={registering}
               onClick={registerService}>LINK REPOSITORY</button>
      </div>
      {registerMessage && <div className="hint indent">{registerMessage}</div>}

      {values && (
        <div className="incident" style={{ marginTop: 14 }}>
          <div className="stat-row">
            <div>
              <div className="stat-label">P95</div>
              <div className="stat-value">{ms(metric(values, 'p95_ms'))}</div>
            </div>
            <div>
              <div className="stat-label">Errors</div>
              <div className={`stat-value ${(metric(values, 'error_rate') ?? 0) > 0.1 ? 'bad' : ''}`}>
                {metric(values, 'error_rate') !== undefined
                  ? `${((metric(values, 'error_rate') as number) * 100).toFixed(1)}%` : '—'}
              </div>
            </div>
            <div>
              <div className="stat-label">Memory</div>
              <div className="stat-value">
                {metric(values, 'memory_percent') !== undefined
                  ? `${metric(values, 'memory_percent')!.toFixed(0)}%` : '—'}
              </div>
            </div>
            <div>
              <div className="stat-label">DB Pool</div>
              <div className={`stat-value ${
                (metric(values, 'db_pool_used') ?? 0) >= (metric(values, 'db_pool_capacity') ?? Infinity)
                  ? 'bad' : ''}`}>
                {metric(values, 'db_pool_used') !== undefined && metric(values, 'db_pool_capacity')
                  ? `${metric(values, 'db_pool_used')}/${metric(values, 'db_pool_capacity')}` : '—'}
              </div>
            </div>
            <div>
              <div className="stat-label">Waiting</div>
              <div className="stat-value">{metric(values, 'db_waiting_requests') ?? '—'}</div>
            </div>
          </div>
        </div>
      )}

      {worst && (
        <div className="risk-monitor">
          <div className="constraint-title">RISK MONITOR</div>
          <div className="risk-row">
            <span className={levelClass(worst.level, worst.confirmed)}>
              {LEVEL_WORD[worst.level]}{worst.confirmed ? ' · CONFIRMED' : ''}
            </span>
            <span className="v">{worst.predicted_failure}</span>
            {worst.eta_seconds != null && (
              <span className="small faint">ETA ~{worst.eta_seconds.toFixed(0)}s</span>
            )}
          </div>
          <ul className="risk-evidence">
            {worst.evidence.map((line, i) => <li key={i}>{line}</li>)}
          </ul>
        </div>
      )}

      {monitor.incidents.length > 0 && (
        <div className="constraint-block">
          <div className="constraint-title">INCIDENTS</div>
          {monitor.incidents.map((incident) => (
            <div className="repo-meta-row" key={incident.incident_id}>
              <span className="k">{incident.status.replace(/_/g, ' ')}</span>
              <span className="v">
                {incident.predicted_failure} — {incident.service}
                {incident.run_id && (
                  <>
                    {' '}
                    <button className="link-btn" onClick={() => onOpenInvestigation(incident.run_id!)}>
                      view investigation →
                    </button>
                  </>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
