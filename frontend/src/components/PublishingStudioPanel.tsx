// Docked Master-Detail Video Publishing Studio Panel (React 19 SPA)
import { useEffect, useState, useRef, useCallback } from 'react'
import { apiGet, apiPost, apiPatch } from '../api'
import type { ToastType } from '../hooks/useToast'

interface ChannelItem {
  id: string
  name: string
  youtube_channel_id: string
  is_authorized?: boolean
  is_connected?: boolean
  status_badge?: string
  handle?: string
  last_verified_at?: string
}

interface QuotaData {
  used_units: number
  remaining_units: number
  daily_limit: number
  pct: number
  cost_per_upload: number
  remaining_uploads: number
}

interface PublishJobItem {
  id: string
  clip_id: string
  title?: string
  status: string
  privacy_status?: string
  schedule_mode?: string
  scheduled_at?: string | null
  attempt_count?: number
  last_error?: string | null
  youtube_video_id?: string | null
  watch_url?: string | null
  created_at?: string | null
}

interface PublishingStudioPanelProps {
  clipId: string | null
  onClose: () => void
  addToast: (msg: string, t?: ToastType) => void
  onClipPublished?: () => void
  onPrevClip?: () => void
  onNextClip?: () => void
  hasPrevClip?: boolean
  hasNextClip?: boolean
}

export default function PublishingStudioPanel({
  clipId,
  onClose,
  addToast,
  onClipPublished,
  onPrevClip,
  onNextClip,
  hasPrevClip,
  hasNextClip
}: PublishingStudioPanelProps) {
  const [loading, setLoading] = useState(false)
  const [channels, setChannels] = useState<ChannelItem[]>([])
  const [selectedChannelId, setSelectedChannelId] = useState<string>('')
  const [quota, setQuota] = useState<QuotaData | null>(null)
  const [jobs, setJobs] = useState<PublishJobItem[]>([])
  const [verifyStatus, setVerifyStatus] = useState<string | null>(null)

  // Draft form state
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [tags, setTags] = useState('')
  const [category, setCategory] = useState('22')
  const [privacy, setPrivacy] = useState<'unlisted' | 'public' | 'private' | 'scheduled'>('unlisted')
  const [scheduledAt, setScheduledAt] = useState('')
  const [madeForKids, setMadeForKids] = useState(false)
  const [dryRun, setDryRun] = useState(false)
  const [draftVersion, setDraftVersion] = useState<number>(1)
  const [autosaveStatus, setAutosaveStatus] = useState<string>('All changes saved')
  const [submitting, setSubmitting] = useState(false)

  const autosaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isInitialLoadRef = useRef<boolean>(true)

  // Load channels, quota telemetry, and jobs
  const loadStudioTelemetry = useCallback(async () => {
    try {
      const overview = await apiGet<any>('/api/publish/overview')
      if (overview?.channels) {
        setChannels(overview.channels)
        if (!selectedChannelId && overview.channels.length > 0) {
          const debateks = overview.channels.find((c: any) => c.name === 'Debateks' || c.youtube_channel_id === 'UCBWVd_s5Gb6silTdeYaMqvA')
          setSelectedChannelId(debateks ? debateks.id : overview.channels[0].id)
        }
      }
      if (overview?.quota) {
        setQuota(overview.quota)
      }
    } catch {
      // Fallback
    }

    try {
      const outboxJobs = await apiGet<PublishJobItem[]>('/api/publish/jobs?limit=20')
      setJobs(outboxJobs || [])
    } catch {
      // Fallback
    }
  }, [selectedChannelId])

  useEffect(() => {
    loadStudioTelemetry()
  }, [loadStudioTelemetry])

  // Load draft metadata when clipId changes
  useEffect(() => {
    if (!clipId) return
    isInitialLoadRef.current = true
    setLoading(true)

    apiGet<any>(`/api/clips/${clipId}/publish-draft`)
      .then(d => {
        if (d) {
          setTitle(d.title || '')
          setDescription(d.description || '')
          if (d.tags) {
            setTags(Array.isArray(d.tags) ? d.tags.join(', ') : d.tags)
          }
          if (d.privacy_status) setPrivacy(d.privacy_status)
          if (d.category_id) setCategory(d.category_id)
          if (typeof d.made_for_kids === 'boolean') setMadeForKids(d.made_for_kids)
          if (d.draft_version) setDraftVersion(d.draft_version)
        }
        setAutosaveStatus('All changes saved')
      })
      .catch(() => {
        // Ignored or new draft
      })
      .finally(() => {
        setLoading(false)
        setTimeout(() => {
          isInitialLoadRef.current = false
        }, 300)
      })
  }, [clipId])

  // Autosave handler debounced 500ms
  const triggerAutosave = useCallback(() => {
    if (!clipId || isInitialLoadRef.current) return
    setAutosaveStatus('Saving...')

    if (autosaveTimerRef.current) {
      clearTimeout(autosaveTimerRef.current)
    }

    autosaveTimerRef.current = setTimeout(async () => {
      try {
        const res = await apiPatch<any>(`/api/clips/${clipId}/publish-draft`, {
          title,
          description,
          tags,
          privacy_status: privacy,
          category_id: category,
          made_for_kids: madeForKids,
          draft_version: draftVersion
        })
        if (res?.draft_version) {
          setDraftVersion(res.draft_version)
        }
        const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        setAutosaveStatus(`Saved at ${timeStr}`)
      } catch {
        setAutosaveStatus('Autosave error')
      }
    }, 500)
  }, [clipId, title, description, tags, privacy, category, madeForKids, draftVersion])

  const flushDraftImmediately = async () => {
    if (!clipId || isInitialLoadRef.current) return
    if (autosaveTimerRef.current) {
      clearTimeout(autosaveTimerRef.current)
      autosaveTimerRef.current = null
    }
    setAutosaveStatus('Saving...')
    try {
      const res = await apiPatch<any>(`/api/clips/${clipId}/publish-draft`, {
        title,
        description,
        tags,
        privacy_status: privacy,
        category_id: category,
        made_for_kids: madeForKids,
        draft_version: draftVersion
      })
      if (res?.draft_version) {
        setDraftVersion(res.draft_version)
      }
      const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      setAutosaveStatus(`Saved at ${timeStr}`)
      addToast('Draft saved successfully', 'info')
    } catch {
      setAutosaveStatus('Save error')
    }
  }

  const handleFieldChange = (setter: () => void) => {
    setter()
    triggerAutosave()
  }

  const applyAiAngle = (angle: 'curiosity' | 'stakes' | 'question' | 'punchy') => {
    const base = title.trim() || 'Shocking Revelation'
    let newTitle = ''
    if (angle === 'curiosity') newTitle = `The Hidden Truth About ${base}`
    else if (angle === 'stakes') newTitle = `Why This Changes Everything: ${base}`
    else if (angle === 'question') newTitle = `Did Anyone Else Notice This in ${base}?`
    else newTitle = `${base.toUpperCase()} (Unreal)`

    setTitle(newTitle.slice(0, 100))
    triggerAutosave()
    addToast(`Applied ${angle} AI hook angle`, 'info')
  }

  const appendPreset = (presetText: string) => {
    setDescription(prev => (prev.trim() ? `${prev.trim()}\n\n${presetText}` : presetText))
    triggerAutosave()
    addToast('Appended preset tags', 'info')
  }

  const handleOpenFolder = async () => {
    if (!clipId) return
    try {
      const res = await apiPost<any>(`/api/clips/${clipId}/open-folder`)
      if (res?.ok) {
        addToast(`Opened containing folder (${res.method_used})`, 'success')
      } else {
        addToast(res?.error || 'Could not open folder', 'error')
      }
    } catch (e: unknown) {
      addToast(`Error opening folder: ${e instanceof Error ? e.message : String(e)}`, 'error')
    }
  }

  const handleConnectChannel = async () => {
    try {
      const res = await apiPost<any>('/api/channels/connect', { channel_id: selectedChannelId })
      if (res?.authorization_url) {
        window.location.href = res.authorization_url
      } else {
        addToast('Failed to generate OAuth authorization URL', 'error')
      }
    } catch (e: unknown) {
      addToast(`OAuth error: ${e instanceof Error ? e.message : String(e)}`, 'error')
    }
  }

  const handleVerifyChannel = async () => {
    if (!selectedChannelId) return
    setVerifyStatus('Verifying credentials...')
    try {
      const res = await apiPost<any>(`/api/channels/${selectedChannelId}/verify`)
      if (res?.ok) {
        setVerifyStatus(`✓ Verified: ${res.actual_title || res.actual_channel_id}`)
        addToast('Channel credentials verified successfully!', 'success')
      } else {
        setVerifyStatus(`✗ Verification failed: ${res.error || 'Needs Auth'}`)
        addToast(`Channel verification failed: ${res.error}`, 'error')
      }
    } catch (e: unknown) {
      setVerifyStatus(`Error: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const handleDisconnectChannel = async () => {
    if (!selectedChannelId) return
    if (!confirm('Disconnect YouTube OAuth credentials for this channel?')) return
    try {
      await apiPost(`/api/channels/${selectedChannelId}/disconnect`)
      addToast('Channel disconnected', 'info')
      loadStudioTelemetry()
    } catch (e: unknown) {
      addToast(`Disconnect failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    }
  }

  const handlePublish = async () => {
    if (!clipId) return
    setSubmitting(true)
    try {
      await flushDraftImmediately()
      const res = await apiPost<any>(`/api/clips/${clipId}/publish`, {
        channel_id: selectedChannelId,
        dry_run: dryRun,
        privacy_status: privacy
      })
      if (res?.status === 'error' || res?.status === 'PAUSED_NEEDS_ATTENTION' || res?.status === 'FAILED') {
        addToast(`Publish warning: ${res.message || res.last_error || 'Publish issue'}`, 'error')
      } else {
        addToast(res?.is_dry_run ? 'Dry-run upload simulation complete!' : 'Short published to YouTube!', 'success')
        if (onClipPublished) onClipPublished()
      }
      loadStudioTelemetry()
    } catch (e: unknown) {
      addToast(`Publish failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setSubmitting(false)
    }
  }

  const handleSchedule = async () => {
    if (!clipId) return
    if (!scheduledAt) {
      addToast('Please select a future release date & time', 'error')
      setPrivacy('scheduled')
      return
    }
    setSubmitting(true)
    try {
      await flushDraftImmediately()
      const isoTime = new Date(scheduledAt).toISOString()
      const res = await apiPost<any>(`/api/clips/${clipId}/schedule`, {
        scheduled_at: isoTime,
        channel_id: selectedChannelId
      })
      addToast(`Short scheduled for ${res.scheduled_publish_at}!`, 'success')
      loadStudioTelemetry()
      if (onClipPublished) onClipPublished()
    } catch (e: unknown) {
      addToast(`Scheduling failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setSubmitting(false)
    }
  }

  const handleRetryJob = async (jobId: string) => {
    try {
      await apiPost(`/api/publish/jobs/${jobId}/retry`)
      addToast('Retry triggered for publishing job', 'info')
      loadStudioTelemetry()
    } catch (e: unknown) {
      addToast(`Retry error: ${e instanceof Error ? e.message : String(e)}`, 'error')
    }
  }

  const currentChannel = channels.find(c => c.id === selectedChannelId)

  return (
    <aside className="publishing-studio-panel card bg-dark border-secondary shadow-lg h-100 d-flex flex-column" data-testid="publishing-studio-panel">
      {/* Studio Header */}
      <div className="card-header bg-black py-2 px-3 d-flex justify-content-between align-items-center border-secondary">
        <div className="d-flex align-items-center gap-2">
          <span className="text-light fw-bold" style={{ fontSize: 14 }}>🚀 Video Publishing Studio</span>
          <span className="badge bg-secondary font-monospace" style={{ fontSize: 11 }}>
            {clipId ? `Clip ${clipId.slice(0, 8)}` : 'No Clip Selected'}
          </span>
        </div>
        <button
          type="button"
          className="btn btn-ghost btn-xs text-light"
          onClick={onClose}
          aria-label="Close Publishing Studio"
        >✕</button>
      </div>

      <div className="card-body p-3 overflow-y-auto flex-grow-1">
        {/* Target Channel Selector */}
        <div className="mb-3 p-2 bg-black rounded border border-secondary">
          <div className="d-flex justify-content-between align-items-center mb-1">
            <span className="text-muted small fw-bold text-uppercase" style={{ fontSize: 11 }}>Target Channel</span>
            <div className="d-flex gap-1">
              <button type="button" className="btn btn-outline-info btn-xs py-0 px-1" style={{ fontSize: 10 }} onClick={handleConnectChannel} title="Connect channel with Google OAuth">🔑 Connect</button>
              <button type="button" className="btn btn-outline-secondary btn-xs py-0 px-1" style={{ fontSize: 10 }} onClick={handleVerifyChannel} title="Verify identity credentials">✓ Verify</button>
              <button type="button" className="btn btn-outline-danger btn-xs py-0 px-1" style={{ fontSize: 10 }} onClick={handleDisconnectChannel} title="Disconnect OAuth">Disconnect</button>
            </div>
          </div>
          <div className="d-flex align-items-center gap-2">
            <select
              className="form-select form-select-sm bg-dark text-light border-secondary"
              value={selectedChannelId}
              onChange={e => setSelectedChannelId(e.target.value)}
              style={{ fontSize: 12 }}
            >
              {channels.map(ch => (
                <option key={ch.id} value={ch.id}>{ch.name}</option>
              ))}
            </select>
            <span
              className={`badge ${currentChannel?.is_authorized || currentChannel?.is_connected ? 'bg-success' : 'bg-danger'}`}
              style={{ fontSize: 11, whiteSpace: 'nowrap' }}
            >
              {currentChannel?.is_authorized || currentChannel?.is_connected ? '🟢 Authorized' : '🔴 Needs Auth'}
            </span>
          </div>
          {verifyStatus && (
            <div className="small mt-1 text-info" style={{ fontSize: 11 }}>{verifyStatus}</div>
          )}
        </div>

        {/* Quota Telemetry */}
        <div className="quota-box p-2 mb-3 rounded bg-black border border-secondary">
          <div className="d-flex justify-content-between align-items-center mb-1">
            <span className="text-light fw-bold" style={{ fontSize: 12 }}>
              YouTube API Quota <small className="text-muted fw-normal" style={{ fontSize: 10 }}>(estimate)</small>
            </span>
            <span className={`badge ${quota && quota.pct > 80 ? 'bg-danger' : quota && quota.pct > 50 ? 'bg-warning text-dark' : 'bg-primary'}`} style={{ fontSize: 10 }}>
              {quota ? `${quota.used_units} / ${quota.daily_limit} units (${quota.pct}%)` : '0 / 10,000 units'}
            </span>
          </div>
          <div className="progress mb-1" style={{ height: 6, backgroundColor: '#2d3748' }}>
            <div
              className={`progress-bar ${quota && quota.pct > 80 ? 'bg-danger' : quota && quota.pct > 50 ? 'bg-warning' : 'bg-info'}`}
              role="progressbar"
              style={{ width: `${quota?.pct ?? 0}%` }}
            />
          </div>
          <div className="d-flex justify-content-between align-items-center text-muted" style={{ fontSize: 10 }}>
            <span>~{quota?.remaining_uploads ?? 6} video uploads remaining today</span>
            <span>1,600 units / upload</span>
          </div>
        </div>

        {/* Video Preview */}
        <div className="mb-3">
          <div className="bg-black rounded p-2 border border-secondary text-center position-relative d-flex align-items-center justify-content-center" style={{ minHeight: 200 }}>
            {clipId ? (
              <video
                key={clipId}
                src={`/api/video/${clipId}`}
                controls
                playsInline
                className="w-100 rounded"
                style={{ maxHeight: 240, aspectRatio: '9/16', objectFit: 'contain' }}
              />
            ) : (
              <div className="p-3 text-muted">
                <div style={{ fontSize: 32 }}>▶️</div>
                <div className="small mt-1">Select a clip from the review queue</div>
              </div>
            )}
          </div>
          <div className="d-flex justify-content-between align-items-center mt-1 px-1">
            <span className="text-muted" style={{ fontSize: 11 }}>Preview</span>
            <div className="d-flex gap-2">
              <a
                href={clipId ? `/api/video/${clipId}?download=1` : '#'}
                className="btn btn-link text-info p-0 text-decoration-none"
                style={{ fontSize: 11 }}
                download
              >
                ⬇️ Download
              </a>
              <button
                type="button"
                className="btn btn-link text-info p-0 text-decoration-none"
                style={{ fontSize: 11 }}
                onClick={handleOpenFolder}
                disabled={!clipId}
              >
                📁 Open in Explorer
              </button>
            </div>
          </div>
        </div>

        {/* Title & AI Angles */}
        <div className="mb-3">
          <div className="d-flex justify-content-between align-items-center mb-1">
            <label className="form-label text-light fw-bold m-0" style={{ fontSize: 12 }}>Title (Max 100)</label>
            <div className="d-flex align-items-center gap-2">
              <span className="text-muted" style={{ fontSize: 10 }}>{autosaveStatus}</span>
              <span className="text-muted" style={{ fontSize: 10 }}>{title.length}/100</span>
            </div>
          </div>
          <input
            type="text"
            className="form-control form-control-sm bg-black text-light border-secondary"
            value={title}
            maxLength={100}
            placeholder="Enter title..."
            onChange={e => handleFieldChange(() => setTitle(e.target.value))}
            disabled={!clipId || loading}
          />
          <div className="d-flex flex-wrap gap-1 mt-1">
            <span className="text-muted align-self-center" style={{ fontSize: 10 }}>AI Angles:</span>
            <button type="button" className="btn btn-outline-warning btn-xs py-0 px-1" style={{ fontSize: 10 }} onClick={() => applyAiAngle('curiosity')}>🔥 Curiosity</button>
            <button type="button" className="btn btn-outline-danger btn-xs py-0 px-1" style={{ fontSize: 10 }} onClick={() => applyAiAngle('stakes')}>⚡ Stakes</button>
            <button type="button" className="btn btn-outline-info btn-xs py-0 px-1" style={{ fontSize: 10 }} onClick={() => applyAiAngle('question')}>❓ Question</button>
            <button type="button" className="btn btn-outline-success btn-xs py-0 px-1" style={{ fontSize: 10 }} onClick={() => applyAiAngle('punchy')}>🎯 Punchy</button>
          </div>
        </div>

        {/* Description & Presets */}
        <div className="mb-3">
          <div className="d-flex justify-content-between align-items-center mb-1">
            <label className="form-label text-light fw-bold m-0" style={{ fontSize: 12 }}>Description & Hashtags</label>
            <div className="d-flex gap-1">
              <button type="button" className="btn btn-outline-secondary btn-xs py-0 px-1" style={{ fontSize: 9 }} onClick={() => appendPreset('#Shorts #Viral #Trending')}>+ Viral Tags</button>
              <button type="button" className="btn btn-outline-secondary btn-xs py-0 px-1" style={{ fontSize: 9 }} onClick={() => appendPreset('Subscribe for more daily clips!')}>+ CTA</button>
            </div>
          </div>
          <textarea
            rows={3}
            className="form-control form-control-sm bg-black text-light border-secondary"
            value={description}
            placeholder="Description..."
            onChange={e => handleFieldChange(() => setDescription(e.target.value))}
            disabled={!clipId || loading}
          />
        </div>

        {/* Tags */}
        <div className="mb-3">
          <label className="form-label text-light fw-bold mb-1" style={{ fontSize: 12 }}>Tags</label>
          <input
            type="text"
            className="form-control form-control-sm bg-black text-light border-secondary"
            value={tags}
            placeholder="e.g. shorts, viral, podcast"
            onChange={e => handleFieldChange(() => setTags(e.target.value))}
            disabled={!clipId || loading}
          />
        </div>

        {/* Category & Privacy */}
        <div className="row g-2 mb-3">
          <div className="col-6">
            <label className="form-label text-light fw-bold mb-1" style={{ fontSize: 11 }}>Category</label>
            <select
              className="form-select form-select-sm bg-black text-light border-secondary"
              value={category}
              onChange={e => handleFieldChange(() => setCategory(e.target.value))}
              disabled={!clipId || loading}
            >
              <option value="22">People & Blogs (22)</option>
              <option value="24">Entertainment (24)</option>
              <option value="20">Gaming (20)</option>
              <option value="28">Science & Tech (28)</option>
              <option value="27">Education (27)</option>
              <option value="17">Sports (17)</option>
            </select>
          </div>
          <div className="col-6">
            <label className="form-label text-light fw-bold mb-1" style={{ fontSize: 11 }}>Privacy</label>
            <select
              className="form-select form-select-sm bg-black text-light border-secondary"
              value={privacy}
              onChange={e => handleFieldChange(() => setPrivacy(e.target.value as any))}
              disabled={!clipId || loading}
            >
              <option value="unlisted">Unlisted 🛡️ (Safe)</option>
              <option value="public">Public 🌍 (Instant)</option>
              <option value="private">Private 🔒</option>
              <option value="scheduled">Scheduled 📅</option>
            </select>
          </div>
        </div>

        {/* Schedule Date-Time Picker */}
        {privacy === 'scheduled' && (
          <div className="mb-3 p-2 bg-black rounded border border-secondary">
            <label className="form-label text-light fw-bold mb-1" style={{ fontSize: 11 }}>Release Date & Time (UTC)</label>
            <input
              type="datetime-local"
              className="form-control form-control-sm bg-dark text-light border-secondary"
              value={scheduledAt}
              onChange={e => setScheduledAt(e.target.value)}
            />
          </div>
        )}

        {/* Kids & Dry-Run */}
        <div className="d-flex justify-content-between align-items-center mb-3">
          <div className="form-check form-switch">
            <input
              className="form-check-input"
              type="checkbox"
              id="studioReactKids"
              checked={madeForKids}
              onChange={e => handleFieldChange(() => setMadeForKids(e.target.checked))}
            />
            <label className="form-check-label text-muted small" htmlFor="studioReactKids" style={{ fontSize: 11 }}>Made for Kids</label>
          </div>
          <div className="form-check form-switch">
            <input
              className="form-check-input"
              type="checkbox"
              id="studioReactDryRun"
              checked={dryRun}
              onChange={e => setDryRun(e.target.checked)}
            />
            <label className="form-check-label text-warning small" htmlFor="studioReactDryRun" style={{ fontSize: 11 }}>Dry-Run Mode</label>
          </div>
        </div>

        {/* Main CTA Buttons */}
        <div className="d-flex gap-2 mb-3">
          <button
            type="button"
            className="btn btn-success btn-sm w-100 fw-bold"
            disabled={!clipId || submitting || loading}
            onClick={handlePublish}
          >
            {submitting ? 'Publishing...' : '🚀 Publish to YouTube'}
          </button>
          <button
            type="button"
            className="btn btn-outline-info btn-sm fw-bold px-3"
            disabled={!clipId || submitting || loading}
            onClick={handleSchedule}
          >
            📅 Schedule
          </button>
        </div>

        {/* Outbox Jobs */}
        <div className="border-top border-secondary pt-2 mt-2">
          <div className="d-flex justify-content-between align-items-center mb-1">
            <span className="text-light fw-bold" style={{ fontSize: 12 }}>📋 Publishing Outbox</span>
            <button
              type="button"
              className="btn btn-ghost btn-xs text-muted"
              style={{ fontSize: 10 }}
              onClick={loadStudioTelemetry}
            >↻ Refresh</button>
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: 160 }}>
            {jobs.length === 0 ? (
              <div className="text-muted small text-center py-2" style={{ fontSize: 11 }}>No active publishing jobs</div>
            ) : (
              <table className="table table-dark table-sm m-0" style={{ fontSize: 11 }}>
                <tbody>
                  {jobs.map(job => (
                    <tr key={job.id}>
                      <td className="text-truncate" style={{ maxWidth: 120 }}>{job.title || job.clip_id.slice(0, 8)}</td>
                      <td>
                        <span className={`badge ${job.status === 'PUBLISHED' ? 'bg-success' : job.status === 'UPLOADING' ? 'bg-info' : job.status === 'SCHEDULED' ? 'bg-primary' : job.status === 'PAUSED_NEEDS_ATTENTION' ? 'bg-warning text-dark' : 'bg-danger'}`} style={{ fontSize: 9 }}>
                          {job.status}
                        </span>
                      </td>
                      <td className="text-end">
                        {job.watch_url ? (
                          <a href={job.watch_url} target="_blank" rel="noopener noreferrer" className="text-info text-decoration-none">📺</a>
                        ) : job.status === 'FAILED' || job.status === 'PAUSED_NEEDS_ATTENTION' ? (
                          <button type="button" className="btn btn-outline-warning btn-xs py-0 px-1" style={{ fontSize: 9 }} onClick={() => handleRetryJob(job.id)}>↻</button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </aside>
  )
}
