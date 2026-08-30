import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchCausalGraph } from './graphApi'

describe('fetchCausalGraph', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('requests the run-scoped graph endpoint and returns the parsed graph', async () => {
    const graph = { nodes: [{ id: 'incident' }], edges: [] }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(graph),
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchCausalGraph('run-1')

    expect(fetchMock).toHaveBeenCalledWith('/api/investigation/run-1/graph')
    expect(result).toEqual(graph)
  })

  it('URL-encodes the run id', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ nodes: [], edges: [] }) })
    vi.stubGlobal('fetch', fetchMock)

    await fetchCausalGraph('run/with space')

    expect(fetchMock).toHaveBeenCalledWith('/api/investigation/run%2Fwith%20space/graph')
  })

  it('rejects when the backend responds with a non-OK status', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 404, json: () => Promise.resolve({}) })
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchCausalGraph('missing-run')).rejects.toThrow('404')
  })
})
