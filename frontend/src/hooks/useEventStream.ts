// SSE event bus hook — connects to /api/events/stream and dispatches typed events
import { useEffect, useRef } from 'react'

export interface SseEvent {
  event_type?: string
  payload?: Record<string, unknown>
  timestamp?: string
  id?: string
  category?: string
  level?: string
  title?: string
  message?: string
  stage_progress_pct?: number
  entity_id?: string
  entity_type?: string
  action_url?: string
  details?: Record<string, unknown>
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
