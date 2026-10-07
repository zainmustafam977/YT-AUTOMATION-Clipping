// Channels Page — channel list with enable/disable toggles & Setup Wizard
import { useEffect, useState, useCallback } from 'react'
import { apiGet, apiPost } from '../api'
import type { ToastType } from '../hooks/useToast'

interface Channel {
  id: string
  name: string
  youtube_channel_id: string
  watch_enabled?: boolean
  human_review_required?: boolean
  is_active?: boolean
  clip_count?: number
  published_count?: number
  is_connected?: boolean
  handle?: string
  avatar_url?: string
  last_verified_at?: string
}

interface PendingMismatch {
  id: string
  intended_channel_id: string
  intended_channel_name: string
  authorized_channel_id: string
  authorized_title: string
  authorized_handle?: string
  authorized_avatar_url?: string
  created_at?: string
  expires_at?: string
}

interface ChannelSetup {
  privacy_status: string
  made_for_kids: boolean
  category_id: string
  default_tags: string[] | string
  default_language: string
  license: string
  daily_upload_quota: number
  preferred_publish_hour_utc: number
  auto_publish: boolean
  publish_target_channel_id?: string
  oauth_token_json?: string
}

interface ChannelVideo {
  video_id: string
  title: string
  published_at: string
  published_relative: string
  duration_sec?: number | null
  duration_formatted?: string | null
  view_count?: number | null
  views_formatted?: string | null
  youtube_url: string
  thumbnail_url: string
  is_ingested: boolean
  status?: string | null
  status_label?: string | null
  clips_count?: number
  queue_url?: string | null
}

export default function ChannelsPage({ addToast }: { addToast: (msg: string, t?: ToastType) => void }) {
  const [channels, setChannels] = useState<Channel[]>([])
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState<string | null>(null)
  const [setupChannel, setSetupChannel] = useState<Channel | null>(null)
  const [setupData, setSetupData] = useState<ChannelSetup | null>(null)
  const [savingSetup, setSavingSetup] = useState(false)

  // Channel Videos Drawer/Modal State
  const [videosChannel, setVideosChannel] = useState<Channel | null>(null)
  const [channelVideos, setChannelVideos] = useState<ChannelVideo[]>([])
  const [loadingVideos, setLoadingVideos] = useState(false)
  const [videosQuery, setVideosQuery] = useState('')
  const [captionPreset, setCaptionPreset] = useState('hormozi_viral')
  const [ingestingId, setIngestingId] = useState<string | null>(null)

  // Mismatch & OAuth State
  const [mismatches, setMismatches] = useState<PendingMismatch[]>([])
  const [activeMismatch, setActiveMismatch] = useState<PendingMismatch | null>(null)
  const [resolvingMismatch, setResolvingMismatch] = useState(false)

  const loadMismatches = useCallback(async () => {
    try {
      const data = await apiGet<PendingMismatch[]>('/api/channels/mismatches/pending')
      if (Array.isArray(data) && data.length > 0) {
        setMismatches(data)
        setActiveMismatch(data[0])
      } else {
        setMismatches([])
        setActiveMismatch(null)
      }
    } catch {}
  }, [])

  const resolveMismatch = async (action: 'rebind' | 'create_new' | 'cancel') => {
    if (!activeMismatch) return
    setResolvingMismatch(true)
    try {
      const res = await apiPost<any>('/api/channels/connect/resolve', {
        mismatch_id: activeMismatch.id,
        action
      })
      addToast(res?.message || `Mismatch resolved via ${action}`, 'success')
      await loadMismatches()
      await loadChannels()
    } catch (err: unknown) {
      addToast(`Resolve failed: ${err instanceof Error ? err.message : String(err)}`, 'error')
    } finally {
      setResolvingMismatch(false)
    }
  }

  const connectOAuth = async (ch: Channel) => {
    try {
      const res = await apiPost<any>('/api/channels/connect', { channel_id: ch.id })
      if (res?.authorization_url) {
        window.location.href = res.authorization_url
      } else {
        addToast('Failed to generate OAuth authorization URL', 'error')
      }
    } catch (err: unknown) {
      addToast(`Connect failed: ${err instanceof Error ? err.message : String(err)}`, 'error')
    }
  }

  const verifyOAuth = async (ch: Channel) => {
    try {
      const res = await apiPost<any>(`/api/channels/${ch.id}/verify`)
      if (res?.ok) {
        addToast(`✓ Verified: ${res.actual_title || res.actual_channel_id}`, 'success')
      } else {
        addToast(`Verification failed: ${res?.error || 'Needs Auth'}`, 'error')
      }
      await loadChannels()
    } catch (err: unknown) {
      addToast(`Verify error: ${err instanceof Error ? err.message : String(err)}`, 'error')
    }
  }

  const disconnectOAuth = async (ch: Channel) => {
    if (!confirm(`Disconnect YouTube OAuth token for ${ch.name}?`)) return
    try {
      await apiPost(`/api/channels/${ch.id}/disconnect`)
      addToast(`Disconnected ${ch.name}`, 'info')
      await loadChannels()
    } catch (err: unknown) {
      addToast(`Disconnect failed: ${err instanceof Error ? err.message : String(err)}`, 'error')
    }
  }

  const loadChannels = useCallback(async () => {
    try {
      const res = await apiGet<{ channels?: Channel[] } | Channel[]>('/api/channels')
      const list = Array.isArray(res) ? res : (res?.channels ?? [])
      setChannels(list.map(c => ({
        ...c,
        is_active: c.is_active !== undefined ? c.is_active : (c.watch_enabled !== undefined ? c.watch_enabled : true),
        clip_count: c.clip_count ?? 0,
        published_count: c.published_count ?? 0
      })))
    } catch {
      // Graceful fallback
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadChannels()
    loadMismatches()
  }, [loadChannels, loadMismatches])

  const toggle = async (ch: Channel) => {
    setToggling(ch.id)
    try {
      await apiPost(`/channels/${ch.id}/toggle-watch`)
      setChannels(prev => prev.map(c => c.id === ch.id ? { ...c, is_active: !c.is_active } : c))
      addToast(`Channel ${ch.name} toggled!`, 'success')
    } catch {
      addToast('Toggle failed', 'error')
    } finally {
      setToggling(null)
    }
  }

  const openSetup = async (ch: Channel) => {
    setSetupChannel(ch)
    try {
      const res = await apiGet<any>(`/api/channels/${ch.id}/setup`)
      const d = res?.upload_defaults || {}
      setSetupData({
        privacy_status: d.privacy_status || 'unlisted',
        made_for_kids: Boolean(d.made_for_kids),
        category_id: String(d.category_id || '22'),
        default_tags: Array.isArray(d.default_tags) ? d.default_tags.join(', ') : (d.default_tags || '#Shorts, #Viral'),
        default_language: d.default_language || 'en',
        license: d.license || 'youtube',
        daily_upload_quota: d.daily_upload_quota || 4,
        preferred_publish_hour_utc: d.preferred_publish_hour_utc !== undefined ? d.preferred_publish_hour_utc : 18,
        auto_publish: Boolean(d.auto_publish),
        publish_target_channel_id: res?.publish_target_channel_id || '',
        oauth_token_json: ''
      })
    } catch (e: unknown) {
      addToast(`Could not load channel setup: ${e instanceof Error ? e.message : String(e)}`, 'error')
    }
  }

  const saveSetup = async () => {
    if (!setupChannel || !setupData) return
    setSavingSetup(true)
    try {
      const tagsList = typeof setupData.default_tags === 'string'
        ? setupData.default_tags.split(',').map(t => t.trim()).filter(Boolean)
        : setupData.default_tags

      await apiPost(`/api/channels/${setupChannel.id}/setup`, {
        ...setupData,
        default_tags: tagsList
      })
      addToast(`Upload settings saved for ${setupChannel.name}!`, 'success')
      setSetupChannel(null)
    } catch (e: unknown) {
      addToast(`Save failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setSavingSetup(false)
    }
  }

  const openVideos = async (ch: Channel) => {
    setVideosChannel(ch)
    setLoadingVideos(true)
    setVideosQuery('')
    try {
      const res = await apiGet<{ videos?: ChannelVideo[] }>(`/api/channels/${ch.id}/videos`)
      setChannelVideos(res?.videos || [])
    } catch (e: unknown) {
      addToast(`Failed to load videos: ${e instanceof Error ? e.message : String(e)}`, 'error')
      setChannelVideos([])
    } finally {
      setLoadingVideos(false)
    }
  }

  const ingestVideo = async (videoId: string) => {
    if (!videosChannel) return
    setIngestingId(videoId)
    try {
      const tokenMeta = document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement | null
      const csrfToken = tokenMeta ? tokenMeta.content : ''
      const fd = new FormData()
      fd.append('video_input', videoId)
      fd.append('channel_id', videosChannel.id)
      fd.append('caption_preset', captionPreset)
      if (csrfToken) fd.append('csrf_token', csrfToken)

      const res = await fetch('/process-video', {
        method: 'POST',
        headers: csrfToken ? { 'X-CSRF-Token': csrfToken } : {},
        body: fd
      })
      if (!res.ok) {
        const txt = await res.text()
        throw new Error(txt || `HTTP ${res.status}`)
      }
      addToast(`Started processing video ${videoId}!`, 'success')
      setChannelVideos(prev =>
        prev.map(v =>
          v.video_id === videoId
            ? {
                ...v,
                is_ingested: true,
                status: 'DETECTED',
                status_label: 'DETECTED',
                clips_count: 0
              }
            : v
        )
      )
    } catch (e: unknown) {
      addToast(`Ingest failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setIngestingId(null)
    }
  }

  const [showGuide, setShowGuide] = useState(false)
  const [showRestoreModal, setShowRestoreModal] = useState(false)
  const [restoreJson, setRestoreJson] = useState('')
  const [updateExisting, setUpdateExisting] = useState(true)
  const [restoring, setRestoring] = useState(false)

  const exportChannels = () => {
    window.location.href = '/api/channels/export'
    addToast('Downloading channels backup...', 'info')
  }

  const handleRestoreFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (event) => {
      setRestoreJson(event.target?.result as string || '')
    }
    reader.readAsText(file)
  }

  const executeRestoreChannels = async () => {
    if (!restoreJson.trim()) {
      addToast('Please choose a file or paste JSON data', 'error')
      return
    }
    setRestoring(true)
    try {
      const parsed = JSON.parse(restoreJson.trim())
      const res = await apiPost<{ imported: number; updated: number; skipped: number }>('/api/channels/import', {
        channels: parsed.channels || (Array.isArray(parsed) ? parsed : [parsed]),
        update_existing: updateExisting
      })
      addToast(`Restored channels! Added: ${res.imported}, Updated: ${res.updated}, Skipped: ${res.skipped}`, 'success')
      setShowRestoreModal(false)
      setRestoreJson('')
      loadChannels()
    } catch (err: unknown) {
      addToast(`Restore failed: ${err instanceof Error ? err.message : String(err)}`, 'error')
    } finally {
      setRestoring(false)
    }
  }

  const focusField = (id: string) => {
    const el = document.getElementById(id)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.focus()
      el.style.outline = '2px solid #38bdf8'
      setTimeout(() => { el.style.outline = 'none' }, 2000)
    }
  }

  if (loading) return <div className="spinner" data-testid="channels-loading" />

  return (
    <div data-testid="channels-page">
      <div className="card">
        <div className="card-header">
          <div className="card-title">📺 Channel Manager</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-ghost btn-sm" onClick={() => setShowGuide(true)} data-testid="open-guide-btn">
              📘 Setup Guide
            </button>
            <button className="btn btn-ghost btn-sm" onClick={exportChannels} data-testid="export-channels-btn">
              📥 Export Channels
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setShowRestoreModal(true)} data-testid="restore-channels-btn">
              📤 Restore Channels
            </button>
            <a href="/channels" className="btn btn-primary btn-sm" data-testid="manage-channels-link">
              ⚙️ Full Channel Manager
            </a>
          </div>
        </div>
        {/* Mismatch Alert Banner */}
        {activeMismatch && (
          <div className="alert alert-danger" style={{ margin: '16px 20px', padding: '14px 18px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid #ef4444', borderRadius: 'var(--radius)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
              <div>
                <div style={{ fontWeight: 700, color: '#ef4444', fontSize: 14 }}>
                  ⚠️ YouTube Channel Mismatch Detected
                </div>
                <div style={{ fontSize: 12, marginTop: 4, color: 'var(--text)' }}>
                  Intended Engine Channel: <strong>{activeMismatch.intended_channel_name}</strong> (<code>{activeMismatch.intended_channel_id}</code>)<br />
                  Google Account Authorized: <strong style={{ color: 'var(--warning)' }}>{activeMismatch.authorized_title || activeMismatch.authorized_handle}</strong> (<code>{activeMismatch.authorized_channel_id}</code>)
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button
                  className="btn btn-warning btn-sm"
                  disabled={resolvingMismatch}
                  onClick={() => resolveMismatch('rebind')}
                  title="Re-bind the target channel to the detected YouTube channel"
                >
                  1. Update Binding (Rebind)
                </button>
                <button
                  className="btn btn-outline-info btn-sm"
                  disabled={resolvingMismatch}
                  onClick={() => resolveMismatch('create_new')}
                  title="Register the detected channel as a new channel"
                >
                  2. Create New Channel
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={resolvingMismatch}
                  onClick={() => resolveMismatch('cancel')}
                  title="Discard token"
                >
                  3. Cancel
                </button>
              </div>
            </div>
          </div>
        )}

        {channels.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">📺</div>
            <div className="empty-state-title">No channels configured yet</div>
            <p style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 6, marginBottom: 16 }}>
              Configure YouTube channels to enable automated drip-feed uploads and WebSub monitoring.
            </p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
              <button className="btn btn-ghost" onClick={() => setShowGuide(true)}>
                📘 Read Setup Guide
              </button>
              <a href="/channels" className="btn btn-primary" data-testid="add-channel-cta">
                + Open Channel Setup Wizard
              </a>
            </div>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Channel Name</th>
                  <th>YouTube ID</th>
                  <th>OAuth Connection</th>
                  <th>Clips</th>
                  <th>Published</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {channels.map(ch => (
                  <tr key={ch.id} data-testid={`channel-row-${ch.id}`}>
                    <td style={{ fontWeight: 600 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        {ch.avatar_url && (
                          <img src={ch.avatar_url} alt="" style={{ width: 24, height: 24, borderRadius: '50%' }} />
                        )}
                        <div>
                          <div>{ch.name}</div>
                          {ch.handle && <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{ch.handle}</div>}
                        </div>
                      </div>
                    </td>
                    <td><code style={{ fontSize: 11 }}>{ch.youtube_channel_id || '—'}</code></td>
                    <td>
                      <span className={`badge ${ch.is_connected ? 'badge-success' : 'badge-danger'}`} style={{ fontSize: 11 }}>
                        {ch.is_connected ? '🟢 Authorized' : '🔴 Needs Auth'}
                      </span>
                    </td>
                    <td>{ch.clip_count}</td>
                    <td>{ch.published_count}</td>
                    <td>
                      <span className={`badge badge-${ch.is_active ? 'success' : 'muted'}`}>
                        {ch.is_active ? '● Active' : '○ Paused'}
                      </span>
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => connectOAuth(ch)}
                          title="Authorize or Reconnect Channel via Google OAuth"
                          data-testid={`connect-btn-${ch.id}`}
                        >
                          🔑 {ch.is_connected ? 'Reconnect' : 'Connect'}
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => verifyOAuth(ch)}
                          title="Pre-upload 1-unit identity verification"
                          data-testid={`verify-btn-${ch.id}`}
                        >
                          ✓ Verify
                        </button>
                        {ch.is_connected && (
                          <button
                            className="btn btn-ghost btn-sm"
                            style={{ color: '#ef4444' }}
                            onClick={() => disconnectOAuth(ch)}
                            title="Disconnect OAuth credentials"
                            data-testid={`disconnect-btn-${ch.id}`}
                          >
                            Disconnect
                          </button>
                        )}
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => openVideos(ch)}
                          data-testid={`videos-btn-${ch.id}`}
                          title="Explore & Ingest Channel Videos"
                        >
                          📺 Videos
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => openSetup(ch)}
                          data-testid={`setup-btn-${ch.id}`}
                          title="Configure Upload Defaults"
                        >
                          ⚙️ Setup
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => toggle(ch)}
                          disabled={toggling === ch.id}
                          data-testid={`toggle-btn-${ch.id}`}
                        >
                          {ch.is_active ? '⏸ Pause' : '▶ Enable'}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Channel Setup Modal with Jump Links */}
      {setupChannel && setupData && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.85)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
            padding: 16,
          }}
          data-testid="channel-setup-modal"
        >
          <div
            style={{
              background: '#181824',
              border: '1px solid rgba(255,255,255,0.15)',
              borderRadius: 8,
              width: '100%',
              maxWidth: 680,
              maxHeight: '90vh',
              overflowY: 'auto',
              padding: 20,
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h4 style={{ margin: 0, fontSize: 16, color: 'var(--accent)' }}>
                ⚙️ Channel Upload Defaults: {setupChannel.name}
              </h4>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setSetupChannel(null)}
              >✕</button>
            </div>

            {/* Quick Jumps Banner */}
            <div style={{ background: 'rgba(56, 189, 248, 0.08)', border: '1px solid rgba(56, 189, 248, 0.3)', padding: 12, borderRadius: 6 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#38bdf8', marginBottom: 6 }}>
                🚀 Quick Field Jump Links:
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                <button type="button" className="btn btn-ghost btn-sm" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => focusField('field_privacy')}>
                  🎯 Privacy
                </button>
                <button type="button" className="btn btn-ghost btn-sm" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => focusField('field_category')}>
                  📂 Category
                </button>
                <button type="button" className="btn btn-ghost btn-sm" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => focusField('field_tags')}>
                  🏷️ Tags
                </button>
                <button type="button" className="btn btn-ghost btn-sm" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => focusField('field_quota')}>
                  📊 Daily Quota
                </button>
                <button type="button" className="btn btn-ghost btn-sm" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => focusField('field_peak_hour')}>
                  ⏰ Peak Hour (UTC)
                </button>
                <button type="button" className="btn btn-ghost btn-sm" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => focusField('field_target_id')}>
                  🆔 Target ID
                </button>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <div>
                <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>Default Privacy</label>
                <select
                  id="field_privacy"
                  className="form-input"
                  value={setupData.privacy_status}
                  onChange={e => setSetupData({ ...setupData, privacy_status: e.target.value })}
                >
                  <option value="unlisted">Unlisted</option>
                  <option value="public">Public</option>
                  <option value="private">Private</option>
                </select>
              </div>
              <div>
                <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>YouTube Category</label>
                <select
                  id="field_category"
                  className="form-input"
                  value={setupData.category_id}
                  onChange={e => setSetupData({ ...setupData, category_id: e.target.value })}
                >
                  <option value="24">Entertainment (24)</option>
                  <option value="22">People & Blogs (22)</option>
                  <option value="20">Gaming (20)</option>
                  <option value="23">Comedy (23)</option>
                  <option value="27">Education (27)</option>
                  <option value="26">Howto & Style (26)</option>
                  <option value="28">Science & Tech (28)</option>
                </select>
              </div>
            </div>

            <div style={{ display: 'flex', gap: 20, background: 'rgba(255,255,255,0.03)', padding: 10, borderRadius: 6 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text)' }}>
                <input
                  type="checkbox"
                  checked={setupData.auto_publish}
                  onChange={e => setSetupData({ ...setupData, auto_publish: e.target.checked })}
                />
                ⚡ Auto-Publish (Skip Review)
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text)' }}>
                <input
                  type="checkbox"
                  checked={setupData.made_for_kids}
                  onChange={e => setSetupData({ ...setupData, made_for_kids: e.target.checked })}
                />
                👶 Made for Kids (COPPA)
              </label>
            </div>

            <div>
              <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>Default Tags (Comma-separated)</label>
              <input
                id="field_tags"
                className="form-input"
                value={typeof setupData.default_tags === 'string' ? setupData.default_tags : setupData.default_tags.join(', ')}
                onChange={e => setSetupData({ ...setupData, default_tags: e.target.value })}
                placeholder="#Shorts, #Viral, #Trending"
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
              <div>
                <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>Daily Quota</label>
                <input
                  id="field_quota"
                  type="number"
                  className="form-input"
                  min="1"
                  max="50"
                  value={setupData.daily_upload_quota}
                  onChange={e => setSetupData({ ...setupData, daily_upload_quota: Number(e.target.value) })}
                />
              </div>
              <div>
                <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>Peak Hour (UTC)</label>
                <input
                  id="field_peak_hour"
                  type="number"
                  className="form-input"
                  min="0"
                  max="23"
                  value={setupData.preferred_publish_hour_utc}
                  onChange={e => setSetupData({ ...setupData, preferred_publish_hour_utc: Number(e.target.value) })}
                />
              </div>
              <div>
                <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>Default Language</label>
                <input
                  className="form-input"
                  value={setupData.default_language}
                  onChange={e => setSetupData({ ...setupData, default_language: e.target.value })}
                />
              </div>
            </div>

            <div>
              <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>Target Channel ID (Optional Override)</label>
              <input
                id="field_target_id"
                className="form-input"
                value={setupData.publish_target_channel_id || ''}
                onChange={e => setSetupData({ ...setupData, publish_target_channel_id: e.target.value })}
                placeholder="Leave blank to use default YouTube client"
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 10 }}>
              <button className="btn btn-ghost" onClick={() => setSetupChannel(null)}>Cancel</button>
              <button
                className="btn btn-primary"
                onClick={saveSetup}
                disabled={savingSetup}
              >
                {savingSetup ? '💾 Saving…' : '💾 Save Defaults'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Channel Restore Modal */}
      {showRestoreModal && (
        <div style={{
          position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.85)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000, padding: 16
        }}>
          <div style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8,
            width: '90%', maxWidth: 500, padding: 20
          }}>
            <h3 style={{ margin: '0 0 10px', color: '#f59e0b', fontSize: 18 }}>
              📤 Restore Channels Backup
            </h3>
            <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 14 }}>
              Select a <code>channels_backup.json</code> file or paste JSON data. Channels are automatically upserted with zero database collisions.
            </p>

            <div style={{ marginBottom: 14 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
                Choose Backup File:
              </label>
              <input
                type="file"
                accept=".json,application/json"
                className="form-input"
                onChange={handleRestoreFile}
              />
            </div>

            <div style={{ marginBottom: 14 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
                Or Paste JSON:
              </label>
              <textarea
                className="form-input"
                style={{ fontFamily: 'monospace', fontSize: 12, minHeight: 90 }}
                placeholder='{"channels": [...]}'
                value={restoreJson}
                onChange={e => setRestoreJson(e.target.value)}
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={updateExisting}
                  onChange={e => setUpdateExisting(e.target.checked)}
                />
                <span>Update existing channels if already registered</span>
              </label>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button className="btn btn-ghost" onClick={() => setShowRestoreModal(false)} disabled={restoring}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                style={{ background: '#f59e0b', borderColor: '#f59e0b' }}
                onClick={executeRestoreChannels}
                disabled={restoring}
                data-testid="execute-restore-channels-btn"
              >
                {restoring ? '⏳ Restoring…' : 'Restore Channels'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Dedicated Setup Guide Modal */}
      {showGuide && (
        <ChannelSetupGuideModal onClose={() => setShowGuide(false)} />
      )}

      {/* Channel Videos Modal */}
      {videosChannel && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0,0,0,0.85)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
            padding: 16,
          }}
          data-testid="channel-videos-modal"
        >
          <div
            style={{
              background: '#181824',
              border: '1px solid rgba(255,255,255,0.15)',
              borderRadius: 8,
              width: '100%',
              maxWidth: 680,
              maxHeight: '90vh',
              overflowY: 'auto',
              padding: 20,
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h4 style={{ margin: 0, fontSize: 16, color: '#38bdf8' }}>
                📺 Channel Videos: {videosChannel.name}
              </h4>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setVideosChannel(null)}
                data-testid="close-videos-modal"
              >✕</button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, alignItems: 'center' }}>
              <input
                type="text"
                className="form-input"
                placeholder="🔍 Search videos by title or ID..."
                value={videosQuery}
                onChange={e => setVideosQuery(e.target.value)}
                data-testid="videos-search-input"
              />
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <label style={{ fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>Caption Style:</label>
                <select
                  className="form-input"
                  style={{ width: 'auto', fontSize: 12, padding: '4px 8px' }}
                  value={captionPreset}
                  onChange={e => setCaptionPreset(e.target.value)}
                  data-testid="caption-preset-select"
                >
                  <option value="hormozi_viral">🔥 Hormozi Viral</option>
                  <option value="mrbeast_impact">⚡ MrBeast Impact</option>
                  <option value="cyberpunk_neon">🌆 Cyberpunk Neon</option>
                  <option value="minimalist_editorial">✨ Minimalist Editorial</option>
                  <option value="high_contrast_box">⬛ High Contrast Box</option>
                </select>
              </div>
            </div>

            {loadingVideos ? (
              <div style={{ textAlign: 'center', padding: '40px 0' }}>
                <div className="spinner" style={{ margin: '0 auto' }} />
                <div style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 10 }}>
                  Fetching public videos from channel...
                </div>
              </div>
            ) : channelVideos.filter(v => !videosQuery.trim() || v.title?.toLowerCase().includes(videosQuery.toLowerCase().trim()) || v.video_id.toLowerCase().includes(videosQuery.toLowerCase().trim())).length === 0 ? (
              <div style={{ textAlign: 'center', padding: '30px 0', color: 'var(--text-muted)', fontSize: 13 }}>
                No videos found matching your criteria.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {channelVideos
                  .filter(v => !videosQuery.trim() || v.title?.toLowerCase().includes(videosQuery.toLowerCase().trim()) || v.video_id.toLowerCase().includes(videosQuery.toLowerCase().trim()))
                  .map(v => (
                    <div
                      key={v.video_id}
                      data-testid={`channel-video-card-${v.video_id}`}
                      style={{
                        background: 'rgba(0,0,0,0.4)',
                        border: '1px solid rgba(255,255,255,0.1)',
                        borderRadius: 6,
                        padding: 10,
                        display: 'flex',
                        gap: 12,
                      }}
                    >
                      <div style={{ position: 'relative', width: 120, height: 68, flexShrink: 0, borderRadius: 4, overflow: 'hidden', background: '#0a0a10' }}>
                        <img
                          src={v.thumbnail_url}
                          alt={v.title}
                          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                          onError={(e) => {
                            const target = e.currentTarget
                            if (!target.src.includes('/api/thumbnails/')) {
                              target.src = `/api/thumbnails/${v.video_id}`
                            }
                          }}
                        />
                        {v.duration_formatted && (
                          <span
                            style={{
                              position: 'absolute',
                              bottom: 3,
                              right: 3,
                              background: 'rgba(0,0,0,0.85)',
                              padding: '1px 5px',
                              fontSize: 10,
                              borderRadius: 3,
                              fontWeight: 'bold',
                              fontFamily: 'monospace',
                              color: '#fff',
                              border: '1px solid rgba(255,255,255,0.2)',
                            }}
                          >
                            {v.duration_formatted}
                          </span>
                        )}
                      </div>

                      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                        <div>
                          <a
                            href={v.youtube_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              fontWeight: 600,
                              fontSize: 13,
                              color: '#f8fafc',
                              textDecoration: 'none',
                              display: '-webkit-box',
                              WebkitLineClamp: 2,
                              WebkitBoxOrient: 'vertical',
                              overflow: 'hidden',
                            }}
                            title={v.title}
                          >
                            {v.title} <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>↗</span>
                          </a>
                          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                            📅 {v.published_at}
                            {v.published_relative && (
                              <span
                                style={{
                                  marginLeft: 6,
                                  padding: '1px 5px',
                                  background: 'rgba(56, 189, 248, 0.15)',
                                  color: '#38bdf8',
                                  borderRadius: 3,
                                  fontSize: 10,
                                  border: '1px solid rgba(56, 189, 248, 0.3)',
                                }}
                              >
                                {v.published_relative}
                              </span>
                            )}
                            {v.views_formatted && (
                              <span style={{ marginLeft: 6, color: '#e2e8f0', fontWeight: 500 }}>
                                • 👁️ {v.views_formatted}
                              </span>
                            )}
                            <span style={{ marginLeft: 6 }}>
                              • <code style={{ fontSize: 10 }}>{v.video_id}</code>
                            </span>
                          </div>
                        </div>

                        <div style={{ marginTop: 8 }}>
                          {v.is_ingested ? (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <span className={`badge badge-${v.status === 'DONE' ? 'success' : 'warning'}`}>
                                ● {v.status_label || 'INGESTED'} ({v.clips_count || 0} clips)
                              </span>
                              {v.queue_url && (
                                <a
                                  href={v.queue_url}
                                  className="btn btn-ghost btn-sm"
                                  style={{ fontSize: 11, padding: '2px 8px' }}
                                >
                                  View Queue ↗
                                </a>
                              )}
                            </div>
                          ) : (
                            <button
                              className="btn btn-primary btn-sm"
                              style={{
                                background: '#7c4dff',
                                borderColor: '#7c4dff',
                                width: '100%',
                                padding: '4px 10px',
                                fontSize: 12,
                              }}
                              onClick={() => ingestVideo(v.video_id)}
                              disabled={ingestingId === v.video_id}
                              data-testid={`ingest-video-btn-${v.video_id}`}
                            >
                              {ingestingId === v.video_id ? '⏳ Ingesting…' : '⚡ Ingest & Process'}
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">🔗 Full Channel Wizard & OAuth</div>
            <div className="card-subtitle">Add OAuth credentials, configure posting windows, manage playlists</div>
          </div>
        </div>
        <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 14 }}>
          Complete channel setup including OAuth token linking, per-channel drip-feed posting windows, 
          default caption presets, and upload quota management is available in the full dashboard.
        </p>
        <a href="/channels" className="btn btn-primary" data-testid="open-full-channels">
          Open Full Channel Manager →
        </a>
      </div>
    </div>
  )
}

function ChannelSetupGuideModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0,0,0,0.85)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 10000,
        padding: 16,
      }}
      data-testid="channel-setup-guide-modal"
    >
      <div
        style={{
          background: '#181824',
          border: '1px solid rgba(56, 189, 248, 0.4)',
          borderRadius: 8,
          width: '100%',
          maxWidth: 680,
          maxHeight: '90vh',
          overflowY: 'auto',
          padding: 24,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 18, color: '#38bdf8' }}>
            📘 Complete YouTube Channel Setup Guide
          </h3>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>✕</button>
        </div>

        <p style={{ color: 'var(--text-muted)', fontSize: 13, margin: 0 }}>
          Follow these steps to connect your channel, authorize automated publishing via Google Cloud OAuth, and set up virality defaults.
        </p>

        {/* Step 1 */}
        <div style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border)', padding: 14, borderRadius: 6 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-primary)' }}>
              1. Get Your YouTube Channel ID
            </span>
            <a href="https://studio.youtube.com/channel/settings/advanced" target="_blank" rel="noreferrer" style={{ fontSize: 12, color: '#38bdf8' }}>
              Open YouTube Studio ↗
            </a>
          </div>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0, lineHeight: 1.5 }}>
            Go to <strong>YouTube Studio &gt; Settings &gt; Channel &gt; Advanced settings</strong>.
            Copy the 24-character ID starting with <code>UC...</code> (e.g. <code>UC_x5XG1OV2P6uZZ5FSM9Ttw</code>).
            Handle handles (@name) are not raw IDs.
          </p>
        </div>

        {/* Step 2 */}
        <div style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border)', padding: 14, borderRadius: 6 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-primary)' }}>
              2. Google Cloud OAuth 2.0 Credentials
            </span>
            <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer" style={{ fontSize: 12, color: '#38bdf8' }}>
              GCP Console ↗
            </a>
          </div>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0, lineHeight: 1.5 }}>
            1. Enable <strong>YouTube Data API v3</strong> in Google Cloud.<br />
            2. Configure OAuth Consent Screen and add your YouTube Google account as a Test user.<br />
            3. Create an OAuth 2.0 Client ID with scope <code>https://www.googleapis.com/auth/youtube.upload</code>.<br />
            4. Place <code>client_secrets.json</code> into <code>storage/</code> or paste refreshed token JSON into Channel Setup.
          </p>
        </div>

        {/* Step 3 */}
        <div style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border)', padding: 14, borderRadius: 6 }}>
          <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-primary)', marginBottom: 8 }}>
            3. Category, COPPA &amp; Algorithm Optimization
          </div>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0, lineHeight: 1.5 }}>
            • <strong>Category:</strong> Use <code>22 (People &amp; Blogs)</code> or <code>24 (Entertainment)</code> for highest general recommendation volume.<br />
            • <strong>COPPA:</strong> Keep <strong>Made for Kids unchecked</strong> to enable comments and push notifications.<br />
            • <strong>Peak Publish Hour:</strong> 18:00 UTC (2:00 PM EST / 11:00 AM PST) catches prime North American viewing habits.
          </p>
        </div>

        {/* Step 4 */}
        <div style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border)', padding: 14, borderRadius: 6 }}>
          <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-primary)', marginBottom: 8 }}>
            4. Daily Quota Management
          </div>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0, lineHeight: 1.5 }}>
            YouTube Data API v3 has a default quota of 10,000 units/day. Video uploads consume 1,600 units each (~6 uploads/day max).
            Keep Daily Quota set to 4–5 to prevent quota exhaustion.
          </p>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
          <button className="btn btn-primary" onClick={onClose}>
            ✓ Got It! Close Guide
          </button>
        </div>
      </div>
    </div>
  )
}

