/**
 * The one call this page makes to the backend's own causal-graph builder
 * (causeway/graph.py, via GET /api/investigation/{run_id}/graph). Nothing
 * here computes a graph - CausalGraph.tsx already has buildCausalGraph()
 * for that, entirely client-side and live on every SSE event. This exists
 * so the same page can also show the backend's own deterministic answer,
 * built from the identical event buffer, as spec compliance and as a
 * cross-check - never as a second source of truth to reconcile.
 */
import type { CausalGraph } from './graph'

export async function fetchCausalGraph(runId: string): Promise<CausalGraph> {
  const response = await fetch(`/api/investigation/${encodeURIComponent(runId)}/graph`)
  if (!response.ok) {
    throw new Error(`graph endpoint returned HTTP ${response.status}`)
  }
  return (await response.json()) as CausalGraph
}
