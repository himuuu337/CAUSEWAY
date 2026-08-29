import type { CausewayEvent } from '../types'
import { ms } from '../format'

type IncidentEvent = Extract<CausewayEvent, { type: 'incident' }>

interface Props {
  incident?: IncidentEvent
  title: string
  service: string
}

/**
 * What is known about the incident before any experiment runs.
 *
 * The two paths know different things here, and the card shows only what the
 * run it is describing actually has.
 *
 * The bundled demonstration was calibrated at seed time, so it can show a
 * healthy and an incident p95 measured on this machine. A repository has no
 * such calibration and Causeway does not invent one: what it has is the
 * symptom its manifest reported, the workload that will be replayed, and the
 * database that was built from its own schema. Showing an empty latency bar
 * there would be worse than showing nothing - it would imply a measurement
 * that has not been taken.
 */
export default function IncidentCard({ incident, title, service }: Props) {
  const cal = incident?.calibration
  const healthy = cal?.healthy_p95_ms
  const broken = cal?.incident_p95_ms
  const ratio = cal?.ratio
  const healthyWidth = healthy && broken ? Math.max(1, (healthy / broken) * 100) : 0

  const replay = incident?.fixture ?? incident?.workload
  const database = incident?.database

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">{title}</h2>
        <span className="card-note">{service}</span>
        <div className="spacer" />
        <span className="card-note">
          {incident && replay
            ? `replay ${replay.id} · ${replay.requests} requests · concurrency ${replay.concurrency} · ${incident.repetitions} repetitions per phase`
            : 'awaiting investigation'}
        </span>
      </div>

      {cal ? (
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
      ) : incident ? (
        <div className="incident">
          <div className="repo-meta-row">
            <span className="k">SYMPTOM</span>
            <span className="v">{String(incident.incident.symptom)}</span>
          </div>
          <div className="repo-meta-row">
            <span className="k">DETECTED</span>
            <span className="v mono">{String(incident.incident.detected_at)}</span>
          </div>
          {incident.verification && (
            <div className="repo-meta-row">
              <span className="k">VERIFIED BY</span>
              <span className="v mono">{incident.verification}</span>
            </div>
          )}
          {database && (
            <div className="repo-meta-row">
              <span className="k">AGAINST</span>
              <span className="v mono">
                {Object.entries(database.tables)
                  .map(([table, rows]) => `${table} ${rows.toLocaleString()} rows`)
                  .join(' · ')}
              </span>
            </div>
          )}
          <p className="small faint" style={{ marginBottom: 0, marginTop: 10 }}>
            No healthy or incident latency is shown yet, because none has been
            measured yet. Every number in this run is measured during the
            experiment, against a control taken beside it seconds earlier.
          </p>
        </div>
      ) : (
        <div className="incident">
          <p className="small faint" style={{ marginBottom: 0 }}>
            Nothing has been measured yet.
          </p>
        </div>
      )}
    </section>
  )
}
