/**
 * One rule, shared everywhere provenance gets a label.
 *
 * Never call the configured planner a fallback, and never call anything
 * Gemini unless the backend reported `kind: "gemini"`. Claiming AI designed
 * an experiment that a deterministic planner designed would be the one
 * dishonest thing this interface could do - so this rule lives in exactly
 * one place and everything else imports it.
 */
import type { Provenance } from './types'

/** `gemini:gemini-3.6-flash` reads better as just the model. */
export function modelOf(source: string): string {
  return source.startsWith('gemini:') ? source.slice('gemini:'.length) : source
}

export type PlannerCategory = 'fallback' | 'gemini' | 'deterministic'

export function plannerCategory(provenance: Provenance): PlannerCategory {
  if (provenance.used_fallback) return 'fallback'
  if (provenance.kind === 'gemini') return 'gemini'
  return 'deterministic'
}

export interface PlannerDetail {
  category: PlannerCategory
  label: string
  /** True only when an actual, non-fallback Gemini plan was accepted. */
  isAi: boolean
}

export function plannerDetail(provenance: Provenance): PlannerDetail {
  const category = plannerCategory(provenance)
  switch (category) {
    case 'fallback':
      return { category, label: 'Deterministic Fallback', isAi: false }
    case 'gemini':
      return { category, label: `Gemini · ${modelOf(provenance.source)}`, isAi: true }
    case 'deterministic':
      return { category, label: 'Deterministic Planner', isAi: false }
  }
}

export function plannerTagClass(provenance: Provenance): string {
  const category = plannerCategory(provenance)
  if (category === 'fallback') return 'planner-tag fallback'
  if (category === 'gemini') return 'planner-tag ai'
  return 'planner-tag'
}
