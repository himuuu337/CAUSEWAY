import type { RepositoryView } from '../useInvestigation'

interface Props { view: RepositoryView }

const STATUS_WORD: Record<string, string> = {
  validating: 'VALIDATING URL', cloning: 'CLONING…',
  loaded: 'SUPPORTED', rejected: 'UNSUPPORTED',
}

/**
 * The repository lifecycle, rendered from repository_* events only.
 *
 * Every field here arrived on repository_loaded exactly as the backend's
 * manifest validator accepted it, or as the database builder reported after
 * building it. There is no candidate list: a manifest is forbidden from
 * declaring suspects, and the ones on screen were read out of the source by
 * the detectors instead. Nothing is guessed while a clone is still in
 * flight, and a rejected repository never pretends to have loaded.
 */
function bytesOf(count: number): string {
  if (count >= 1024 * 1024) return `${(count / (1024 * 1024)).toFixed(1)} MB`
  if (count >= 1024) return `${Math.round(count / 1024)} KB`
  return `${count} B`
}

export default function RepositoryPanel({ view }: Props) {
  return (
    <section className="card repo-card">
      <div className="card-head">
        <h2 className="card-title">Repository</h2>
        <span className="card-note mono">{view.url}</span>
        <div className="spacer" />
        <span className={`verdict-pill repo-${view.status}`}>
          {STATUS_WORD[view.status] ?? view.status}
        </span>
      </div>

      {view.status === 'rejected' ? (
        <div className="notice repo-rejected">
          <div className="repo-rejected-title">UNSUPPORTED REPOSITORY</div>
          <div>This repository does not contain a supported Causeway demo configuration.</div>
          {view.rejection && (
            <div className="small faint" style={{ marginTop: 6 }}>
              {view.rejection.stage}: {view.rejection.reason}
            </div>
          )}
        </div>
      ) : (
        <div className="repo-meta">
          <div className="repo-meta-row">
            <span className="k">REPOSITORY</span>
            <span className="v mono">
              {view.owner && view.name ? `${view.owner}/${view.name}` : '—'}
            </span>
          </div>
          {view.commitSha && (
            <div className="repo-meta-row">
              <span className="k">COMMIT</span>
              <span className="v mono">{view.commitSha.slice(0, 12)}</span>
            </div>
          )}
          {view.service && (
            <div className="repo-meta-row">
              <span className="k">SERVICE</span>
              <span className="v">{view.service} · {view.runtime}</span>
            </div>
          )}
          {view.entrypoint && (
            <div className="repo-meta-row">
              <span className="k">RUNS</span>
              <span className="v mono">python {view.entrypoint}</span>
            </div>
          )}
          {view.sources.length > 0 && (
            <div className="repo-meta-row">
              <span className="k">ANALYSED</span>
              <span className="v mono">{view.sources.join(', ')}</span>
            </div>
          )}
          {view.patchable.length > 0 && (
            <div className="repo-meta-row">
              <span className="k">PATCHABLE</span>
              <span className="v mono">{view.patchable.join(', ')}</span>
            </div>
          )}
          {view.database && (
            <div className="repo-meta-row">
              <span className="k">DATABASE</span>
              <span className="v mono">
                {view.database.engine} · {bytesOf(view.database.bytes)} ·{' '}
                {Object.entries(view.database.tables)
                  .map(([table, rows]) => `${table} ${rows.toLocaleString()} rows`)
                  .join(' · ')}
              </span>
            </div>
          )}
          {view.workload && (
            <div className="repo-meta-row">
              <span className="k">WORKLOAD</span>
              <span className="v mono">
                {view.workload.id} · {view.workload.requests} requests ·
                {' '}concurrency {view.workload.concurrency}
              </span>
            </div>
          )}
          {view.status === 'loaded' && (
            <div className="repo-meta-row">
              <span className="k good">&#10003;</span>
              <span className="v good">
                Built from this repository&apos;s own schema and seed — Causeway&apos;s
                bundled fixture is not used on this path
              </span>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
