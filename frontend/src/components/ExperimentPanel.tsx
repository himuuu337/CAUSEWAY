import { Fragment } from 'react'
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
export default function ExperimentPanel({ view, candidate, active }: Props) {
  if (!view.started) return null

  // What to call this hypothesis on screen. A bundled candidate is A or B; a
  // repository hypothesis is a file and a line, which is both shorter than
  // its full identifier and more useful to a human reading the panel.
  const short = view.code ? `${view.code.file}:${view.code.line}` : view.id
  const subtitle = view.code ? `${view.code.symbol}()` : candidate?.branch

  const rows = EVIDENCE.map((phase) => rowOf(view, phase))
  const values = rows
    .map((row) => row?.p95_ms)
    .filter((value): value is number => value !== undefined)
  const max = values.length > 0 ? Math.max(...values) : 0

  return (
    <section className="card exp">
      <div className="exp-head">
        <span className="cand-id mono">{short}</span>
        <div>
          <div className="exp-name">Controlled experiment</div>
          {subtitle && <div className="exp-branch mono">{subtitle}</div>}
        </div>
        <div className="spacer" />
        {view.verdict
          ? <span className={`verdict-pill ${view.verdict}`}>{view.verdict}</span>
          : <span className="verdict-pill waiting">
              {active ? 'MEASURING…' : 'AWAITING MEASUREMENT'}
            </span>}
      </div>

      {view.code && (
        <div className="exp-edit">
          <span className="exp-edit-label">THE INTERVENTION IS AN EDIT</span>
          <code className="hyp-observed">{view.code.observed}</code>
          <span className="arrow">&rarr;</span>
          <code className="hyp-counterfactual">{view.code.counterfactual}</code>
          <span className="small faint">
            applied to a disposable copy of the repository, one phase at a time
          </span>
        </div>
      )}

      <div className="stages">
        {EVIDENCE.map((phase, index) => {
          const row = rows[index]
          const state = row?.state ?? 'pending'
          const width = share(row?.p95_ms, max)
          return (
            <Fragment key={phase}>
              {index > 0 && <div className="stage-sep" aria-hidden="true">&rarr;</div>}
              <div className={`stage ${state}`}>
                <div className="stage-name mono">{short} {STAGE_WORD[phase]}</div>

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
              {short} {STAGE_WORD[phase]}
              <span className="arrow">&rarr;</span>
              <b className={rows[index]?.state ?? 'pending'}>
                {summaryWord(phase, rows[index]?.state)}
              </b>
            </div>
          ))}
        </div>
        <div className="summary-verdict">
          <span className="label">VERDICT</span>
          {view.verdict
            ? <span className={`verdict-pill large ${view.verdict}`}>
                {short} {view.verdict}
              </span>
            : <span className="verdict-pill large waiting">PENDING</span>}
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
              {view.code && <th>Source as measured</th>}
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
                {view.code && (
                  <td className="mono small">
                    {row.applied === undefined
                      ? ''
                      : row.applied.length === 0
                        ? 'as cloned, unmodified'
                        : row.applied
                            .map((edit) => `${edit.file}:${edit.line} ${edit.after}`)
                            .join(' · ')}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  )
}
