/**
 * Presentation helpers. Nothing here decides anything - every value passed in
 * was measured by the backend and is only being made readable.
 */

/** A latency, rendered at a precision that does not imply false accuracy. */
export function ms(value?: number): string {
  if (value === undefined || value === null) return '—'
  if (value >= 1000) return `${(value / 1000).toFixed(2)} s`
  if (value >= 100) return `${value.toFixed(0)} ms`
  return `${value.toFixed(1)} ms`
}

/** A ratio against a control the backend computed. */
export function times(value?: number | null): string {
  if (value === undefined || value === null) return '—'
  return value >= 10 ? `${value.toFixed(0)}×` : `${value.toFixed(1)}×`
}

/** Bar height as a percentage of the tallest measurement in the same group. */
export function share(value: number | undefined, max: number): number {
  if (!value || !max || max <= 0) return 0
  return Math.max(1.5, Math.min(100, (value / max) * 100))
}

export function seconds(value?: number): string {
  return value === undefined ? '—' : `${value.toFixed(1)}s`
}
