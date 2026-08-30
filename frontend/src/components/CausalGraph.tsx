import { useEffect, useMemo, useRef, useState } from 'react'
import ReactFlow, {
  Background, Controls, Handle, MarkerType, Position,
  type Edge, type Node, type NodeProps,
} from 'reactflow'
import 'reactflow/dist/style.css'
import type { InvestigationState } from '../useInvestigation'
import type { MonitorState } from '../useMonitor'
import { buildCausalGraph, layoutGraph, type CausalGraph as CausalGraphData, type CausalStrength, type GraphNode } from '../graph'
import { fetchCausalGraph } from '../graphApi'
import GraphDrawer from './GraphDrawer'

interface Props {
  state: InvestigationState
  monitor?: MonitorState
}

const STRENGTH_COLOR: Record<CausalStrength, string> = {
  candidate: 'var(--amber)',
  proven: 'var(--green)',
  refuted: 'var(--red)',
  supported: 'var(--blue)',
  unresolved: 'var(--text-faint)',
  link: 'var(--line-strong)',
}

const NODE_TYPE_LABEL: Record<GraphNode['type'], string> = {
  incident: 'INCIDENT',
  repository: 'REPOSITORY',
  candidate: 'DEPLOYED CHANGE',
  code_change: 'CODE CHANGE',
  experiment: 'EXPERIMENT',
  fix: 'REMEDIATION',
  prediction: 'PREDICTION',
}

function statusColor(status: string): string {
  const s = status.toLowerCase()
  if (s.includes('proven') || s === 'verified' || s === 'healthy' || s === 'recovered') {
    return 'var(--green)'
  }
  if (s.includes('refuted') || s === 'failed' || s === 'broken' || s === 'blocked') {
    return 'var(--red)'
  }
  if (s.includes('supported')) return 'var(--blue)'
  if (s.includes('predicted')) return 'var(--violet)'
  if (s.includes('unresolved') || s.includes('running') || s.includes('deployed')
    || s.includes('testable') || s.includes('proposed') || s.includes('candidate')) {
    return 'var(--amber)'
  }
  return 'var(--text-dim)'
}

function GraphNodeCard({ data }: NodeProps<GraphNode>) {
  const color = statusColor(data.status)
  // The only metadata field a node card shows outside its drawer: a real,
  // deterministic engineering-insight category (see analysis/hypothesis.py),
  // never anything guessed for this one finding.
  const category = data.type === 'code_change' ? (data.metadata.category as string | undefined) : undefined
  return (
    <div className="graph-node" style={{ borderColor: color }}>
      <Handle type="target" position={Position.Top} className="graph-node-handle" />
      <div className="graph-node-type-row">
        <div className="graph-node-type">{NODE_TYPE_LABEL[data.type]}</div>
        {category && category !== 'UNKNOWN' && <div className="graph-node-category">{category}</div>}
      </div>
      <div className="graph-node-label">{data.label}</div>
      {data.description && <div className="graph-node-desc">{data.description}</div>}
      <div className="graph-node-status" style={{ color }}>{data.status}</div>
      <Handle type="source" position={Position.Bottom} className="graph-node-handle" />
    </div>
  )
}

const NODE_TYPES = { causal: GraphNodeCard }

/**
 * The causal graph: a live rendering of InvestigationState (and, when a
 * prediction actually preceded this incident, MonitorState), never a second
 * source of truth. Every node and edge here can be traced back to an event
 * the backend already emitted - see graph.ts.
 */
export default function CausalGraph({ state, monitor }: Props) {
  // Live, instant, entirely client-side - the same InvestigationState every
  // other panel on this page already renders from. Never blank, and never
  // waits on a network round trip.
  const localGraph = useMemo(() => buildCausalGraph(state, monitor), [state, monitor])

  // The backend's own build_graph(), fetched from the identical event
  // buffer. Debounced on the event count so a burst of SSE events (a
  // measured phase lands every few hundred ms) collapses into one request
  // rather than one per event - there is no polling loop here, only a
  // request triggered by state actually having changed.
  const [serverGraph, setServerGraph] = useState<CausalGraphData | null>(null)
  const [serverConfirmed, setServerConfirmed] = useState(false)
  const requestId = useRef(0)

  useEffect(() => {
    setServerGraph(null)
    setServerConfirmed(false)
  }, [state.runId])

  useEffect(() => {
    const runId = state.runId
    if (!runId) return undefined
    const thisRequest = ++requestId.current
    const timer = window.setTimeout(() => {
      fetchCausalGraph(runId)
        .then((fetched) => {
          if (requestId.current !== thisRequest) return // superseded by a newer event
          setServerGraph(fetched)
          setServerConfirmed(true)
        })
        .catch(() => {
          // BACKEND_UNAVAILABLE for this one call: the already-correct local
          // graph keeps rendering, silently, exactly as it was before this
          // request was ever made.
        })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [state.runId, state.events.length])

  const graph = serverGraph ?? localGraph
  const { nodes: positioned, edges } = useMemo(() => layoutGraph(graph), [graph])
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const selected = selectedId ? graph.nodes.find((node) => node.id === selectedId) ?? null : null

  const rfNodes: Node[] = positioned.map((node) => ({
    id: node.id,
    type: 'causal',
    position: { x: node.x, y: node.y },
    data: node,
    draggable: false,
    selectable: true,
  }))

  const rfEdges: Edge[] = edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label || undefined,
    labelStyle: { fill: STRENGTH_COLOR[edge.strength], fontSize: 11 },
    labelBgStyle: { fill: 'var(--bg)' },
    style: {
      stroke: STRENGTH_COLOR[edge.strength],
      strokeDasharray: edge.strength === 'candidate' || edge.strength === 'unresolved' ? '4 3' : undefined,
    },
    markerEnd: { type: MarkerType.ArrowClosed, color: STRENGTH_COLOR[edge.strength] },
  }))

  return (
    <section className="card graph-card">
      <div className="card-head">
        <h2 className="card-title">Causal graph</h2>
        <span className="card-note">
          what happened, what was suspected, what was tested, what was verified
        </span>
        <div className="spacer" />
        <span
          className={`graph-source-tag${serverConfirmed ? ' confirmed' : ''}`}
          title={serverConfirmed
            ? 'Matches causeway/graph.py, built server-side from this run’s own event buffer'
            : 'Rendering the live client-side view while the backend’s own graph is confirmed'}
        >
          {serverConfirmed ? 'BACKEND-VERIFIED' : 'LIVE'}
        </span>
      </div>

      {graph.nodes.length === 0 ? (
        <p className="small faint graph-empty">
          Causeway could not construct the causal graph from the available
          investigation evidence.
        </p>
      ) : (
        <div className="graph-canvas">
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={NODE_TYPES}
            onNodeClick={(_event, node) => setSelectedId(node.id)}
            onPaneClick={() => setSelectedId(null)}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            proOptions={{ hideAttribution: true }}
            nodesConnectable={false}
            nodesDraggable={false}
            panOnScroll
            zoomOnScroll={false}
            zoomOnPinch
          >
            <Background gap={20} color="var(--line)" />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      )}

      {selected && <GraphDrawer node={selected} onClose={() => setSelectedId(null)} />}
    </section>
  )
}
