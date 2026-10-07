// Queue Page — Review Queue with Docked Publishing Studio, External Video Ingestion & Quota Telemetry
import { useEffect, useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { apiGet, apiPost, apiUpload } from '../api'
import PublishingStudioPanel from '../components/PublishingStudioPanel'
import type { ToastType } from '../hooks/useToast'

interface Clip {
  id: string
  hook_text: string
  payoff_text: string
  score: number
  duration_ms: number
  status: string
  ai_provider: string
  ai_model: string
  rendered_path: string | null
  video_id: string
  thumbnail_url: string | null
  is_sensitive?: boolean
}

interface QueueData {
  clips: Clip[]
  rejected: Clip[]
}

function scoreColor(s: number) {
  if (s >= 8.5) return 'var(--success)'
  if (s >= 7.0) return 'var(--warning)'
  return 'var(--danger)'
}

function ProviderBadge({ provider }: { provider: string }) {
  const p = provider.toLowerCase()
  const style = p === 'groq' ? { background: 'rgba(255,160,0,0.15)', color: '#ffa000' }
    : p === 'gemini' ? { background: 'rgba(66,133,244,0.15)', color: '#4285f4' }
    : { background: 'rgba(255,255,255,0.06)', color: 'var(--text-muted)' }
  return <span className="badge" style={{ ...style, fontSize: 10 }}>{provider.toUpperCase()}</span>
}

export default function QueuePage({ addToast }: { addToast: (msg: string, t?: ToastType) => void }) {
  const [data, setData] = useState<QueueData | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'active' | 'rejected'>('active')
  const [busy, setBusy] = useState<string | null>(null)
  const [coverModalClip, setCoverModalClip] = useState<Clip | null>(null)
  const [coverTimestamp, setCoverTimestamp] = useState<number>(0)
  const [maxDuration, setMaxDuration] = useState<number>(60)
  const [settingCover, setSettingCover] = useState<boolean>(false)

  // URL search params for docked studio (?clip=<id>&studio=open)
  const [searchParams, setSearchParams] = useSearchParams()
  const clipParam = searchParams.get('clip')
  const studioParam = searchParams.get('studio')
  const isStudioOpen = studioParam === 'open' || Boolean(clipParam)
  const selectedClipId = clipParam || (data?.clips?.[0]?.id ?? null)

  // Quota Telemetry State
  const [quota, setQuota] = useState<{ used_units: number; daily_limit: number; pct: number; remaining_uploads: number } | null>(null)

  // External Video Ingest State
  const [showIngestModal, setShowIngestModal] = useState(false)
  const [ingestFile, setIngestFile] = useState<File | null>(null)
  const [ingestTitle, setIngestTitle] = useState('')
  const [ingestChannelId, setIngestChannelId] = useState('')
  const [channels, setChannels] = useState<{ id: string; name: string }[]>([])
  const [ingesting, setIngesting] = useState(false)

  const loadQuotaAndChannels = useCallback(async () => {
    try {
      const q = await apiGet<any>('/api/quota')
      if (q) setQuota(q)
    } catch {}
    try {
      const chs = await apiGet<any[]>('/api/channels')
      if (Array.isArray(chs)) setChannels(chs)
    } catch {}
  }, [])

  useEffect(() => {
    loadQuotaAndChannels()
  }, [loadQuotaAndChannels])

  const openPublishStudio = (clip: Clip) => {
    setSearchParams({ clip: clip.id, studio: 'open' })
  }

  const toggleStudio = () => {
    if (isStudioOpen) {
      setSearchParams({})
    } else {
      const firstId = data?.clips?.[0]?.id
      setSearchParams({ studio: 'open', ...(firstId ? { clip: firstId } : {}) })
    }
  }

  const handleIngestSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!ingestFile) {
      addToast('Please select an MP4 video file', 'error')
      return
    }
    setIngesting(true)
    try {
      const fd = new FormData()
      fd.append('file', ingestFile)
      if (ingestTitle) fd.append('title', ingestTitle)
      if (ingestChannelId) fd.append('channel_id', ingestChannelId)

      const res = await apiUpload<any>('/api/queue/ingest', fd)
      addToast('External video ingested successfully!', 'success')
      setShowIngestModal(false)
      setIngestFile(null)
      setIngestTitle('')
      await load()
      setSearchParams({ clip: res.clip_id, studio: 'open' })
    } catch (err: unknown) {
      addToast(`Ingest error: ${err instanceof Error ? err.message : String(err)}`, 'error')
    } finally {
      setIngesting(false)
    }
  }

  const load = useCallback(async () => {
    try {
      const d = await apiGet<QueueData>('/api/queue/summary')
      setData(d)
    } catch {
      addToast('Failed to load queue', 'error')
    } finally {
      setLoading(false)
    }
  }, [addToast])

  useEffect(() => { load() }, [load])

  const act = async (clipId: string, action: string, body?: object) => {
    setBusy(clipId)
    try {
      await apiPost(`/api/clips/${clipId}/${action}`, body)
      addToast(`${action} successful`, 'success')
      await load()
    } catch (e: unknown) {
      addToast(`${action} failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setBusy(null)
    }
  }

  const openFolder = async (clipId: string) => {
    try {
      const res = await apiPost<{ status?: string; message?: string; path?: string; folder?: string }>(`/api/clips/${clipId}/open-folder`)
      if (res?.status === 'success') {
        addToast(res.message || 'Opened folder in File Explorer', 'success')
      } else if (res?.status === 'fallback') {
        if (res.path) {
          try {
            await navigator.clipboard.writeText(res.path)
            addToast(`Copied file path to clipboard: ${res.path}`, 'info')
          } catch {
            addToast(`File path: ${res.path}`, 'info')
          }
        } else {
          addToast(res.message || 'Opened folder with fallback', 'info')
        }
      } else {
        addToast(res?.message || 'Could not open folder', 'error')
      }
    } catch (e: unknown) {
      addToast(`Could not open folder: ${e instanceof Error ? e.message : String(e)}`, 'error')
    }
  }

  if (loading) return <div className="spinner" data-testid="queue-loading" />

  const clips = tab === 'active' ? (data?.clips ?? []) : (data?.rejected ?? [])

  return (
    <div data-testid="queue-page">
      {/* Top Bar with Title, Quota Meter, External Video, Studio Toggle */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <h2 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Review Queue</h2>
          <span className="badge badge-muted">
            {tab === 'active' ? (data?.clips?.length ?? 0) : (data?.rejected?.length ?? 0)} items in view
          </span>

          {/* YouTube Data API Quota Telemetry */}
          {quota && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '4px 12px',
                background: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                borderRadius: 8,
                fontSize: 12,
              }}
              title="YouTube Data API Daily Quota (Resets midnight Pacific / 08:00 UTC)"
            >
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', lineHeight: 1.2 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontWeight: 600 }}>YouTube API Quota <small style={{ fontWeight: 400, opacity: 0.8 }}>(estimate)</small></span>
                  <span
                    className={`badge ${quota.pct > 80 ? 'badge-danger' : quota.pct > 50 ? 'badge-warning' : 'badge-info'}`}
                    style={{ fontSize: 10 }}
                  >
                    {quota.used_units.toLocaleString()} / {quota.daily_limit.toLocaleString()} units ({quota.pct}%)
                  </span>
                </div>
                <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                  ~{quota.remaining_uploads} upload{quota.remaining_uploads === 1 ? '' : 's'} remaining today (1,600 units/upload)
                </span>
              </div>
              <div style={{ width: 64, height: 6, background: '#262626', borderRadius: 3, overflow: 'hidden' }}>
                <div
                  style={{
                    width: `${Math.min(quota.pct, 100)}%`,
                    height: '100%',
                    background: quota.pct > 80 ? 'var(--danger)' : quota.pct > 50 ? 'var(--warning)' : 'var(--accent)',
                    transition: 'width 0.3s ease',
                  }}
                />
              </div>
            </div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setShowIngestModal(true)}
            data-testid="add-external-video-btn"
            title="Ingest external MP4 video directly into Publishing Studio"
          >
            ➕ Add External Video
          </button>

          <button
            type="button"
            className={`btn btn-sm ${isStudioOpen ? 'btn-primary' : 'btn-ghost'}`}
            onClick={toggleStudio}
            data-testid="toggle-studio-btn"
            title="Toggle docked master-detail Publishing Studio"
          >
            🚀 Video Publishing Studio
          </button>
        </div>
      </div>

      {/* Main Workspace: Clips Column + Docked Studio Panel */}
      <div className={`docked-studio-container ${isStudioOpen ? 'studio-open' : ''}`}>
        <div className="docked-studio-clips">
          {/* Tabs */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
            <button
              className={`btn ${tab === 'active' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setTab('active')}
              data-testid="tab-active"
            >
              🎬 Active Queue {data?.clips?.length ? `(${data.clips.length})` : ''}
            </button>
            <button
              className={`btn ${tab === 'rejected' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setTab('rejected')}
              data-testid="tab-rejected"
            >
              🗑 Rejected {data?.rejected?.length ? `(${data.rejected.length})` : ''}
            </button>
          </div>

          {clips.length === 0 ? (
            <div className="empty-state" data-testid="queue-empty">
              <div className="empty-state-icon">{tab === 'active' ? '🎬' : '🗑'}</div>
              <div className="empty-state-title">{tab === 'active' ? 'No clips pending review' : 'No rejected clips'}</div>
              {tab === 'active' && (
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  style={{ marginTop: 12 }}
                  onClick={() => setShowIngestModal(true)}
                >
                  ➕ Add External Video
                </button>
              )}
            </div>
          ) : (
            <div className="clip-grid" data-testid="clip-grid">
              {clips.map(clip => {
                const isSelected = isStudioOpen && selectedClipId === clip.id
                return (
                  <div
                    key={clip.id}
                    className="clip-card"
                    style={isSelected ? { borderColor: 'var(--accent)', boxShadow: '0 0 12px var(--accent-glow)' } : undefined}
                    data-testid={`clip-card-${clip.id}`}
                  >
                    <div className="clip-card-thumb">
                      {clip.thumbnail_url
                        ? <img src={clip.thumbnail_url} alt="thumb" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                        : '🎬'}
                    </div>
                    <div className="clip-card-body">
                      <div className="clip-card-hook">{clip.hook_text || 'No hook text'}</div>
                      <div className="clip-card-meta">
                        <span style={{ color: scoreColor(clip.score), fontWeight: 700 }}>
                          {clip.score.toFixed(1)}
                        </span>
                        {' · '}
                        {Math.round(clip.duration_ms / 1000)}s
                        {' · '}
                        <ProviderBadge provider={clip.ai_provider} />
                        {clip.is_sensitive && (
                          <>
                            {' · '}
                            <span
                              className="badge badge-warning"
                              style={{
                                background: 'rgba(239, 68, 68, 0.15)',
                                color: '#ef4444',
                                border: '1px solid #ef4444',
                                fontWeight: 700,
                                fontSize: 10,
                                letterSpacing: '0.04em',
                              }}
                              title="Flagged by Policy Gate as Sensitive Content (Violence, Tragedy, Hate, Adult)"
                              data-testid={`sensitive-badge-${clip.id}`}
                            >
                              ⚠️ SENSITIVE_CONTENT
                            </span>
                          </>
                        )}
                      </div>
                      <div className="clip-card-actions">
                        {tab === 'active' ? (
                          <>
                            <button
                              className="btn btn-success btn-sm"
                              onClick={() => act(clip.id, 'approve')}
                              disabled={busy === clip.id}
                              data-testid={`approve-btn-${clip.id}`}
                            >✅ Approve</button>
                            <button
                              className="btn btn-primary btn-sm"
                              style={{ background: 'var(--accent)', borderColor: 'var(--accent)', color: '#000' }}
                              onClick={() => openPublishStudio(clip)}
                              disabled={busy === clip.id}
                              data-testid={`publish-btn-${clip.id}`}
                              title="Open docked Publishing Studio for this clip"
                            >🚀 Studio</button>
                            <button
                              className="btn btn-danger btn-sm"
                              onClick={() => act(clip.id, 'reject')}
                              disabled={busy === clip.id}
                              data-testid={`reject-btn-${clip.id}`}
                            >❌ Reject</button>
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={() => {
                                setCoverModalClip(clip)
                                setCoverTimestamp(0)
                                setMaxDuration(clip.duration_ms ? clip.duration_ms / 1000 : 60)
                              }}
                              data-testid={`cover-btn-${clip.id}`}
                              title="Set YouTube Shorts Cover Frame"
                            >📸 Cover</button>
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={() => openFolder(clip.id)}
                              data-testid={`folder-btn-${clip.id}`}
                              title="Open in Explorer"
                            >📁</button>
                          </>
                        ) : (
                          <>
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={() => act(clip.id, 'restore')}
                              disabled={busy === clip.id}
                              data-testid={`restore-btn-${clip.id}`}
                            >↩ Restore</button>
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={() => act(clip.id, 'recover')}
                              disabled={busy === clip.id}
                              data-testid={`recover-btn-${clip.id}`}
                            >🛠️ Recover</button>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Docked Publishing Studio Panel */}
        {isStudioOpen && (
          <>
            <div className="docked-studio-backdrop" onClick={() => setSearchParams({})} />
            <div className="docked-studio-panel">
              <PublishingStudioPanel
                clipId={selectedClipId}
                onClose={() => setSearchParams({})}
                addToast={addToast}
                onClipPublished={() => {
                  load()
                  loadQuotaAndChannels()
                }}
                onPrevClip={() => {
                  const idx = clips.findIndex(c => c.id === selectedClipId)
                  if (idx > 0) {
                    const prev = clips[idx - 1]
                    const p = new URLSearchParams(searchParams)
                    p.set('clip', prev.id)
                    p.set('studio', 'open')
                    setSearchParams(p)
                  }
                }}
                onNextClip={() => {
                  const idx = clips.findIndex(c => c.id === selectedClipId)
                  if (idx >= 0 && idx < clips.length - 1) {
                    const next = clips[idx + 1]
                    const p = new URLSearchParams(searchParams)
                    p.set('clip', next.id)
                    p.set('studio', 'open')
                    setSearchParams(p)
                  }
                }}
                hasPrevClip={clips.findIndex(c => c.id === selectedClipId) > 0}
                hasNextClip={clips.findIndex(c => c.id === selectedClipId) >= 0 && clips.findIndex(c => c.id === selectedClipId) < clips.length - 1}
              />
            </div>
          </>
        )}
      </div>

      {/* Cover Frame Scrubber Modal (Enhancement 7) */}
      {coverModalClip && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.8)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
            padding: 16,
          }}
          data-testid="cover-modal"
        >
          <div
            style={{
              background: '#181824',
              border: '1px solid rgba(255,255,255,0.15)',
              borderRadius: 8,
              width: '100%',
              maxWidth: 400,
              padding: 16,
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h4 style={{ margin: 0, fontSize: 16, color: '#f59e0b' }}>📸 Cover Frame Selector</h4>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setCoverModalClip(null)}
              >✕</button>
            </div>

            <video
              id="reactModalVideo"
              src={`/api/video/${coverModalClip.id}`}
              controls
              playsInline
              style={{
                width: '100%',
                maxHeight: '52vh',
                aspectRatio: '9/16',
                borderRadius: 6,
                background: '#000',
              }}
              onLoadedMetadata={(e) => {
                const dur = (e.target as HTMLVideoElement).duration
                if (dur && !isNaN(dur)) setMaxDuration(dur)
              }}
            />

            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, color: '#e5e7eb' }}>
                <span>Cover Pos (first 0.033s inject):</span>
                <span style={{ fontFamily: 'monospace', fontWeight: 700, color: '#60a5fa' }}>
                  {coverTimestamp.toFixed(2)}s / {maxDuration.toFixed(2)}s
                </span>
              </div>
              <input
                type="range"
                min="0"
                max={maxDuration || 60}
                step="0.05"
                value={coverTimestamp}
                onChange={(e) => {
                  const val = parseFloat(e.target.value)
                  setCoverTimestamp(val)
                  const vid = document.getElementById('reactModalVideo') as HTMLVideoElement
                  if (vid) vid.currentTime = val
                }}
                style={{ width: '100%' }}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  const vid = document.getElementById('reactModalVideo') as HTMLVideoElement
                  if (vid) setCoverTimestamp(vid.currentTime || 0)
                }}
              >Use Video Pos</button>
              <button
                className="btn btn-primary btn-sm"
                disabled={settingCover}
                onClick={async () => {
                  setSettingCover(true)
                  try {
                    await apiPost(`/api/clips/${coverModalClip.id}/cover-frame`, {
                      timestamp_sec: coverTimestamp,
                    })
                    addToast('Cover frame injected into video (0.033s)!', 'success')
                    setCoverModalClip(null)
                    await load()
                  } catch (err: unknown) {
                    addToast(
                      `Failed to set cover frame: ${err instanceof Error ? err.message : String(err)}`,
                      'error'
                    )
                  } finally {
                    setSettingCover(false)
                  }
                }}
                data-testid="submit-cover-frame-btn"
              >
                {settingCover ? 'Injecting...' : 'Set as Cover'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal: External Video Ingest */}
      {showIngestModal && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.8)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
            padding: 16,
          }}
          data-testid="external-ingest-modal"
        >
          <div
            style={{
              background: '#181824',
              border: '1px solid rgba(255,255,255,0.15)',
              borderRadius: 8,
              width: '100%',
              maxWidth: 480,
              padding: 20,
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h4 style={{ margin: 0, fontSize: 16, color: 'var(--accent)' }}>➕ Add External Video</h4>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setShowIngestModal(false)}
              >✕</button>
            </div>

            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-muted)' }}>
              Upload an external rendered <code>.mp4</code> video to review and publish directly via Shorts Engine.
            </p>

            <form onSubmit={handleIngestSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div className="form-group" style={{ margin: 0 }}>
                <label className="form-label" style={{ fontSize: 12 }}>Select Video File (.mp4, .mov, .webm) *</label>
                <input
                  type="file"
                  className="form-input"
                  accept="video/mp4,video/quicktime,video/webm"
                  onChange={e => setIngestFile(e.target.files?.[0] || null)}
                  required
                />
              </div>

              <div className="form-group" style={{ margin: 0 }}>
                <label className="form-label" style={{ fontSize: 12 }}>Clip Title (Optional)</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="Enter video title or leave blank to infer..."
                  value={ingestTitle}
                  onChange={e => setIngestTitle(e.target.value)}
                />
              </div>

              <div className="form-group" style={{ margin: 0 }}>
                <label className="form-label" style={{ fontSize: 12 }}>Target Channel (Optional)</label>
                <select
                  className="form-select"
                  value={ingestChannelId}
                  onChange={e => setIngestChannelId(e.target.value)}
                >
                  <option value="">— Default System Channel —</option>
                  {channels.map(ch => (
                    <option key={ch.id} value={ch.id}>{ch.name}</option>
                  ))}
                </select>
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 10 }}>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setShowIngestModal(false)}
                  disabled={ingesting}
                >Cancel</button>
                <button
                  type="submit"
                  className="btn btn-primary btn-sm"
                  disabled={ingesting || !ingestFile}
                  data-testid="submit-external-ingest-btn"
                >
                  {ingesting ? 'Uploading & Ingesting…' : 'Upload & Ingest'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
