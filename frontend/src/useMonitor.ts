/**
 * The live production-monitoring feed, followed over its own Server-Sent
 * Events connection - independent of any one investigation.
 *
 * Exactly the same discipline as useInvestigation: this hook folds events
 * into state and computes nothing. A risk level appears on screen because
 * causeway.prediction emitted `risk_updated`, never because the browser
 * decided a number looked dangerous.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Incident, MonitorEvent, RiskAssessment } from './types'

export type MonitorConnection = 'closed' | 'connecting' | 'open' | 'reconnecting'

export interface MonitorState {
  connection: MonitorConnection
  latestTelemetry: Record<string, Record<string, number | string>>
  risk: Record<string, RiskAssessment[]>
  incidents: Incident[]
  events: MonitorEvent[]
}

const EMPTY: MonitorState = {
  connection: 'closed',
  latestTelemetry: {},
  risk: {},
  incidents: [],
  events: [],
}

function reduce(state: MonitorState, event: MonitorEvent): MonitorState {
  const next: MonitorState = { ...state, events: [...state.events, event].slice(-500) }

  switch (event.type) {
    case 'telemetry_received': {
      const { type: _t, service, timestamp: _ts, t: _tt, ...values } = event
      next.latestTelemetry = { ...state.latestTelemetry, [service]: values }
      return next
    }
    case 'risk_updated': {
      const perService = state.risk[event.service] ?? []
      const withoutThisDetector = perService.filter((r) => r.detector !== event.detector)
      next.risk = { ...state.risk, [event.service]: [...withoutThisDetector, event] }
      return next
    }
    case 'incident_created':
      next.incidents = [...state.incidents, event]
      return next
    case 'investigation_handoff':
      next.incidents = state.incidents.map((incident) =>
        incident.incident_id === event.incident_id
          ? { ...incident, status: event.status, run_id: event.run_id }
          : incident)
      return next
    default:
      return next
  }
}

export function useMonitor() {
  const [state, setState] = useState<MonitorState>(EMPTY)
  const sourceRef = useRef<EventSource | null>(null)

  const closeStream = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
  }, [])

  const connect = useCallback(() => {
    closeStream()
    setState((previous) => ({ ...previous, connection: 'connecting' }))

    const source = new EventSource('/api/monitor/stream')
    sourceRef.current = source

    source.onopen = () => setState((previous) => ({ ...previous, connection: 'open' }))

    source.onmessage = (message) => {
      let event: MonitorEvent
      try {
        event = JSON.parse(message.data) as MonitorEvent
      } catch {
        return
      }
      setState((previous) => reduce(previous, event))
    }

    source.onerror = () => {
      setState((previous) => ({
        ...previous,
        connection: source.readyState === EventSource.CLOSED ? 'closed' : 'reconnecting',
      }))
    }
  }, [closeStream])

  useEffect(() => closeStream, [closeStream])

  return { state, connect, disconnect: closeStream }
}
