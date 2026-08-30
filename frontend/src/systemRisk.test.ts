/**
 * buildSystemRisk is a pure function of MonitorState - every test here
 * constructs that state directly, the same way graph.test.ts exercises
 * buildCausalGraph without running the app.
 */
import { describe, expect, it } from 'vitest'
import { buildSystemRisk, serviceRisk, stateFor, worstState } from './systemRisk'
import type { MonitorState } from './useMonitor'
import type { RiskAssessment } from './types'

function assessment(overrides: Partial<RiskAssessment> = {}): RiskAssessment {
  return {
    service: 'svc', detector: 'd', level: 'LOW', score: 0.5, predicted_failure: 'something',
    evidence: [], current_values: {}, trends: {}, eta_seconds: null, sample_count: 10,
    confirmed: false, ...overrides,
  }
}

function monitorState(risk: MonitorState['risk']): MonitorState {
  return { connection: 'open', latestTelemetry: {}, risk, incidents: [], events: [] }
}

describe('stateFor', () => {
  it('maps LOW to STABLE', () => {
    expect(stateFor(assessment({ level: 'LOW' }))).toBe('STABLE')
  })
  it('maps MEDIUM to WATCH', () => {
    expect(stateFor(assessment({ level: 'MEDIUM' }))).toBe('WATCH')
  })
  it('maps an unconfirmed HIGH to ELEVATED', () => {
    expect(stateFor(assessment({ level: 'HIGH', confirmed: false }))).toBe('ELEVATED')
  })
  it('maps a confirmed HIGH to HIGH_RISK', () => {
    expect(stateFor(assessment({ level: 'HIGH', confirmed: true }))).toBe('HIGH_RISK')
  })
})

describe('worstState', () => {
  it('is INSUFFICIENT_DATA for no assessments', () => {
    expect(worstState([])).toBe('INSUFFICIENT_DATA')
  })
  it('picks the most severe of several assessments', () => {
    const assessments = [
      assessment({ level: 'LOW' }), assessment({ level: 'MEDIUM' }),
      assessment({ level: 'HIGH', confirmed: true }),
    ]
    expect(worstState(assessments)).toBe('HIGH_RISK')
  })
})

describe('serviceRisk', () => {
  it('scales the worst score to 0-100', () => {
    const risk = serviceRisk('svc', [assessment({ score: 0.2 }), assessment({ score: 0.9 })])
    expect(risk.score).toBe(90)
    expect(risk.state).toBe('STABLE')
  })

  it('is INSUFFICIENT_DATA at score 0 with no assessments', () => {
    const risk = serviceRisk('svc', [])
    expect(risk.state).toBe('INSUFFICIENT_DATA')
    expect(risk.score).toBe(0)
  })
})

describe('buildSystemRisk', () => {
  it('is INSUFFICIENT_DATA with no services tracked at all', () => {
    const risk = buildSystemRisk(monitorState({}))
    expect(risk.state).toBe('INSUFFICIENT_DATA')
    expect(risk.services).toHaveLength(0)
    expect(risk.services_degraded).toBe(0)
  })

  it('rolls up the worst state across every tracked service', () => {
    const risk = buildSystemRisk(monitorState({
      a: [assessment({ service: 'a', level: 'LOW' })],
      b: [assessment({ service: 'b', level: 'HIGH', confirmed: true, score: 0.95 })],
    }))
    expect(risk.state).toBe('HIGH_RISK')
    expect(risk.score).toBe(95)
    expect(risk.services_degraded).toBe(1)
    expect(risk.services.map((s) => s.service)).toEqual(['a', 'b'])
  })

  it('never fabricates degradation for a service with no assessments yet', () => {
    const risk = buildSystemRisk(monitorState({
      loud: [assessment({ service: 'loud', level: 'HIGH', confirmed: true })],
      quiet: [],
    }))
    expect(risk.state).toBe('HIGH_RISK')
    expect(risk.services_degraded).toBe(1)
    expect(risk.services.find((s) => s.service === 'quiet')?.state).toBe('INSUFFICIENT_DATA')
  })

  it('is deterministic for the same monitor state', () => {
    const state = monitorState({ a: [assessment({ service: 'a', level: 'MEDIUM' })] })
    expect(buildSystemRisk(state)).toEqual(buildSystemRisk(state))
  })
})
