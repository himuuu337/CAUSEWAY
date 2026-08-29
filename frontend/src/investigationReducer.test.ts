import { describe, expect, it } from 'vitest'
import { EMPTY, pipelineOf, reduce } from './investigationReducer'
import type { CausewayEvent } from './types'

function fold(events: CausewayEvent[]) {
  return events.reduce(reduce, EMPTY)
}

describe('reduce', () => {
  it('starts from an idle, empty state', () => {
    expect(EMPTY.runState).toBe('idle')
    expect(EMPTY.hypotheses).toEqual({})
    expect(EMPTY.events).toEqual([])
  })

  it('records every event it is given, unedited, in order', () => {
    const events: CausewayEvent[] = [
      { type: 'stage', stage: 'localization', status: 'running', t: 0 },
      { type: 'stage', stage: 'localization', status: 'done', t: 1 },
    ]
    const state = fold(events)
    expect(state.events).toEqual(events)
  })

  it('files candidates, exclusions, and the deploy count verbatim', () => {
    const state = fold([{
      type: 'candidates',
      candidates: [{
        change_id: 'c1', sha: 'abc123', branch: 'feat/x', service: 'order-service',
        summary: 'x', deployed_at: '2026-01-01', seconds_before_detection: 30,
        files_changed: 2, lines_changed: 10, changed_files: ['a.py'],
      }],
      excluded: [{ change_id: 'c2', branch: 'chore/y', reason: 'different service' }],
      deploys_considered: 5,
    }])
    expect(state.candidates).toHaveLength(1)
    expect(state.candidates[0].change_id).toBe('c1')
    expect(state.excluded[0].reason).toBe('different service')
    expect(state.deploysConsidered).toBe(5)
  })

  it('creates a hypothesis on its first plan event and keeps a stable order', () => {
    const state = fold([
      {
        type: 'plan', hypothesis: 'h2',
        plan: {
          hypothesis_id: 'h2', intervention: { flag: 'f', value: false }, fixture_id: 'fx',
          expected_signature: { metric: 'p95', op: '>', relative_to: 'control', factor: 2 },
          discriminates_between: ['h1', 'h2'], reasoning_summary: 'because',
        },
        validation: { checks: [], passed: 0, total: 0, accepted: true, reasoning_flagged: false },
        provenance: { source: 'deterministic', kind: 'deterministic', proposed_by: 'planner', used_fallback: false, fallback_reason: '' },
      },
      {
        type: 'plan', hypothesis: 'h1',
        plan: {
          hypothesis_id: 'h1', intervention: { flag: 'g', value: true }, fixture_id: 'fx',
          expected_signature: { metric: 'p95', op: '<', relative_to: 'control', factor: 1 },
          discriminates_between: ['h1'], reasoning_summary: 'because too',
        },
        validation: { checks: [], passed: 0, total: 0, accepted: true, reasoning_flagged: false },
        provenance: { source: 'gemini:gemini-3.6-flash', kind: 'gemini', proposed_by: 'planner', used_fallback: false, fallback_reason: '' },
      },
    ])
    // First-seen order is preserved, not sorted.
    expect(state.order).toEqual(['h2', 'h1'])
    expect(state.hypotheses.h1.plan?.reasoning_summary).toBe('because too')
    expect(state.hypotheses.h2.provenance?.kind).toBe('deterministic')
  })

  it('tracks phase rows through start, result, and judgement without touching unrelated phases', () => {
    const state = fold([
      { type: 'experiment_start', hypothesis: 'h1', phases: ['control_a', 'reproduce'], intervention: { flag: 'f', value: false }, holding_fixed: [] },
      { type: 'phase_start', hypothesis: 'h1', phase: 'reproduce', flags: {} },
      { type: 'phase_result', hypothesis: 'h1', phase: 'reproduce', role: 'evidence', p95_ms: 900, p50_ms: 500, reps: 20, error_rate: 0 },
      { type: 'phase_judged', hypothesis: 'h1', phase: 'reproduce', state: 'broken', p95_ms: 900, local_control_ms: 100, ratio: 9, controls_agree: true, drift: 0 },
    ])
    const reproduce = state.hypotheses.h1.phases.find((p) => p.phase === 'reproduce')
    const control = state.hypotheses.h1.phases.find((p) => p.phase === 'control_a')
    expect(reproduce?.p95_ms).toBe(900)
    expect(reproduce?.state).toBe('broken')
    expect(reproduce?.ratio).toBe(9)
    // The untouched phase must remain untouched.
    expect(control?.p95_ms).toBeUndefined()
    expect(control?.running).toBe(false)
  })

  it('never invents a verdict - it only appears after a verdict event', () => {
    const beforeVerdict = fold([
      { type: 'experiment_start', hypothesis: 'h1', phases: ['reproduce'], intervention: { flag: 'f', value: false }, holding_fixed: [] },
    ])
    expect(beforeVerdict.hypotheses.h1.verdict).toBeUndefined()

    const afterVerdict = fold([
      { type: 'experiment_start', hypothesis: 'h1', phases: ['reproduce'], intervention: { flag: 'f', value: false }, holding_fixed: [] },
      { type: 'verdict', hypothesis: 'h1', verdict: 'PROVEN', reason: 'removing it fixed it', detail: {}, phases: [] },
    ])
    expect(afterVerdict.hypotheses.h1.verdict).toBe('PROVEN')
    expect(afterVerdict.hypotheses.h1.reason).toBe('removing it fixed it')
  })

  it('marks a validation rejection as the error kind, but does not overwrite an earlier one', () => {
    const rejected = fold([
      { type: 'validation', hypothesis: 'h1', checks: [{ name: 'x', passed: false, detail: 'no' }], passed: 0, total: 1, accepted: false, reasoning_flagged: false },
    ])
    expect(rejected.errorKind).toBe('validation-rejected')

    const alreadyErrored = fold([
      { type: 'error', message: 'boom' },
      { type: 'validation', hypothesis: 'h1', checks: [], passed: 0, total: 1, accepted: false, reasoning_flagged: false },
    ])
    expect(alreadyErrored.errorKind).toBe('generic')
  })

  it('sets runState and surfaces the error message from an end event', () => {
    const state = fold([{ type: 'end', run_id: 'r1', state: 'failed', error: 'sandbox crashed', event_count: 3, elapsed_s: 1.2 }])
    expect(state.runState).toBe('failed')
    expect(state.error).toBe('sandbox crashed')
    expect(state.errorKind).toBe('run-failed')
  })

  it('leaves state untouched for an unrecognized event type beyond appending it', () => {
    // @ts-expect-error - deliberately testing an event type the union doesn't declare
    const state = reduce(EMPTY, { type: 'mystery' })
    expect(state.events).toHaveLength(1)
    expect(state.hypotheses).toEqual({})
  })
})

describe('pipelineOf', () => {
  it('labels an accepted deterministic plan as the deterministic planner, never a fallback', () => {
    const state = fold([
      { type: 'stage', stage: 'planning', status: 'done', t: 0 },
      {
        type: 'plan', hypothesis: 'h1',
        plan: { hypothesis_id: 'h1', intervention: { flag: 'f', value: false }, fixture_id: 'fx', expected_signature: { metric: 'p95', op: '>', relative_to: 'control', factor: 2 }, discriminates_between: [], reasoning_summary: '' },
        validation: { checks: [], passed: 0, total: 0, accepted: true, reasoning_flagged: false },
        provenance: { source: 'deterministic', kind: 'deterministic', proposed_by: 'planner', used_fallback: false, fallback_reason: '' },
      },
    ])
    const planner = pipelineOf(state).find((s) => s.key === 'planner')
    expect(planner?.detail).toBe('Deterministic Planner')
    expect(planner?.kind).toBe('code')
  })

  it('labels a Gemini plan as Gemini only when it was actually accepted, not a fallback', () => {
    const state = fold([{
      type: 'plan', hypothesis: 'h1',
      plan: { hypothesis_id: 'h1', intervention: { flag: 'f', value: false }, fixture_id: 'fx', expected_signature: { metric: 'p95', op: '>', relative_to: 'control', factor: 2 }, discriminates_between: [], reasoning_summary: '' },
      validation: { checks: [], passed: 0, total: 0, accepted: true, reasoning_flagged: false },
      provenance: { source: 'gemini:gemini-3.6-flash', kind: 'gemini', proposed_by: 'planner', used_fallback: false, fallback_reason: '' },
    }])
    const planner = pipelineOf(state).find((s) => s.key === 'planner')
    expect(planner?.detail).toBe('Gemini · gemini-3.6-flash')
    expect(planner?.kind).toBe('ai')
  })

  it('never labels a fallback plan as Gemini, even when Gemini was the one proposed by', () => {
    const state = fold([{
      type: 'plan', hypothesis: 'h1',
      plan: { hypothesis_id: 'h1', intervention: { flag: 'f', value: false }, fixture_id: 'fx', expected_signature: { metric: 'p95', op: '>', relative_to: 'control', factor: 2 }, discriminates_between: [], reasoning_summary: '' },
      validation: { checks: [], passed: 0, total: 0, accepted: true, reasoning_flagged: false },
      provenance: { source: 'gemini:gemini-3.6-flash', kind: 'gemini', proposed_by: 'gemini', used_fallback: true, fallback_reason: 'invalid json' },
    }])
    const planner = pipelineOf(state).find((s) => s.key === 'planner')
    expect(planner?.detail).toBe('Deterministic Fallback')
    expect(planner?.kind).toBe('code')
  })

  it('marks the verdict stage done only once every candidate has a verdict', () => {
    const withOneOfTwo = fold([
      { type: 'candidates', candidates: [
        { change_id: 'h1', sha: 'a', branch: 'a', service: 's', summary: '', deployed_at: '', seconds_before_detection: 0, files_changed: 0, lines_changed: 0, changed_files: [] },
        { change_id: 'h2', sha: 'b', branch: 'b', service: 's', summary: '', deployed_at: '', seconds_before_detection: 0, files_changed: 0, lines_changed: 0, changed_files: [] },
      ], excluded: [], deploys_considered: 2 },
      { type: 'plan', hypothesis: 'h1', plan: { hypothesis_id: 'h1', intervention: { flag: 'f', value: false }, fixture_id: 'fx', expected_signature: { metric: 'p95', op: '>', relative_to: 'control', factor: 2 }, discriminates_between: [], reasoning_summary: '' }, validation: { checks: [], passed: 0, total: 0, accepted: true, reasoning_flagged: false }, provenance: { source: 'deterministic', kind: 'deterministic', proposed_by: 'planner', used_fallback: false, fallback_reason: '' } },
      { type: 'verdict', hypothesis: 'h1', verdict: 'PROVEN', reason: '', detail: {}, phases: [] },
    ])
    expect(pipelineOf(withOneOfTwo).find((s) => s.key === 'verdict')?.status).toBe('active')
  })
})
