import { useEffect, useState } from 'react'
import { apiGet, apiPost, apiUpload } from '../api'
import type { ToastType } from '../hooks/useToast'

interface ApiKeyStatus {
  provider: string
  model: string
  rpm_used: number
  rpm_limit: number
  tpm_used: number
  tpm_limit: number
  latency_ms: number
  status: string
}

interface PipelineRow {
  id: string
  youtube_video_id: string
  title: string | null
  status: string
  created_at: string
  updated_at: string | null
}

interface StatusData {
  api_keys: ApiKeyStatus[]
  pipelines: PipelineRow[]
  disk_used_gb: number
  disk_total_gb: number
  clip_count: number
  published_count: number
  pending_count: number
}

const STATUS_COLORS: Record<string, string> = {
  DONE: 'success', COMPLETED: 'success',
  FAILED: 'danger', FAILED_NEEDS_REVIEW: 'danger',
  DOWNLOADING: 'info', TRANSCRIBING: 'info', ANALYZING: 'info',
  RENDERING: 'warning', PUBLISHING: 'warning',
  APPROVED: 'success', PENDING: 'muted',
  POLICY_RESTRICTED: 'danger', PENDING_POLICY_REVIEW: 'warning',
}

function statusBadge(s: string) {
  if (s === 'POLICY_RESTRICTED') {
    return (
      <span
        className="badge badge-danger"
        style={{ background: 'rgba(239, 68, 68, 0.2)', border: '1px solid #ef4444', color: '#ef4444', fontWeight: 700 }}
        title="Restricted by Policy Gate (Sensitive / Age-restricted / Violating Content)"
        data-testid="badge-policy-restricted"
      >
        🛡️ POLICY_RESTRICTED
      </span>
    )
  }
  if (s === 'PENDING_POLICY_REVIEW') {
    return (
      <span
        className="badge badge-warning"
        style={{ background: 'rgba(245, 158, 11, 0.2)', border: '1px solid #f59e0b', color: '#f59e0b', fontWeight: 700 }}
        title="Held by Policy Gate for Operator Review (Sensitive / Warning Detected)"
        data-testid="badge-pending-policy-review"
      >
        ⚠️ PENDING_POLICY_REVIEW
      </span>
    )
  }
  const cls = STATUS_COLORS[s] ?? 'muted'
  return <span className={`badge badge-${cls}`}>{s}</span>
}

export default function StatusPage({ addToast }: { addToast: (msg: string, t?: ToastType) => void }) {
  const [data, setData] = useState<StatusData | null>(null)
  const [loading, setLoading] = useState(true)
  const [url, setUrl] = useState('')
  const [forceRerun, setForceRerun] = useState(false)
  const [ingesting, setIngesting] = useState(false)
  const [clipLimitMode, setClipLimitMode] = useState<'auto' | 'manual'>('auto')
  const [minClips, setMinClips] = useState<number | ''>('')
  const [maxClips, setMaxClips] = useState<number | ''>('')
  const [estimate, setEstimate] = useState<any | null>(null)
  const [dateFilter, setDateFilter] = useState<string>('all')
  const [customStart, setCustomStart] = useState<string>('')
  const [customEnd, setCustomEnd] = useState<string>('')

  // Direct Video Ingest Studio state
  const [ingestTab, setIngestTab] = useState<'url' | 'local_path' | 'upload'>('url')
  const [localPath, setLocalPath] = useState('')
  const [localTitle, setLocalTitle] = useState('')
  const [probingPath, setProbingPath] = useState(false)
  const [pathProbe, setPathProbe] = useState<any | null>(null)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadTitle, setUploadTitle] = useState('')
  const [uploading, setUploading] = useState(false)

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const d = await apiGet<StatusData>('/api/status/summary')
        if (alive) { setData(d); setLoading(false) }
      } catch (e) {
        if (alive) { setLoading(false) }
      }
    }
    load()
    const iv = setInterval(load, 8000)
    return () => { alive = false; clearInterval(iv) }
  }, [])

  useEffect(() => {
    if (!url.trim()) {
      setEstimate(null)
      return
    }
    const timer = setTimeout(async () => {
      try {
        const res = await apiGet<any>(`/api/video-status/${encodeURIComponent(url.trim())}`)
        if (res && res.clip_estimate) {
          setEstimate(res.clip_estimate)
        } else {
          setEstimate(null)
        }
      } catch {
        setEstimate(null)
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [url])

  const handleIngest = async () => {
    if (!url.trim()) { addToast('Enter a YouTube URL or local path', 'error'); return }
    setIngesting(true)
    try {
      const payload: any = {
        url: url.trim(),
        force_rerun: forceRerun,
        clip_limit_mode: clipLimitMode
      }
      if (clipLimitMode === 'manual') {
        if (minClips !== '') payload.override_min_segments = Number(minClips)
        if (maxClips !== '') payload.override_max_segments = Number(maxClips)
      }
      await apiPost('/api/ingest', payload)
      addToast('⚡ Ingestion started!', 'success')
      setUrl('')
      setEstimate(null)
    } catch (e: unknown) {
      addToast(`Ingestion failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setIngesting(false)
    }
  }

  const handleProbePath = async () => {
    if (!localPath.trim()) { addToast('Enter a local video file path to probe', 'error'); return }
    setProbingPath(true)
    setPathProbe(null)
    try {
      const res = await apiPost<any>('/api/ingest/validate-path', { file_path: localPath.trim() })
      setPathProbe(res)
      if (res.valid) {
        addToast(`Path verified: ${res.format_name || 'Video'} (${(res.size_bytes / (1024 * 1024)).toFixed(1)} MB)`, 'success')
      } else {
        addToast(res.error || 'Invalid file path', 'error')
      }
    } catch (e: unknown) {
      addToast(`Probe failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setProbingPath(false)
    }
  }

  const handleIngestLocalPath = async () => {
    if (!localPath.trim()) { addToast('Enter a local video file path', 'error'); return }
    setIngesting(true)
    try {
      const payload: any = {
        file_path: localPath.trim(),
        title: localTitle.trim() || undefined,
      }
      await apiPost('/api/ingest/local-path', payload)
      addToast('⚡ Zero-copy local video registered and queued for processing!', 'success')
      setLocalPath('')
      setLocalTitle('')
      setPathProbe(null)
    } catch (e: unknown) {
      addToast(`Local path ingest failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setIngesting(false)
    }
  }

  const handleDirectUpload = async () => {
    if (!uploadFile) { addToast('Select a video file to upload', 'error'); return }
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', uploadFile)
      if (uploadTitle.trim()) {
        fd.append('title', uploadTitle.trim())
      }
      await apiUpload('/api/ingest/local-file', fd)
      addToast('⚡ Direct video upload and ingestion complete!', 'success')
      setUploadFile(null)
      setUploadTitle('')
    } catch (e: unknown) {
      addToast(`Upload failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setUploading(false)
    }
  }

  const handleCancel = async (id: string) => {
    try {
      await apiPost(`/api/pipeline/${id}/cancel`)
      addToast('Pipeline cancelled', 'info')
    } catch { addToast('Cancel failed', 'error') }
  }

  const handleResume = async (videoId: string) => {
    try {
      await apiPost('/api/ingest', { url: videoId, force_rerun: true })
      addToast('Pipeline resumed', 'success')
    } catch { addToast('Resume failed', 'error') }
  }

  const diskPct = data && data.disk_total_gb > 0 ? Math.round((data.disk_used_gb / data.disk_total_gb) * 100) : 0

  return (
    <div data-testid="status-page">
      {loading && !data && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
          <div className="spinner" data-testid="status-loading" style={{ width: 20, height: 20 }} />
          <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>Syncing real-time pipeline status…</span>
        </div>
      )}
      {/* Operational Readiness Assistant */}
      {data && data.api_keys.length === 0 && (
        <div style={{
          background: 'rgba(245, 158, 11, 0.1)',
          border: '1px solid #f59e0b',
          borderRadius: 8,
          padding: '14px 18px',
          marginBottom: 20
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontWeight: 700, color: '#f59e0b', fontSize: 14 }}>
              ⚠️ System Setup &amp; Operational Readiness Checklist
            </span>
            <span className="badge" style={{ background: '#f59e0b', color: '#000', fontWeight: 600 }}>Action Recommended</span>
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: 13, margin: '0 0 12px 0' }}>
            No AI API keys configured. The platform is running in algorithmic fallback mode. Configure a free Groq or Gemini API key to unlock AI viral analysis, hooks scoring, and CTR titles.
          </p>
          <a href="/settings" className="btn btn-ghost btn-sm" style={{ borderColor: '#f59e0b', color: '#f59e0b' }}>
            🔑 Configure AI API Keys in Settings →
          </a>
        </div>
      )}

      {/* Stat Cards */}
      <div className="stat-grid" data-testid="stat-grid">
        <div className="stat-card">
          <div className="stat-value">{data?.clip_count ?? 0}</div>
          <div className="stat-label">Total Clips</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--success)' }}>{data?.published_count ?? 0}</div>
          <div className="stat-label">Published</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--warning)' }}>{data?.pending_count ?? 0}</div>
          <div className="stat-label">Pending Review</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{diskPct}%</div>
          <div className="stat-label">Disk Used ({data?.disk_used_gb?.toFixed(1)} / {data?.disk_total_gb?.toFixed(1)} GB)</div>
          <div className="progress-bar">
            <div className={`progress-fill ${diskPct > 85 ? 'danger' : diskPct > 60 ? 'warning' : 'accent'}`}
              style={{ width: `${diskPct}%` }} />
          </div>
        </div>
      </div>

      {/* Ingestion & Direct Creator Studio */}
      <div className="card" data-testid="ingest-card">
        <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
          <div>
            <div className="card-title">⚡ Ingest & Process Video</div>
            <div className="card-subtitle">YouTube URL, zero-copy local path, or direct media upload</div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              className={`btn btn-sm ${ingestTab === 'url' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setIngestTab('url')}
              data-testid="tab-ingest-url"
            >
              🌐 YouTube URL
            </button>
            <button
              className={`btn btn-sm ${ingestTab === 'local_path' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setIngestTab('local_path')}
              data-testid="tab-ingest-local-path"
            >
              📁 Zero-Copy Path
            </button>
            <button
              className={`btn btn-sm ${ingestTab === 'upload' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setIngestTab('upload')}
              data-testid="tab-ingest-upload"
            >
              ⬆️ Direct Upload
            </button>
          </div>
        </div>

        {/* Tab 1: YouTube URL / ID */}
        {ingestTab === 'url' && (
          <div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
              <div className="form-group" style={{ flex: 1, marginBottom: 0 }}>
                <input
                  className="form-input"
                  placeholder="https://youtube.com/watch?v=… or 11-char Video ID"
                  value={url}
                  onChange={e => setUrl(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleIngest()}
                  data-testid="ingest-url-input"
                />
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                <input type="checkbox" checked={forceRerun} onChange={e => setForceRerun(e.target.checked)} data-testid="force-rerun-toggle" />
                Force Re-run
              </label>
              <button
                className="btn btn-primary"
                onClick={handleIngest}
                disabled={ingesting || !url.trim()}
                data-testid="ingest-btn"
              >
                {ingesting ? '⏳ Ingesting…' : '⚡ Ingest'}
              </button>
            </div>

            {/* Dynamic Clip Estimation & Mode Toggle */}
            <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                <label style={{ fontSize: 13, color: 'var(--text-muted)' }}>Clip Limits Mode:</label>
                <select
                  className="form-input"
                  style={{ width: 'auto', padding: '4px 8px', fontSize: 12 }}
                  value={clipLimitMode}
                  onChange={e => setClipLimitMode(e.target.value as 'auto' | 'manual')}
                >
                  <option value="auto">✨ Auto-Calculate (Smart Length Adaptive)</option>
                  <option value="manual">⚙️ Manual Override Limits</option>
                </select>
                {clipLimitMode === 'manual' && (
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <input
                      type="number"
                      placeholder="Min Clips"
                      className="form-input"
                      style={{ width: 90, padding: '4px 8px', fontSize: 12 }}
                      value={minClips}
                      onChange={e => setMinClips(e.target.value ? Number(e.target.value) : '')}
                    />
                    <input
                      type="number"
                      placeholder="Max Clips"
                      className="form-input"
                      style={{ width: 90, padding: '4px 8px', fontSize: 12 }}
                      value={maxClips}
                      onChange={e => setMaxClips(e.target.value ? Number(e.target.value) : '')}
                    />
                  </div>
                )}
              </div>

              {estimate && (
                <div style={{ padding: '8px 12px', background: 'rgba(255,193,7,0.1)', border: '1px solid rgba(255,193,7,0.3)', borderRadius: 6, fontSize: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span style={{ fontWeight: 600, color: 'var(--warning)' }}>🎯 Smart Dynamic Clip Estimation:</span>
                    <span className="badge badge-warning">{estimate.recommended_range}</span>
                  </div>
                  <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>{estimate.explanation}</div>
                  <div style={{ marginTop: 4, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>Recommended Model:</span>
                    <span className="badge badge-info">{estimate.model_name}</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Tab 2: Zero-Copy Local File Path */}
        {ingestTab === 'local_path' && (
          <div data-testid="local-path-studio">
            <p style={{ color: 'var(--text-muted)', fontSize: 12, margin: '0 0 10px 0' }}>
              ⚡ <strong>Zero-Copy Ingestion:</strong> Point directly to any local video file (MP4, MKV, MOV, WebM, AVI). The engine will process the file directly without copying or duplicating storage.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <input
                  className="form-input"
                  style={{ flex: 2 }}
                  placeholder="Absolute local file path (e.g. D:\Videos\podcast_raw.mp4 or /data/video.mov)"
                  value={localPath}
                  onChange={e => { setLocalPath(e.target.value); setPathProbe(null) }}
                  data-testid="local-path-input"
                />
                <input
                  className="form-input"
                  style={{ flex: 1 }}
                  placeholder="Custom Title (optional)"
                  value={localTitle}
                  onChange={e => setLocalTitle(e.target.value)}
                  data-testid="local-title-input"
                />
                <button
                  className="btn btn-ghost"
                  onClick={handleProbePath}
                  disabled={probingPath || !localPath.trim()}
                  data-testid="probe-path-btn"
                  title="Probe file existence, duration, and codec metadata"
                >
                  {probingPath ? '🔍 Probing…' : '🔍 Pre-Flight Probe'}
                </button>
                <button
                  className="btn btn-primary"
                  onClick={handleIngestLocalPath}
                  disabled={ingesting || !localPath.trim()}
                  data-testid="ingest-local-btn"
                >
                  {ingesting ? '⏳ Queuing…' : '⚡ Ingest Zero-Copy'}
                </button>
              </div>

              {/* Probe Result Box */}
              {pathProbe && (
                <div style={{
                  padding: '10px 14px',
                  borderRadius: 6,
                  border: pathProbe.valid ? '1px solid rgba(16, 185, 129, 0.4)' : '1px solid rgba(239, 68, 68, 0.4)',
                  background: pathProbe.valid ? 'rgba(16, 185, 129, 0.08)' : 'rgba(239, 68, 68, 0.08)',
                  fontSize: 12,
                }} data-testid="path-probe-result">
                  {pathProbe.valid ? (
                    <div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                        <span style={{ fontWeight: 700, color: 'var(--success)' }}>
                          ✅ Valid Media File Verified (Zero-Copy Ready)
                        </span>
                        <span className="badge badge-success">{pathProbe.format_name?.toUpperCase() || 'VIDEO'}</span>
                      </div>
                      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', color: 'var(--text-muted)', fontSize: 12, marginTop: 4 }}>
                        <span>⏱️ Duration: <strong>{Math.round(pathProbe.duration_sec ?? 0)}s</strong></span>
                        <span>📐 Resolution: <strong>{pathProbe.width}x{pathProbe.height}</strong></span>
                        <span>🎞️ Codec: <strong>{pathProbe.codec_name}</strong></span>
                        <span>💾 Size: <strong>{((pathProbe.size_bytes ?? 0) / (1024 * 1024)).toFixed(1)} MB</strong></span>
                        <span>🔗 Mode: <strong>Zero-Copy Direct Mapping</strong></span>
                      </div>
                    </div>
                  ) : (
                    <div style={{ color: '#ef4444', fontWeight: 600 }}>
                      ❌ Pre-flight probe failed: {pathProbe.error || 'File not found or format unsupported'}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Tab 3: Direct Media File Upload */}
        {ingestTab === 'upload' && (
          <div data-testid="direct-upload-studio">
            <p style={{ color: 'var(--text-muted)', fontSize: 12, margin: '0 0 10px 0' }}>
              ⬆️ <strong>Streaming Multipart Upload:</strong> Upload master footage directly from your browser. Files are chunk-streamed directly to staging disk without exhausting server RAM.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{
                border: '2px dashed rgba(255,255,255,0.2)',
                borderRadius: 8,
                padding: '18px 20px',
                textAlign: 'center',
                background: 'rgba(255,255,255,0.02)',
                cursor: 'pointer',
              }}>
                <input
                  type="file"
                  id="direct-video-file-input"
                  accept="video/mp4,video/quicktime,video/x-matroska,video/webm,video/x-msvideo,.mp4,.mov,.mkv,.webm,.avi"
                  style={{ display: 'none' }}
                  onChange={e => {
                    const f = e.target.files?.[0]
                    if (f) setUploadFile(f)
                  }}
                  data-testid="file-upload-input"
                />
                <label htmlFor="direct-video-file-input" style={{ cursor: 'pointer', display: 'block' }}>
                  {uploadFile ? (
                    <div>
                      <div style={{ fontSize: 24, marginBottom: 4 }}>🎬</div>
                      <div style={{ fontWeight: 600, color: 'var(--accent)' }}>{uploadFile.name}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                        {(uploadFile.size / (1024 * 1024)).toFixed(1)} MB · Click to change file
                      </div>
                    </div>
                  ) : (
                    <div>
                      <div style={{ fontSize: 24, marginBottom: 4 }}>📂</div>
                      <div style={{ fontWeight: 600 }}>Click or drag a master video file here to upload</div>
                      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                        Supported formats: MP4, MOV, MKV, WebM, AVI (up to 10GB+)
                      </div>
                    </div>
                  )}
                </label>
              </div>

              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <input
                  className="form-input"
                  style={{ flex: 1 }}
                  placeholder="Custom Video Title (optional)"
                  value={uploadTitle}
                  onChange={e => setUploadTitle(e.target.value)}
                  data-testid="upload-title-input"
                />
                <button
                  className="btn btn-primary"
                  onClick={handleDirectUpload}
                  disabled={uploading || !uploadFile}
                  data-testid="upload-submit-btn"
                >
                  {uploading ? '⏳ Streaming & Ingesting…' : '⬆️ Upload & Ingest Video'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* AI Key Monitor */}
      {data?.api_keys && data.api_keys.length > 0 && (
        <div className="card" data-testid="api-key-monitor">
          <div className="card-header">
            <div className="card-title">🔑 AI API Key Quota Monitor</div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Model</th>
                  <th>RPM</th>
                  <th>TPM</th>
                  <th>Latency</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {data.api_keys.map((k, i) => (
                  <tr key={i}>
                    <td>{k.provider}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{k.model}</td>
                    <td>
                      {k.rpm_used}/{k.rpm_limit}
                      <div className="progress-bar">
                        <div className="progress-fill accent" style={{ width: `${Math.min(100, (k.rpm_used / k.rpm_limit) * 100)}%` }} />
                      </div>
                    </td>
                    <td>
                      {k.tpm_used}/{k.tpm_limit}
                      <div className="progress-bar">
                        <div className="progress-fill accent" style={{ width: `${Math.min(100, (k.tpm_used / k.tpm_limit) * 100)}%` }} />
                      </div>
                    </td>
                    <td>{k.latency_ms > 0 ? `${k.latency_ms}ms` : '—'}</td>
                    <td>{statusBadge(k.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Pipeline Activity */}
      <div className="card" data-testid="pipeline-table-card">
        <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <div className="card-title">🔄 Recent Pipeline Activity</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <select
              className="form-input"
              style={{ width: 'auto', padding: '4px 8px', fontSize: 12 }}
              value={dateFilter}
              onChange={e => setDateFilter(e.target.value)}
            >
              <option value="all">📅 All Dates</option>
              <option value="today">📅 Today</option>
              <option value="yesterday">📅 Yesterday</option>
              <option value="7days">📅 Last 7 Days</option>
              <option value="30days">📅 Last 30 Days</option>
              <option value="custom">📅 Custom Range...</option>
            </select>
            {dateFilter === 'custom' && (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <input
                  type="date"
                  className="form-input"
                  style={{ width: 'auto', padding: '4px 8px', fontSize: 12 }}
                  value={customStart}
                  onChange={e => setCustomStart(e.target.value)}
                />
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>to</span>
                <input
                  type="date"
                  className="form-input"
                  style={{ width: 'auto', padding: '4px 8px', fontSize: 12 }}
                  value={customEnd}
                  onChange={e => setCustomEnd(e.target.value)}
                />
              </div>
            )}
          </div>
        </div>
        {(() => {
          const filteredPipelines = (data?.pipelines ?? []).filter(p => {
            if (dateFilter === 'all') return true
            const pDate = new Date(p.created_at)
            const now = new Date()
            if (dateFilter === 'today') return pDate.toDateString() === now.toDateString()
            if (dateFilter === 'yesterday') {
              const yest = new Date(now)
              yest.setDate(yest.getDate() - 1)
              return pDate.toDateString() === yest.toDateString()
            }
            if (dateFilter === '7days') return pDate >= new Date(now.getTime() - 7 * 86400000)
            if (dateFilter === '30days') return pDate >= new Date(now.getTime() - 30 * 86400000)
            if (dateFilter === 'custom') {
              if (customStart && pDate < new Date(customStart)) return false
              if (customEnd) {
                const eDate = new Date(customEnd)
                eDate.setHours(23, 59, 59, 999)
                if (pDate > eDate) return false
              }
              return true
            }
            return true
          })

          if (filteredPipelines.length === 0) {
            return (
              <div className="empty-state">
                <div className="empty-state-icon">🎥</div>
                <div className="empty-state-title">No matching pipelines</div>
                <p style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 6 }}>No video ingestion activity matches your filters.</p>
              </div>
            )
          }

          return (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Video ID</th>
                    <th>Title</th>
                    <th>Status</th>
                    <th>Started</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredPipelines.map(p => {
                  const isActive = ['DOWNLOADING','TRANSCRIBING','ANALYZING','RENDERING','PUBLISHING'].includes(p.status)
                  const isStale  = ['FAILED','FAILED_NEEDS_REVIEW'].includes(p.status)
                  const isDone   = ['DONE','COMPLETED'].includes(p.status)
                  return (
                    <tr key={p.id} data-testid={`pipeline-row-${p.youtube_video_id}`}>
                      <td><code style={{ fontSize: 11 }}>{p.youtube_video_id}</code></td>
                      <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.title ?? '—'}</td>
                      <td>{statusBadge(p.status)}</td>
                      <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>{new Date(p.created_at).toLocaleString()}</td>
                      <td>
                        <div style={{ display: 'flex', gap: 6 }}>
                          {isActive && (
                            <button className="btn btn-ghost btn-sm" onClick={() => handleCancel(p.id)} data-testid={`cancel-btn-${p.id}`}>
                              ⏹ Cancel
                            </button>
                          )}
                          {isStale && (
                            <button className="btn btn-ghost btn-sm" onClick={() => handleResume(p.youtube_video_id)} data-testid={`resume-btn-${p.id}`}>
                              ⚡ Resume
                            </button>
                          )}
                          {isDone && (
                            <button className="btn btn-ghost btn-sm" onClick={() => handleResume(p.youtube_video_id)} data-testid={`rerun-btn-${p.id}`}>
                              ▶ Re-run
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )
      })()}
    </div>
  </div>
)
}
