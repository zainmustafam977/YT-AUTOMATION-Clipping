import { Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { useState, useCallback } from 'react'
import StatusPage from './pages/StatusPage'
import QueuePage from './pages/QueuePage'
import ChannelsPage from './pages/ChannelsPage'
import SettingsPage from './pages/SettingsPage'
import LogsPage from './pages/LogsPage'
import { useToast, Toast } from './hooks/useToast'
import { useEventStream, SseEvent } from './hooks/useEventStream'

const NAV = [
  { to: '/status',   icon: '📊', label: 'Status' },
  { to: '/queue',    icon: '🎬', label: 'Review Queue' },
  { to: '/channels', icon: '📺', label: 'Channels' },
  { to: '/logs',     icon: '📋', label: 'Logs' },
  { to: '/settings', icon: '⚙️', label: 'Settings' },
]

function ToastStack({ toasts, remove }: { toasts: Toast[]; remove: (id: number) => void }) {
  return (
    <div className="toast-stack">
      {toasts.map(t => (
        <div key={t.id} className={`toast ${t.type}`} role="alert">
          <span>{t.type === 'success' ? '✅' : t.type === 'error' ? '❌' : 'ℹ️'}</span>
          <span style={{ flex: 1 }}>{t.message}</span>
          <button
            onClick={() => remove(t.id)}
            style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', fontSize: 16 }}
            aria-label="Dismiss"
          >×</button>
        </div>
      ))}
    </div>
  )
}

export default function App() {
  const { toasts, addToast, removeToast } = useToast()
  const [sseConnected, setSseConnected] = useState(false)

  const handleSseEvent = useCallback((ev: SseEvent) => {
    setSseConnected(true)
    if (ev.title) {
      const type = (ev.level === 'SUCCESS') ? 'success' : (ev.level === 'ERROR' || ev.level === 'CRITICAL') ? 'error' : 'info'
      addToast(`${ev.title}: ${ev.message ?? ''}`, type)
    } else if (ev.event_type === 'pipeline_complete') {
      addToast(`✅ Pipeline complete: ${ev.payload?.video_id ?? ''}`, 'success')
    } else if (ev.event_type === 'pipeline_failed') {
      addToast(`❌ Pipeline failed: ${ev.payload?.video_id ?? ''}`, 'error')
    } else if (ev.event_type === 'clip_published') {
      addToast(`🚀 Clip published: ${ev.payload?.clip_id ?? ''}`, 'success')
    }
  }, [addToast])

  useEventStream(handleSseEvent)

  return (
    <div className="shell">
      {/* ── Header ── */}
      <header className="header" data-testid="app-header">
        <div className="header-logo">⚡ Shorts<span>Engine</span></div>
        <div className="live-banner" style={{ margin: 0, padding: '4px 12px', fontSize: 11 }}>
          <div className="status-dot" style={{ background: sseConnected ? 'var(--success)' : 'var(--warning)' }} />
          {sseConnected ? 'Live' : 'Connecting…'}
        </div>
        <div className="header-spacer" />
        <div className="header-user" data-testid="user-pill">Operator</div>
        <a
          href="/logout"
          className="btn btn-ghost btn-sm"
          data-testid="logout-btn"
          style={{ marginLeft: 8 }}
        >Logout</a>
      </header>

      {/* ── Sidebar ── */}
      <nav className="sidebar" data-testid="sidebar">
        <div className="nav-section-label">Navigation</div>
        {NAV.map(n => (
          <NavLink
            key={n.to}
            to={n.to}
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
            data-testid={`nav-${n.label.toLowerCase().replace(/ /g, '-')}`}
          >
            <span className="nav-link-icon">{n.icon}</span>
            {n.label}
          </NavLink>
        ))}
        <div className="nav-section-label" style={{ marginTop: 16 }}>Links</div>
        <a
          href="/api/docs"
          target="_blank"
          rel="noopener noreferrer"
          className="nav-link"
          data-testid="nav-api-docs"
        >
          <span className="nav-link-icon">📖</span>
          API Docs
        </a>
        <a
          href="/"
          className="nav-link"
          data-testid="nav-classic-dashboard"
        >
          <span className="nav-link-icon">🏛️</span>
          Classic Dashboard
        </a>
      </nav>

      {/* ── Main Content ── */}
      <main className="main-content" data-testid="main-content">
        <Routes>
          <Route path="/" element={<Navigate to="/status" replace />} />
          <Route path="/status"   element={<StatusPage addToast={addToast} />} />
          <Route path="/queue"    element={<QueuePage  addToast={addToast} />} />
          <Route path="/publish"  element={<Navigate to="/queue?studio=open" replace />} />
          <Route path="/channels" element={<ChannelsPage addToast={addToast} />} />
          <Route path="/logs"     element={<LogsPage />} />
          <Route path="/settings" element={<SettingsPage addToast={addToast} />} />
          <Route path="*" element={
            <div className="empty-state">
              <div className="empty-state-icon">🔍</div>
              <div className="empty-state-title">Page not found</div>
              <NavLink to="/status" className="btn btn-ghost" style={{ marginTop: 12 }}>Go to Status</NavLink>
            </div>
          } />
        </Routes>
      </main>

      <ToastStack toasts={toasts} remove={removeToast} />
    </div>
  )
}
