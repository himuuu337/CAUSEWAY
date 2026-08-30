import type { Plan, Provenance, Validation } from '../types'
import type { GraphNode } from '../graph'
import { ms, times } from '../format'

interface Props {
  node: GraphNode
  onClose: () => void
}

/** Only the metadata shapes graph.ts actually attaches per node type - kept
 * local so a typo here fails the build instead of rendering `undefined`. */
interface IncidentMeta { id: string; service: string; detectedAt: string }
interface RepositoryMeta {
  url: string; commitSha?: string; runtime?: string
  primaryLanguage?: string; entrypoint?: string; sources: string[]
}
interface CandidateMeta {
  changeId: string; sha: string; branch: string; service: string
  deployedAt: string; secondsBeforeDetection: number
  filesChanged: number; linesChanged: number; changedFiles: string[]
}
interface CodeChangeMeta {
  file: string; line: number; lineEnd?: number; symbol: string; kind: string
  category?: string; observed: string; counterfactual: string | null
  evidence: string; reason: string; detector: string
}
interface ExperimentPhase {
  phase: string; role: 'control' | 'evidence'; p95_ms?: number
  state?: string; ratio?: number | null; drift?: number
}
interface ExperimentMeta {
  phases: ExperimentPhase[]
  plan: Plan | null
  provenance: Provenance | null
  validation: Validation | null
}
interface FixMeta {
  file: string | null; diff: string | null
  operation: { type: string; target: string; before: string; after: string } | null
  reason: string | null
  provenance: Provenance | null
  blocked: { scope: 'intent' | 'repository'; reason: string } | null
}
interface PredictionMeta {
  service: string; detector: string; riskScore: number; evidence: string[]
  telemetryWindow: { current_values: Record<string, number>; trends: Record<string, number>
    eta_seconds: number | null; sample_count: number }
  createdAt: number
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="drawer-section">
      <div className="drawer-section-title">{title}</div>
      {children}
    </div>
  )
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === undefined || value === null || value === '') return null
  return (
    <div className="drawer-field">
      <div className="drawer-field-label">{label}</div>
      <div className="drawer-field-value">{value}</div>
    </div>
  )
}

function IncidentBody({ meta }: { meta: IncidentMeta }) {
  return (
    <Section title="OBSERVED FACTS">
      <Field label="Incident ID" value={<span className="mono">{meta.id}</span>} />
      <Field label="Service" value={meta.service} />
      <Field label="Detected at" value={meta.detectedAt} />
    </Section>
  )
}

function RepositoryBody({ meta }: { meta: RepositoryMeta }) {
  return (
    <Section title="OBSERVED FACTS">
      <Field label="Repository" value={<span className="mono">{meta.url}</span>} />
      <Field label="Commit" value={meta.commitSha && <span className="mono">{meta.commitSha}</span>} />
      <Field label="Runtime" value={meta.runtime} />
      <Field label="Primary language" value={meta.primaryLanguage} />
      <Field label="Entrypoint" value={meta.entrypoint && <span className="mono">{meta.entrypoint}</span>} />
      <Field label="Source files" value={meta.sources.length > 0 ? meta.sources.join(', ') : undefined} />
      <div className="drawer-actions">
        <a className="drawer-action" href={meta.url} target="_blank" rel="noreferrer">View Repository</a>
      </div>
    </Section>
  )
}

function CandidateBody({ meta }: { meta: CandidateMeta }) {
  return (
    <Section title="OBSERVED FACTS">
      <Field label="Change" value={<span className="mono">{meta.changeId}</span>} />
      <Field label="Commit" value={<span className="mono">{meta.sha.slice(0, 12)}</span>} />
      <Field label="Branch" value={meta.branch} />
      <Field label="Service" value={meta.service} />
      <Field label="Deployed at" value={meta.deployedAt} />
      <Field label="Before detection" value={`${meta.secondsBeforeDetection}s`} />
      <Field label="Diff size" value={`${meta.filesChanged} files, ${meta.linesChanged} lines`} />
      {meta.changedFiles.length > 0 && (
        <Field
          label="Changed files"
          value={<ul className="drawer-list">{meta.changedFiles.map((f) => <li key={f} className="mono">{f}</li>)}</ul>}
        />
      )}
      <p className="small faint drawer-note">
        Relevant deployment identified. This is the bundled demonstration's fabricated
        deploy record — no line-level source location applies to it.
      </p>
    </Section>
  )
}

function CodeChangeBody({ meta }: { meta: CodeChangeMeta }) {
  // meta.reason is a deterministic detector's own rationale - regex/AST
  // output, not a language model's - so it belongs under OBSERVED FACTS
  // with everything else this same detector reported, never under an
  // "AI INTERPRETATION" heading nothing here earned. The only genuine AI
  // interpretation for a hypothesis is the planner's reasoning_summary,
  // shown in ExperimentBody once one exists.
  const location = meta.lineEnd && meta.lineEnd !== meta.line
    ? `${meta.line}-${meta.lineEnd}` : `${meta.line}`
  return (
    <Section title="OBSERVED FACTS">
      {meta.category && <Field label="Engineering insight" value={<span className="mono">{meta.category}</span>} />}
      <Field label="File" value={<span className="mono">{meta.file}:{location}</span>} />
      <Field label="Symbol" value={<span className="mono">{meta.symbol}</span>} />
      <Field label="Kind" value={meta.kind} />
      <Field label="Detected by" value={<span className="mono">{meta.detector}</span>} />
      <Field label="Found" value={<code className="hyp-observed">{meta.observed}</code>} />
      {meta.counterfactual && (
        <Field label="Would test" value={<code className="hyp-counterfactual">{meta.counterfactual}</code>} />
      )}
      <Field label="Evidence" value={meta.evidence} />
      <p className="small faint drawer-note">{meta.reason}</p>
      <p className="small faint drawer-note">
        Found by static analysis, not by measurement — being on this graph is not
        evidence of anything. Only the experiment below can say what it costs.
      </p>
    </Section>
  )
}

const PHASE_LABEL: Record<string, string> = {
  'control-1': 'Control 1', reproduce: 'Reproduce', 'control-2': 'Control 2',
  ablate: 'Ablate', 'control-3': 'Control 3', restore: 'Restore', 'control-4': 'Control 4',
  'fix-control-1': 'Fix control 1', 'fix-before': 'Before fix',
  'fix-control-2': 'Fix control 2', 'fix-after': 'After fix', 'fix-control-3': 'Fix control 3',
}

function ExperimentBody({ meta }: { meta: ExperimentMeta }) {
  return (
    <>
      <Section title="OBSERVED FACTS">
        {meta.phases.length === 0 ? (
          <p className="small faint">No phases have been measured yet.</p>
        ) : (
          <ul className="drawer-list">
            {meta.phases.map((phase) => (
              <li key={phase.phase} className="mono small">
                {PHASE_LABEL[phase.phase] ?? phase.phase}: {ms(phase.p95_ms)}
                {phase.ratio != null ? ` (${times(phase.ratio)} local control)` : ''}
                {phase.state ? ` — ${phase.state}` : ''}
              </li>
            ))}
          </ul>
        )}
        {meta.validation && (
          <Field
            label="Deterministic validator"
            value={`${meta.validation.passed} / ${meta.validation.total} checks passed`}
          />
        )}
      </Section>
      {meta.plan?.reasoning_summary && (
        <Section title="AI INTERPRETATION">
          <p className="small faint drawer-note">
            {meta.provenance?.kind === 'gemini' ? 'Gemini' : 'Deterministic planner'}: {meta.plan.reasoning_summary}
          </p>
          <p className="small faint drawer-note">
            Presentation only — the planner never sees a measurement, and this prose
            is never read by the verdict engine.
          </p>
        </Section>
      )}
    </>
  )
}

function FixBody({ meta }: { meta: FixMeta }) {
  if (meta.blocked) {
    return (
      <Section title="OBSERVED FACTS">
        <p className="small faint drawer-note">No fix was applied. {meta.blocked.reason}</p>
      </Section>
    )
  }
  return (
    <>
      <Section title="AI-PROPOSED PATCH">
        {meta.operation && (
          <div className="patch-review">
            <div className="patch-label">{meta.operation.target}</div>
            <div className="patch-lines">
              <div className="patch-line before">- {meta.operation.before}</div>
              <div className="patch-line after">+ {meta.operation.after}</div>
            </div>
          </div>
        )}
        {meta.diff && (
          <pre className="diff drawer-diff">
            {meta.diff.split('\n').map((line, index) => (
              <div
                key={index}
                className={
                  line.startsWith('+') && !line.startsWith('+++') ? 'diff-add'
                    : line.startsWith('-') && !line.startsWith('---') ? 'diff-del'
                      : line.startsWith('@@') ? 'diff-hunk' : 'diff-ctx'
                }
              >
                {line}
              </div>
            ))}
          </pre>
        )}
      </Section>
      {meta.reason && (
        <Section title="VERIFIED CONCLUSION">
          <p className="small faint drawer-note">{meta.reason}</p>
        </Section>
      )}
    </>
  )
}

function PredictionBody({ meta }: { meta: PredictionMeta }) {
  return (
    <Section title="OBSERVED FACTS">
      <Field label="Service" value={meta.service} />
      <Field label="Detector" value={<span className="mono">{meta.detector}</span>} />
      <Field label="Risk score" value={`${Math.round(meta.riskScore)} / 100`} />
      <Field
        label="Projected threshold crossing"
        value={meta.telemetryWindow.eta_seconds != null
          ? `~${Math.round(meta.telemetryWindow.eta_seconds / 60)} min`
          : 'cannot be reliably estimated from current telemetry'}
      />
      {meta.evidence.length > 0 && (
        <Field label="Evidence" value={<ul className="drawer-list">{meta.evidence.map((e) => <li key={e} className="small">{e}</li>)}</ul>} />
      )}
      <p className="small faint drawer-note">
        A deterministic detector's assessment — not an AI judgement, and not a claim
        that an incident will happen.
      </p>
    </Section>
  )
}

export default function GraphDrawer({ node, onClose }: Props) {
  return (
    <aside className="graph-drawer" role="dialog" aria-label={`${node.label} details`}>
      <div className="graph-drawer-head">
        <div>
          <div className="drawer-type">{node.type.replace('_', ' ')}</div>
          <h3 className="drawer-title">{node.label}</h3>
        </div>
        <button className="drawer-close" onClick={onClose} aria-label="Close">&times;</button>
      </div>
      <div className={`verdict-pill drawer-status-pill ${node.status.replace(/\s+/g, '-')}`}>
        {node.status.toUpperCase()}
      </div>

      {node.type === 'incident' && <IncidentBody meta={node.metadata as unknown as IncidentMeta} />}
      {node.type === 'repository' && <RepositoryBody meta={node.metadata as unknown as RepositoryMeta} />}
      {node.type === 'candidate' && <CandidateBody meta={node.metadata as unknown as CandidateMeta} />}
      {node.type === 'code_change' && <CodeChangeBody meta={node.metadata as unknown as CodeChangeMeta} />}
      {node.type === 'experiment' && <ExperimentBody meta={node.metadata as unknown as ExperimentMeta} />}
      {node.type === 'fix' && <FixBody meta={node.metadata as unknown as FixMeta} />}
      {node.type === 'prediction' && <PredictionBody meta={node.metadata as unknown as PredictionMeta} />}
    </aside>
  )
}
