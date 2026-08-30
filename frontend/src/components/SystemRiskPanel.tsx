import { useEffect, useMemo, useRef, useState } from 'react'
import type { MonitorState } from '../useMonitor'
import type { PredictionState, RiskAssessment, SystemRisk } from '../types'
import { buildSystemRisk } from '../systemRisk'
import { fetchSystemRisk } from '../systemRiskApi'

interface Props {
  monitor: MonitorState
  selectedService?: string
  onSelectService: (service: string) => void
}

const STATE_LABEL: Record<PredictionState, string> = {
  STABLE: 'STABLE', WATCH: 'WATCH', ELEVATED: 'ELEVATED', HIGH_RISK: 'HIGH RISK',
  INSUFFICIENT_DATA: 'INSUFFICIENT DATA',
}
const STATE_CLASS: Record<PredictionState, string> = {
  STABLE: 'state-pill stable', WATCH: 'state-pill watch', ELEVATED: 'state-pill elevated',
  HIGH_RISK: 'state-pill high-risk', INSUFFICIENT_DATA: 'state-pill insufficient',
}

function worstOf(assessments: RiskAssessment[]): RiskAssessment | undefined {
  return assessments.reduce<RiskAssessment | undefined>(
    (acc, a) => (!acc || a.score > acc.score ? a : acc), undefined)
}

/**
 * SYSTEM RISK: the rollup across every service the live telemetry feed has
 * seen. Renders instantly from buildSystemRisk(monitor) - the same
 * MonitorState every other part of this page already folds from SSE - then
 * prefers the backend's own GET /api/prediction/system once it lands,
 * exactly the same instant-local / backend-preferred pattern CausalGraph.tsx
 * uses. Never a fabricated forecast: a service with no assessments yet
 * reads INSUFFICIENT_DATA, not STABLE.
 */
export default function SystemRiskPanel({ monitor, selectedService, onSelectService }: Props) {
  const localRisk = useMemo(() => buildSystemRisk(monitor), [monitor])
  const [serverRisk, setServerRisk] = useState<SystemRisk | null>(null)
  const [serverConfirmed, setServerConfirmed] = useState(false)
  const requestId = useRef(0)

  useEffect(() => {
    if (monitor.connection !== 'open') return undefined
    const thisRequest = ++requestId.current
    const timer = window.setTimeout(() => {
      fetchSystemRisk()
        .then((fetched) => {
          if (requestId.current !== thisRequest) return
          setServerRisk(fetched)
          setServerConfirmed(true)
        })
        .catch(() => {
          // BACKEND_UNAVAILABLE for this one call - the live rollup keeps
          // rendering, silently, exactly as it was before this request.
        })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [monitor.connection, monitor.events.length])

  const risk = serverRisk ?? localRisk

  return (
    <div className="system-risk">
      <div className="system-risk-head">
        <div className="constraint-title">SYSTEM RISK</div>
        <span className={STATE_CLASS[risk.state]}>{STATE_LABEL[risk.state]}</span>
        <span className="system-risk-score">{Math.round(risk.score)} / 100</span>
        <div className="spacer" />
        <span className={`graph-source-tag${serverConfirmed ? ' confirmed' : ''}`}>
          {serverConfirmed ? 'BACKEND-VERIFIED' : 'LIVE'}
        </span>
      </div>

      {risk.services.length === 0 ? (
        <p className="small faint" style={{ marginTop: 8 }}>
          No telemetry received yet — insufficient data to assess system risk.
        </p>
      ) : (
        <>
          <div className="small faint" style={{ margin: '4px 0 10px' }}>
            {risk.services_degraded} of {risk.services.length} service{risk.services.length === 1 ? '' : 's'} showing degradation
          </div>
          <div className="system-risk-list">
            {risk.services.map((s) => {
              const worst = worstOf(s.assessments)
              return (
                <button
                  key={s.service}
                  type="button"
                  className={`system-risk-row${s.service === selectedService ? ' active' : ''}`}
                  onClick={() => onSelectService(s.service)}
                >
                  <span className={STATE_CLASS[s.state]}>{STATE_LABEL[s.state]}</span>
                  <span className="system-risk-service mono">{s.service}</span>
                  <span className="system-risk-detail">
                    {worst
                      ? `${worst.predicted_failure}${worst.eta_seconds != null ? ` · ETA ~${Math.round(worst.eta_seconds)}s` : ''}`
                      : 'no detector has enough signal yet'}
                  </span>
                  <span className="system-risk-score-small mono">{Math.round(s.score)}</span>
                </button>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
