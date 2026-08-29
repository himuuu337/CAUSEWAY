import type { HypothesisView, PhaseRow } from '../useInvestigation'
import type { Candidate } from '../types'
import { ms, share, times } from '../format'

interface Props {
  view: HypothesisView
  candidate?: Candidate
  active: boolean
}

/** The three phases that carry evidence, in the order they are measured. */
const EVIDENCE = ['reproduce', 'ablate', 'restore'] as const

const STEP_WORD: Record<string, string> = {
  reproduce: 'PRESENT', ablate: 'REMOVED', restore: 'RESTORED',
}
const STEP_SUB: Record<string, string> = {
  reproduce: 'incident state reproduced',
  ablate: 'this change removed, every other flag held fixed',
  restore: 'the change put back',
}
const STATE_WORD: Record<string, string> = {
  broken: 'BROKEN', healthy: 'HEALTHY',
  inconclusive: 'INCONCLUSIVE', unstable: 'UNSTABLE',
}

function rowOf(view: HypothesisView, phase: string): PhaseRow | undefined {
  return view.phases.find((row) => row.phase === phase)
}

/**
 * The hero. Three bars, one per evidence phase, each with a dashed tick at the
 * exact control the backend judged it against.
 *
 * Nothing on this panel is computed from measurements. Bar heights are a
 * proportion of the tallest number in the same experiment; every state word,
 * every ratio and the verdict itself arrived as an event.
 */
export default function ExperimentPanel({ view, candidate, active }: Props) {
  if (!view.started) return null

  const values = view.phases
    .map((row) => row.p95_ms)
    .filter((value): value is number => value !== undefined)
  const max = values.length > 0 ? Math.max(...values) : 0

  const points = EVIDENCE.map((phase, index) => {
    const row = rowOf(view, phase)
    const height = share(row?.p95_ms, max)
    return `${16.7 + index * 33.3},${100 - height}`
  }).join(' ')
  const traceReady = EVIDENCE.every((phase) => rowOf(view, phase)?.p95_ms !== undefined)

  return (
    <section className="card exp">
      <div className="card-head">
        <div className="exp-title">
          <span className="cand-id">{view.id}</span>
          <span className="h">Controlled experiment</span>
          {candidate && <span className="card-note mono">{candidate.branch}</span>}
        </div>
        <div className="spacer" />
        {view.verdict
          ? <span className={`verdict-pill ${view.verdict}`}>{view.verdict}</span>
          : <span className="verdict-pill waiting">
              {active ? 'MEASURING…' : 'AWAITING MEASUREMENT'}
            </span>}
      </div>

      <div className="exp-body">
        <div>
          <div className="plot">
            {traceReady && (
              <svg className="trace" viewBox="0 0 100 100" preserveAspectRatio="none">
                <polyline
                  points={points}
                  fill="none"
                  stroke="var(--text-faint)"
                  strokeWidth="1.5"
                  strokeDasharray="3 3"
                  vectorEffect="non-scaling-stroke"
                />
              </svg>
            )}
            {EVIDENCE.map((phase) => {
              const row = rowOf(view, phase)
              const state = row?.state ?? 'pending'
              const height = share(row?.p95_ms, max)
              const control = share(row?.localControlMs, max)
              return (
                <div className="plot-col" key={phase}>
                  <div className="plot-value">
                    {row?.p95_ms !== undefined ? ms(row.p95_ms) : row?.running ? '…' : ''}
                  </div>
                  <div className={`plot-bar ${state}`} style={{ height: `${height}%` }} />
                  {row?.localControlMs !== undefined && (
                    <div className="ctrl-tick" style={{ bottom: `${control}%` }}>
                      <span>control {ms(row.localControlMs)}</span>
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          <div className="plot-axis">
            {EVIDENCE.map((phase) => {
              const row = rowOf(view, phase)
              const state = row?.state
              return (
                <div key={phase}>
                  <div className="axis-label">{view.id} {STEP_WORD[phase]}</div>
                  <div className={`axis-state ${state ?? 'pending'}`}>
                    {state ? STATE_WORD[state] ?? state.toUpperCase() : '—'}
                  </div>
                  <div className="axis-ratio">
                    {row?.ratio != null ? `${times(row.ratio)} its local control` : ''}
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <div className="steps">
          {EVIDENCE.map((phase, index) => {
            const row = rowOf(view, phase)
            const state = row?.state ?? 'pending'
            return (
              <div key={phase}>
                {index > 0 && <div className="step-arrow">&darr;</div>}
                <div className={`step ${state}`}>
                  <div>
                    <div className="step-name">{view.id} {STEP_WORD[phase]}</div>
                    <div className="step-sub">{STEP_SUB[phase]}</div>
                    <span className={`step-chip ${state}`}>
                      {row?.state
                        ? STATE_WORD[row.state] ?? row.state.toUpperCase()
                        : row?.running ? 'MEASURING' : 'PENDING'}
                    </span>
                  </div>
                  <div className="step-num">
                    {row?.p95_ms !== undefined ? ms(row.p95_ms) : '—'}
                    {row?.reps ? <div className="step-sub">{row.reps} reps</div> : null}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {view.reason && <div className="verdict-reason">{view.reason}</div>}

      <details className="phases">
        <summary>All seven phases</summary>
        <table className="phases-table">
          <thead>
            <tr>
              <th>Phase</th><th>p95</th><th>p50</th><th>Reps</th>
              <th>Local control</th><th>Ratio</th><th>Judgement</th>
            </tr>
          </thead>
          <tbody>
            {view.phases.map((row) => (
              <tr key={row.phase} className={row.role === 'control' ? 'control' : ''}>
                <td>{row.phase}</td>
                <td className="num">{row.p95_ms !== undefined ? ms(row.p95_ms) : row.running ? '…' : '—'}</td>
                <td className="num">{row.p50_ms !== undefined ? ms(row.p50_ms) : '—'}</td>
                <td className="num">{row.reps ?? '—'}</td>
                <td className="num">{row.localControlMs !== undefined ? ms(row.localControlMs) : ''}</td>
                <td className="num">{row.ratio != null ? times(row.ratio) : ''}</td>
                <td className={row.state ? `st-${row.state}` : ''}>
                  {row.state ? STATE_WORD[row.state] ?? row.state : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  )
}
