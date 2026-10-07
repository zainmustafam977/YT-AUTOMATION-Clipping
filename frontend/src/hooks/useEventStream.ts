// SSE event bus hook — connects to /api/events/stream and dispatches typed events
import { useEffect, useRef } from 'react'

export interface SseEvent {
  event_type: string
  payload: Record<string, unknown>
  timestamp: string
}

export function useEventStream(
  onEvent: (ev: SseEvent) => void,
  enabled = true
) {
  const cbRef = useRef(onEvent)
  cbRef.current = onEvent

  useEffect(() => {
    if (!enabled) return
    const es = new EventSource('/api/events/stream')

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as SseEvent
        cbRef.current(data)
      } catch {
        // ignore malformed frames
      }
    }

    es.onerror = () => {
      // browser will auto-reconnect
    }

    return () => es.close()
  }, [enabled])
}
