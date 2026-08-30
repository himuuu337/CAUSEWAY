import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchSystemRisk } from './systemRiskApi'

describe('fetchSystemRisk', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('requests the system risk endpoint and returns the parsed rollup', async () => {
    const risk = { state: 'STABLE', score: 0, services_degraded: 0, services: [] }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(risk) })
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchSystemRisk()

    expect(fetchMock).toHaveBeenCalledWith('/api/prediction/system')
    expect(result).toEqual(risk)
  })

  it('rejects when the backend responds with a non-OK status', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 503, json: () => Promise.resolve({}) })
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchSystemRisk()).rejects.toThrow('503')
  })
})
