/**
 * The one call this page makes to the backend's own system-risk rollup
 * (causeway/prediction/rollup.py, via GET /api/prediction/system). Mirrors
 * graphApi.ts exactly: buildSystemRisk() already renders instantly from
 * live SSE state, this exists so the page can also show the backend's own
 * computed answer as a cross-check, never as a second source of truth.
 */
import type { SystemRisk } from './types'

export async function fetchSystemRisk(): Promise<SystemRisk> {
  const response = await fetch('/api/prediction/system')
  if (!response.ok) {
    throw new Error(`prediction system endpoint returned HTTP ${response.status}`)
  }
  return (await response.json()) as SystemRisk
}
