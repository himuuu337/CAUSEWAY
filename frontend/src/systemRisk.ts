/**
 * The system-wide risk rollup, computed entirely client-side from
 * MonitorState - the same live state MonitorPanel already renders from
 * `/api/monitor/stream`. This is the exact mirror of
 * causeway/prediction/rollup.py: given the same assessments, the same
 * rollup, always. Nothing here detects anything, scores anything or
 * decides confirmation - every RiskAssessment it reads already arrived
 * decided on a `risk_updated` event.
 */
import type { MonitorState } from './useMonitor'
import type { PredictionState, RiskAssessment, ServiceRisk, SystemRisk } from './types'

const RANK: Record<PredictionState, number> = {
  INSUFFICIENT_DATA: -1, STABLE: 0, WATCH: 1, ELEVATED: 2, HIGH_RISK: 3,
}

export function stateFor(assessment: RiskAssessment): PredictionState {
  if (assessment.level === 'LOW') return 'STABLE'
  if (assessment.level === 'MEDIUM') return 'WATCH'
  return assessment.confirmed ? 'HIGH_RISK' : 'ELEVATED'
}

export function worstState(assessments: RiskAssessment[]): PredictionState {
  if (assessments.length === 0) return 'INSUFFICIENT_DATA'
  return assessments.reduce<PredictionState>(
    (worst, a) => (RANK[stateFor(a)] > RANK[worst] ? stateFor(a) : worst),
    'INSUFFICIENT_DATA')
}

export function serviceRisk(service: string, assessments: RiskAssessment[]): ServiceRisk {
  const score = assessments.length === 0
    ? 0 : Math.round(Math.max(...assessments.map((a) => a.score)) * 1000) / 10
  return { service, state: worstState(assessments), score, assessments }
}

/** Built from `monitor.risk` - the exact same live, SSE-derived record
 * MonitorPanel already reads. No fetch, no latency, never blank. */
export function buildSystemRisk(monitor: MonitorState): SystemRisk {
  const names = Object.keys(monitor.risk).sort()
  const services = names.map((name) => serviceRisk(name, monitor.risk[name] ?? []))
  const servicesDegraded = services.filter((s) =>
    s.state === 'WATCH' || s.state === 'ELEVATED' || s.state === 'HIGH_RISK').length
  const allAssessments = services.flatMap((s) => s.assessments)
  const score = services.length === 0 ? 0 : Math.max(...services.map((s) => s.score))
  return {
    state: worstState(allAssessments),
    score: Math.round(score * 10) / 10,
    services_degraded: servicesDegraded,
    services,
  }
}
