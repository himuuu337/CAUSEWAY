import { describe, expect, it } from 'vitest'
import { modelOf, plannerDetail } from './provenance'
import type { Provenance } from './types'

function provenance(overrides: Partial<Provenance>): Provenance {
  return {
    source: 'deterministic', kind: 'deterministic', proposed_by: 'planner',
    used_fallback: false, fallback_reason: '', ...overrides,
  }
}

describe('modelOf', () => {
  it('strips the gemini: prefix', () => {
    expect(modelOf('gemini:gemini-3.6-flash')).toBe('gemini-3.6-flash')
  })
  it('leaves non-gemini sources alone', () => {
    expect(modelOf('deterministic')).toBe('deterministic')
  })
})

describe('plannerDetail', () => {
  it('is never AI when used_fallback is true, even if kind is gemini', () => {
    const detail = plannerDetail(provenance({ kind: 'gemini', source: 'gemini:x', used_fallback: true }))
    expect(detail.isAi).toBe(false)
    expect(detail.label).toBe('Deterministic Fallback')
  })

  it('is AI only for a non-fallback gemini provenance', () => {
    const detail = plannerDetail(provenance({ kind: 'gemini', source: 'gemini:gemini-3.6-flash' }))
    expect(detail.isAi).toBe(true)
    expect(detail.label).toBe('Gemini · gemini-3.6-flash')
  })

  it('labels a plain deterministic provenance as such', () => {
    const detail = plannerDetail(provenance({}))
    expect(detail.isAi).toBe(false)
    expect(detail.label).toBe('Deterministic Planner')
  })
})
