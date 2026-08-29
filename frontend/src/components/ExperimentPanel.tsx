import { Fragment, memo } from 'react'
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

const STAGE_WORD: Record<string, string> = {
  reproduce: 'PRESENT', ablate: 'REMOVED', restore: 'RESTORED',
}

const STATE_WORD: Record<string, string> = {
  broken: 'BROKEN', healthy: 'HEALTHY',
  inconclusive: 'INCONCLUSIVE', unstable: 'UNSTABLE',
}

/**
 * How a phase's state reads in the summary. Phrasing only - the state itself
 * arrived on a phase_judged event, already decided by the engine.
 */
function summaryWord(phase: string, state?: string): string {
  if (!state) return '—'
  if (state === 'broken') {
    if (phase === 'ablate') return 'STILL BROKEN'
    if (phase === 'restore') return 'BROKEN AGAIN'
    return 'BROKEN'
  }
  if (state === 'healthy') return phase === 'reproduce' ? 'NOT REPRODUCED' : 'HEALTHY'
  return STATE_WORD[state] ?? state.toUpperCase()
}

function rowOf(view: HypothesisView, phase: string): PhaseRow | undefined {
  return view.phases.find((row) => row.phase === phase)
}

/**
 * The hero: three stage cards, one per evidence phase, each carrying its own
 * measurement, bar, state and ratio. A reads HIGH → HIGH → HIGH; B reads
 * HIGH → LOW → HIGH, and that shape is the whole demo.
 *
 * Bar width is the only thing derived here, and only as a proportion of the
 * largest of the three measurements. Every number, every state word and the
 * verdict itself arrived from the backend.
 */
function ExperimentPanel({ view, candidate, active }: Props) {
  if (!view.started) return null

  const rows = EVIDENCE.map((phase) => rowOf(view, phase))
  const values = rows
    .map((row) => row?.p95_ms)
    .filter((value): value is number => value !== undefined)
  const max = values.length > 0 ? Math.max(...values) : 0

  return (
    <section className="card exp">
      <div className="exp-head">
        <span className="cand-id">{view.id}</span>
        <div>
          <div className="exp-name">Controlled experiment</div>
          {candidate && <div className="exp-branch">{candidate.branch}</div>}
        </div>
        <div className="spacer" />
        <span aria-live="polite">
          {view.verdict
            ? <span className={`verdict-pill verdict-pill-arrive ${view.verdict}`}>{view.verdict}</span>
            : <span className="verdict-pill waiting">
                {active ? 'MEASURING…' : 'AWAITING MEASUREMENT'}
              </span>}
        </span>
      </div>

      <div className="stages">
        {EVIDENCE.map((phase, index) => {
          const row = rows[index]
          const state = row?.state ?? 'pending'
          const width = share(row?.p95_ms, max)
          return (
            <Fragment key={phase}>
              {index > 0 && <div className="stage-sep" aria-hidden="true">&rarr;</div>}
              <div className={`stage ${state}`}>
                <div className="stage-name">{view.id} {STAGE_WORD[phase]}</div>

                <div className={`stage-value${row?.p95_ms === undefined ? ' pending' : ''}`}>
                  {row?.p95_ms !== undefined
                    ? ms(row.p95_ms)
                    : row?.running ? 'measuring…' : '—'}
                </div>

                <div className="stage-track">
                  <div className={`stage-fill ${state}`} style={{ width: `${width}%` }} />
                </div>

                <div className={`stage-state ${state}`}>
                  {row?.state ? STATE_WORD[row.state] ?? row.state.toUpperCase() : '—'}
                </div>

                <div className="stage-ratio">
                  {row?.ratio != null ? `${times(row.ratio)} local control` : ''}
                </div>
                <div className="stage-control">
                  {row?.localControlMs !== undefined
                    ? `Local control: ${ms(row.localControlMs)}`
                    : ''}
                </div>
              </div>
            </Fragment>
          )
        })}
      </div>

      <div className="exp-summary">
        <div className="summary-lines">
          {EVIDENCE.map((phase, index) => (
            <div key={phase}>
              {view.id} {STAGE_WORD[phase]}
              <span className="arrow">&rarr;</span>
              <b className={rows[index]?.state ?? 'pending'}>
                {summaryWord(phase, rows[index]?.state)}
              </b>
            </div>
          ))}
        </div>
        <div className="summary-verdict">
          <span className="label">VERDICT</span>
          <span aria-live="polite">
            {view.verdict
              ? <span className={`verdict-pill large verdict-pill-arrive ${view.verdict}`}>
                  {view.id} {view.verdict}
                </span>
              : <span className="verdict-pill large waiting">PENDING</span>}
          </span>
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

export default memo(ExperimentPanel)
