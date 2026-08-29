/**
 * One investigation, followed over Server-Sent Events.
 *
 * This file owns the network lifecycle only - opening the stream, retrying
 * it within a bound, and starting a run. All state shaping lives in
 * investigationReducer.ts, which has no knowledge that a network exists.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { InvestigationState } from './investigationReducer'
import { EMPTY, pipelineOf, reduce } from './investigationReducer'
import type { CausewayEvent, Health } from './types'

export type { Connection, HypothesisView, PhaseRow, PipelineStage } from './investigationReducer'
export { modelOf } from './provenance'

/** EventSource retries forever on its own; past this many failures in a row
 *  without a single successful open, stop and let the person retry by hand
 *  rather than spinning silently against a backend that is actually down. */
const MAX_CONSECUTIVE_FAILURES = 6

export function useInvestigation() {
  const [state, setState] = useState<InvestigationState>(EMPTY)
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState(false)
  const [starting, setStarting] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)
  const failuresRef = useRef(0)
  const lastRunIdRef = useRef<string | null>(null)

  const closeStream = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
  }, [])

  const attach = useCallback((runId: string) => {
    closeStream()
    failuresRef.current = 0
    lastRunIdRef.current = runId
    setState({ ...EMPTY, runId, runState: 'running', connection: 'connecting' })

    const source = new EventSource(
      `/api/investigation/stream?run_id=${encodeURIComponent(runId)}`)
    sourceRef.current = source

    source.onopen = () => {
      failuresRef.current = 0
      setState((previous) => ({ ...previous, connection: 'open' }))
    }

    source.onmessage = (message) => {
      let event: CausewayEvent
      try {
        event = JSON.parse(message.data) as CausewayEvent
      } catch {
        if (import.meta.env.DEV) {
          console.warn('causeway: dropped a malformed SSE payload', message.data)
        }
        return
      }
      setState((previous) => reduce(previous, event))
      if (event.type === 'end') {
        // The run is over. EventSource reconnects on any close, including a
        // clean one, so the client has to be the thing that stops.
        closeStream()
        setState((previous) => ({ ...previous, connection: 'closed' }))
      }
    }

    source.onerror = () => {
      failuresRef.current += 1
      const gaveUp = failuresRef.current >= MAX_CONSECUTIVE_FAILURES
      if (gaveUp) closeStream()

      setState((previous) => {
        if (previous.runState !== 'running') return previous
        if (gaveUp) {
          return {
            ...previous,
            connection: 'closed',
            error: 'lost the connection to the backend and stopped retrying',
            errorKind: 'backend-unreachable',
          }
        }
        return {
          ...previous,
          connection: source.readyState === EventSource.CLOSED ? 'closed' : 'reconnecting',
        }
      })
    }
  }, [closeStream])

  const retry = useCallback(() => {
    if (lastRunIdRef.current) attach(lastRunIdRef.current)
  }, [attach])

  const start = useCallback(async () => {
    setStarting(true)
    try {
      const response = await fetch('/api/investigation', { method: 'POST' })
      const body = await response.json().catch(() => ({}))

      // 409 means somebody (or another tab) already started one - follow it
      // rather than refusing.
      if (response.status === 202 || response.status === 409) {
        attach(body.run_id)
        return
      }
      const detail = body?.detail ?? body
      setState((previous) => ({
        ...previous,
        error: detail?.message
          ? `${detail.message}${detail.hint ? ` — ${detail.hint}` : ''}`
          : `the backend refused to start an investigation (HTTP ${response.status})`,
        errorKind: 'generic',
      }))
    } catch (problem) {
      setState((previous) => ({
        ...previous,
        error: `cannot reach the backend: ${String(problem)}`,
        errorKind: 'backend-unreachable',
      }))
    } finally {
      setStarting(false)
    }
  }, [attach])

  // On load: is the backend up, and is an investigation already in flight?
  useEffect(() => {
    let cancelled = false
    fetch('/api/health')
      .then((response) => response.json())
      .then((body: Health) => { if (!cancelled) setHealth(body) })
      .catch(() => {
        if (!cancelled) {
          setHealthError(true)
          setState((previous) => ({
            ...previous,
            error: 'cannot reach the backend on /api/health',
            errorKind: 'backend-unreachable',
          }))
        }
      })
    fetch('/api/status')
      .then((response) => response.json())
      .then((body) => {
        if (!cancelled && body?.state === 'running' && body.run_id) attach(body.run_id)
      })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [attach])

  useEffect(() => closeStream, [closeStream])

  const pipeline = useMemo(() => pipelineOf(state), [state])
  const busy = starting || state.runState === 'running'
  /** Neither health nor an error has arrived yet - the very first paint. */
  const loadingHealth = health === null && !healthError && state.error === null

  return { state, health, loadingHealth, busy, starting, start, retry, pipeline }
}
