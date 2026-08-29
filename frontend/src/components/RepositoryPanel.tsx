import type { RepositoryView } from '../useInvestigation'

interface Props { view: RepositoryView }

const STATUS_WORD: Record<string, string> = {
  validating: 'VALIDATING URL', cloning: 'CLONING…',
  loaded: 'SUPPORTED', rejected: 'UNSUPPORTED',
}

/**
 * The repository lifecycle, rendered from repository_* events only. Every
 * field here - owner, name, commit, service, runtime, the candidate list -
 * arrived on repository_loaded exactly as the backend's manifest validator
 * accepted it. Nothing is guessed while a clone is still in flight, and a
 * rejected repository never pretends to have loaded.
 */
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
          {view.status === 'loaded' && (
            <div className="repo-meta-row">
              <span className="k good">&#10003;</span>
              <span className="v good">Supported Causeway project</span>
            </div>
          )}
        </div>
      )}

      {view.candidates.length > 0 && (
        <div className="repo-candidates">
          {view.candidates.map((c) => (
            <div key={c.change_id} className="repo-candidate">
              <span className="cand-id small">{c.change_id}</span>
              <span className="repo-candidate-branch mono">{c.branch}</span>
              <span className="small faint">{c.summary}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
