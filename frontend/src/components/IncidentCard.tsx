import { memo } from 'react'
import type { CausewayEvent } from '../types'
import { ms } from '../format'

type IncidentEvent = Extract<CausewayEvent, { type: 'incident' }>

interface Props {
  incident?: IncidentEvent
  title: string
  service: string
}

/**
 * Measured on this machine at setup, not shipped as a constant. Until the
 * incident event arrives there is nothing honest to show, so nothing is shown.
 */
function IncidentCard({ incident, title, service }: Props) {
  const cal = incident?.calibration
  const healthy = cal?.healthy_p95_ms
  const broken = cal?.incident_p95_ms
  const ratio = cal?.ratio
  const healthyWidth = healthy && broken ? Math.max(1, (healthy / broken) * 100) : 0

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">{title}</h2>
        <span className="card-note">{service}</span>
        <div className="spacer" />
        <span className="card-note">
          {incident
            ? `replay ${incident.fixture.id} · ${incident.fixture.requests} requests · concurrency ${incident.fixture.concurrency} · ${incident.repetitions} repetitions per phase`
            : 'awaiting investigation'}
        </span>
      </div>

      <div className="incident">
        <div className="stat-row">
          <div>
            <div className="stat-label">Healthy p95</div>
            <div className="stat-value good">{ms(healthy)}</div>
          </div>
          <div>
            <div className="stat-label">Incident p95</div>
            <div className="stat-value bad">{ms(broken)}</div>
          </div>
          <div>
            <div className="stat-label">Slower by</div>
            <div className="stat-value big">{ratio ? `${ratio.toFixed(1)}×` : '—'}</div>
          </div>
        </div>

        <div className="lat-bars">
          <div className="lat-row">
            <div className="lat-name">Healthy</div>
            <div className="lat-track">
              <div className="lat-fill good" style={{ width: `${healthyWidth}%` }} />
            </div>
            <div className="lat-value">{ms(healthy)}</div>
          </div>
          <div className="lat-row">
            <div className="lat-name">Incident</div>
            <div className="lat-track">
              <div className="lat-fill bad" style={{ width: broken ? '100%' : '0%' }} />
            </div>
            <div className="lat-value">{ms(broken)}</div>
          </div>
        </div>
      </div>
    </section>
  )
}

export default memo(IncidentCard)
