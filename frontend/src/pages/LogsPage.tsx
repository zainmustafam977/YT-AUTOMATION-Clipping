// Logs Page — live SSE log stream viewer
import { useEffect, useRef, useState } from 'react'

interface LogLine {
  id: number
  ts: string
  level: string
  msg: string
}

const LEVEL_COLOR: Record<string, string> = {
  ERROR: 'var(--danger)',
  WARNING: 'var(--warning)',
  WARN: 'var(--warning)',
  INFO: 'var(--text-primary)',
  DEBUG: 'var(--text-muted)',
}

let _lid = 0

export default function LogsPage() {
  const [lines, setLines] = useState<LogLine[]>([])
  const [filter, setFilter] = useState('')
  const [paused, setPaused] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    const es = new EventSource('/api/logs/stream')
    esRef.current = es

    es.onmessage = (e) => {
      if (paused) return
      try {
        const d = JSON.parse(e.data)
        setLines(prev => {
          const next = [...prev, {
            id: ++_lid,
            ts: d.timestamp ?? new Date().toISOString(),
            level: d.level ?? 'INFO',
            msg: d.message ?? e.data,
          }]
          return next.slice(-500) // keep last 500 lines
        })
      } catch {
        setLines(prev => [...prev, { id: ++_lid, ts: new Date().toISOString(), level: 'INFO', msg: e.data }].slice(-500))
      }
    }

    return () => { es.close(); esRef.current = null }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!paused) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lines, paused])

  const filtered = filter
    ? lines.filter(l => l.msg.toLowerCase().includes(filter.toLowerCase()) || l.level.toLowerCase().includes(filter.toLowerCase()))
    : lines

  return (
    <div data-testid="logs-page">
      <div className="card">
        <div className="card-header">
          <div className="card-title">📋 Live Log Stream</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              className="form-input"
              style={{ width: 220 }}
              placeholder="Filter logs…"
              value={filter}
              onChange={e => setFilter(e.target.value)}
              data-testid="log-filter-input"
            />
            <button
              className={`btn ${paused ? 'btn-primary' : 'btn-ghost'} btn-sm`}
              onClick={() => setPaused(p => !p)}
              data-testid="log-pause-btn"
            >{paused ? '▶ Resume' : '⏸ Pause'}</button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setLines([])}
              data-testid="log-clear-btn"
            >🗑 Clear</button>
          </div>
        </div>
        <div
          style={{
            background: 'var(--bg-base)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '12px 16px',
            height: 520,
            overflowY: 'auto',
            fontFamily: 'monospace',
            fontSize: 12,
            lineHeight: 1.6,
          }}
          data-testid="log-output"
        >
          {filtered.length === 0 ? (
            <span style={{ color: 'var(--text-muted)' }}>Waiting for log events…</span>
          ) : filtered.map(l => (
            <div key={l.id} style={{ display: 'flex', gap: 10 }}>
              <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>
                {new Date(l.ts).toLocaleTimeString()}
              </span>
              <span style={{ color: LEVEL_COLOR[l.level] ?? 'var(--text-primary)', flexShrink: 0, width: 54 }}>
                [{l.level}]
              </span>
              <span style={{ color: 'var(--text-primary)', wordBreak: 'break-word' }}>{l.msg}</span>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
        <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
          {lines.length} lines (max 500) · {paused ? '⏸ paused' : '● live'}
        </div>
      </div>
    </div>
  )
}
