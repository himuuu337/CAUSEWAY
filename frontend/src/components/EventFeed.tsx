import { memo, useMemo } from 'react'
import type { CausewayEvent } from '../types'

interface Props { events: CausewayEvent[] }

const MAX_RENDERED = 500
function EventFeed({ events }: Props) {
  const windowed = events.length > MAX_RENDERED ? events.slice(-MAX_RENDERED) : events
  const offset = events.length - windowed.length

  const text = useMemo(
    () => windowed
      .map((event, index) => `${String(index + offset).padStart(4, ' ')}  ${JSON.stringify(event)}`)
      .join('\n'),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [events.length],
  )

  if (events.length === 0) return null
  return (
    <section className="card feed">
      <details>
        <summary>
          Raw investigation events — {events.length} received
          {offset > 0 && <span className="faint"> · showing the last {MAX_RENDERED}</span>}
        </summary>
        <pre>{text}</pre>
      </details>
    </section>
  )
}

export default memo(EventFeed)
