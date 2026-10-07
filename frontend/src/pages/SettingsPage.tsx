// Settings Page — full platform configuration, all params via UI
import { useEffect, useState } from 'react'
import { apiGet, apiPost } from '../api'
import type { ToastType } from '../hooks/useToast'

interface Settings {
  // AI & Routing
  LLM_PROVIDER: string
  LLM_MODEL_NAME: string
  GROQ_MODEL: string
  // Duration bounds
  TARGET_DURATION_MIN_SEC: number
  TARGET_DURATION_MAX_SEC: number
  MAX_CLIPS_PER_VIDEO: number
  MIN_CLIPS_PER_VIDEO: number
  // Video quality
  VIDEO_CRF: number
  VIDEO_PRESET: string
  VIDEO_SCALE_ALGORITHM: string
  TARGET_FPS: number
  AUDIO_BITRATE: string
  AUDIO_SAMPLE_RATE: number
  // Publishing
  DRY_RUN_PUBLISHING: boolean
  DRIP_FEED_ENABLED: boolean
  DRIP_FEED_POLL_INTERVAL_SECONDS: number
  MAX_RETRY_COUNT: number
  // Whisper
  WHISPER_MODEL_SIZE: string
  WHISPER_BEAM_SIZE: number
  SILERO_VAD_FILTER: boolean
  WHISPER_LANGUAGE: string
  // Ingestion & Network Armor (Pillars 1-5)
  INGESTION_POLICY_MODE?: string
  INGESTION_PROXY_STRICT_MODE?: boolean
  INGESTION_PROXY_POOL?: string[]
  INGESTION_ENABLE_OAUTH?: boolean
  HOTFOLDER_ENABLED?: boolean
}

const PROVIDERS = ['auto', 'groq', 'gemini', 'openrouter', 'openai']
const PRESETS = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'veryslow']
const ALGOS = ['lanczos', 'bicubic', 'bilinear', 'neighbor']
const WHISPER_SIZES = ['tiny', 'base', 'small', 'medium', 'large-v2', 'large-v3']

function Toggle({ checked, onChange, id }: { checked: boolean; onChange: (v: boolean) => void; id: string }) {
  return (
    <label htmlFor={id} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
      <div
        id={id}
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        data-testid={`toggle-${id}`}
        style={{
          width: 40, height: 22, borderRadius: 11,
          background: checked ? 'var(--accent)' : 'var(--border)',
          position: 'relative', transition: 'background 0.2s', cursor: 'pointer',
        }}
      >
        <div style={{
          position: 'absolute', top: 3, left: checked ? 21 : 3,
          width: 16, height: 16, borderRadius: '50%',
          background: '#fff', transition: 'left 0.2s',
        }} />
      </div>
      <span style={{ fontSize: 13, color: checked ? 'var(--text-primary)' : 'var(--text-muted)' }}>
        {checked ? 'On' : 'Off'}
      </span>
    </label>
  )
}

const DEFAULT_SETTINGS: Settings = {
  LLM_PROVIDER: 'auto',
  LLM_MODEL_NAME: 'gemini-2.5-flash',
  GROQ_MODEL: 'llama-3.3-70b-versatile',
  TARGET_DURATION_MIN_SEC: 30,
  TARGET_DURATION_MAX_SEC: 60,
  MAX_CLIPS_PER_VIDEO: 5,
  MIN_CLIPS_PER_VIDEO: 1,
  VIDEO_CRF: 19,
  VIDEO_PRESET: 'fast',
  VIDEO_SCALE_ALGORITHM: 'lanczos',
  TARGET_FPS: 30,
  AUDIO_BITRATE: '320k',
  AUDIO_SAMPLE_RATE: 48000,
  DRY_RUN_PUBLISHING: true,
  DRIP_FEED_ENABLED: true,
  DRIP_FEED_POLL_INTERVAL_SECONDS: 60,
  MAX_RETRY_COUNT: 3,
  WHISPER_MODEL_SIZE: 'base',
  WHISPER_BEAM_SIZE: 5,
  SILERO_VAD_FILTER: true,
  WHISPER_LANGUAGE: 'en',
  INGESTION_POLICY_MODE: 'two_tier',
  INGESTION_PROXY_STRICT_MODE: false,
  INGESTION_PROXY_POOL: [],
  INGESTION_ENABLE_OAUTH: true,
  HOTFOLDER_ENABLED: true,
}

export default function SettingsPage({ addToast }: { addToast: (msg: string, t?: ToastType) => void }) {
  const [s, setS] = useState<Settings>(DEFAULT_SETTINGS)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    apiGet<Settings>('/api/settings/export')
      .then(d => { if (d) setS(d) })
      .catch(() => { /* use defaults */ })
  }, [])

  const set = <K extends keyof Settings>(key: K, val: Settings[K]) => {
    setS(prev => ({ ...prev, [key]: val }))
  }

  const save = async () => {
    if (!s) return
    setSaving(true)
    try {
      await apiPost('/api/settings/save', s)
      addToast('✅ Settings saved — active immediately', 'success')
    } catch (e: unknown) {
      addToast(`Save failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div data-testid="settings-page">

      {/* ── AI & Routing ── */}
      <div className="card">
        <div className="card-header"><div className="card-title">🤖 AI & LLM Routing</div></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
          <div className="form-group">
            <label className="form-label">LLM Provider</label>
            <select className="form-select" value={s.LLM_PROVIDER} onChange={e => set('LLM_PROVIDER', e.target.value)} data-testid="setting-LLM_PROVIDER">
              {PROVIDERS.map(p => <option key={p}>{p}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Gemini Model</label>
            <input className="form-input" value={s.LLM_MODEL_NAME} onChange={e => set('LLM_MODEL_NAME', e.target.value)} data-testid="setting-LLM_MODEL_NAME" />
          </div>
          <div className="form-group">
            <label className="form-label">Groq Model</label>
            <input className="form-input" value={s.GROQ_MODEL} onChange={e => set('GROQ_MODEL', e.target.value)} data-testid="setting-GROQ_MODEL" />
          </div>
        </div>
      </div>

      {/* ── Clip Duration Bounds ── */}
      <div className="card">
        <div className="card-header"><div className="card-title">⏱ Clip Duration & Selection Bounds</div></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 14 }}>
          {([
            ['Min Duration (s)', 'TARGET_DURATION_MIN_SEC'],
            ['Max Duration (s)', 'TARGET_DURATION_MAX_SEC'],
            ['Min Clips / Video', 'MIN_CLIPS_PER_VIDEO'],
            ['Max Clips / Video', 'MAX_CLIPS_PER_VIDEO'],
          ] as [string, keyof Settings][]).map(([label, key]) => (
            <div className="form-group" key={key}>
              <label className="form-label">{label}</label>
              <input className="form-input" type="number" value={s[key] as number}
                onChange={e => set(key, parseFloat(e.target.value))}
                data-testid={`setting-${key}`} />
            </div>
          ))}
        </div>
      </div>

      {/* ── Video & Audio Quality ── */}
      <div className="card">
        <div className="card-header"><div className="card-title">🎬 Video & Audio Quality</div></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
          <div className="form-group">
            <label className="form-label">Video CRF (lower = better quality)</label>
            <input className="form-input" type="number" min={12} max={32} value={s.VIDEO_CRF}
              onChange={e => set('VIDEO_CRF', parseInt(e.target.value))} data-testid="setting-VIDEO_CRF" />
          </div>
          <div className="form-group">
            <label className="form-label">Encoding Preset</label>
            <select className="form-select" value={s.VIDEO_PRESET} onChange={e => set('VIDEO_PRESET', e.target.value)} data-testid="setting-VIDEO_PRESET">
              {PRESETS.map(p => <option key={p}>{p}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Scale Algorithm</label>
            <select className="form-select" value={s.VIDEO_SCALE_ALGORITHM} onChange={e => set('VIDEO_SCALE_ALGORITHM', e.target.value)} data-testid="setting-VIDEO_SCALE_ALGORITHM">
              {ALGOS.map(a => <option key={a}>{a}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Target FPS</label>
            <input className="form-input" type="number" min={24} max={60} value={s.TARGET_FPS}
              onChange={e => set('TARGET_FPS', parseInt(e.target.value))} data-testid="setting-TARGET_FPS" />
          </div>
          <div className="form-group">
            <label className="form-label">Audio Bitrate</label>
            <input className="form-input" value={s.AUDIO_BITRATE} onChange={e => set('AUDIO_BITRATE', e.target.value)} data-testid="setting-AUDIO_BITRATE" />
          </div>
          <div className="form-group">
            <label className="form-label">Audio Sample Rate (Hz)</label>
            <input className="form-input" type="number" value={s.AUDIO_SAMPLE_RATE}
              onChange={e => set('AUDIO_SAMPLE_RATE', parseInt(e.target.value))} data-testid="setting-AUDIO_SAMPLE_RATE" />
          </div>
        </div>
      </div>

      {/* ── Whisper Tuning ── */}
      <div className="card">
        <div className="card-header"><div className="card-title">🎙 Whisper Transcription Tuning</div></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
          <div className="form-group">
            <label className="form-label">Whisper Model Size</label>
            <select className="form-select" value={s.WHISPER_MODEL_SIZE} onChange={e => set('WHISPER_MODEL_SIZE', e.target.value)} data-testid="setting-WHISPER_MODEL_SIZE">
              {WHISPER_SIZES.map(w => <option key={w}>{w}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Beam Size</label>
            <input className="form-input" type="number" min={1} max={10} value={s.WHISPER_BEAM_SIZE}
              onChange={e => set('WHISPER_BEAM_SIZE', parseInt(e.target.value))} data-testid="setting-WHISPER_BEAM_SIZE" />
          </div>
          <div className="form-group">
            <label className="form-label">Language Override (empty = auto)</label>
            <input className="form-input" value={s.WHISPER_LANGUAGE} onChange={e => set('WHISPER_LANGUAGE', e.target.value)}
              placeholder="e.g. en, es, ur" data-testid="setting-WHISPER_LANGUAGE" />
          </div>
          <div className="form-group">
            <label className="form-label" style={{ marginBottom: 10 }}>Silero VAD Filter</label>
            <Toggle id="SILERO_VAD_FILTER" checked={s.SILERO_VAD_FILTER} onChange={v => set('SILERO_VAD_FILTER', v)} />
          </div>
        </div>
      </div>

      {/* ── Publishing ── */}
      <div className="card">
        <div className="card-header"><div className="card-title">🚀 Publishing & Drip-Feed</div></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
          <div className="form-group">
            <label className="form-label" style={{ marginBottom: 10 }}>Dry Run Mode</label>
            <Toggle id="DRY_RUN_PUBLISHING" checked={s.DRY_RUN_PUBLISHING} onChange={v => set('DRY_RUN_PUBLISHING', v)} />
          </div>
          <div className="form-group">
            <label className="form-label" style={{ marginBottom: 10 }}>Drip-Feed Enabled</label>
            <Toggle id="DRIP_FEED_ENABLED" checked={s.DRIP_FEED_ENABLED} onChange={v => set('DRIP_FEED_ENABLED', v)} />
          </div>
          <div className="form-group">
            <label className="form-label">Poll Interval (seconds)</label>
            <input className="form-input" type="number" value={s.DRIP_FEED_POLL_INTERVAL_SECONDS}
              onChange={e => set('DRIP_FEED_POLL_INTERVAL_SECONDS', parseInt(e.target.value))} data-testid="setting-DRIP_FEED_POLL_INTERVAL_SECONDS" />
          </div>
          <div className="form-group">
            <label className="form-label">Max Retry Count</label>
            <input className="form-input" type="number" value={s.MAX_RETRY_COUNT}
              onChange={e => set('MAX_RETRY_COUNT', parseInt(e.target.value))} data-testid="setting-MAX_RETRY_COUNT" />
          </div>
        </div>
      </div>

      {/* ── Save ── */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 8, marginBottom: 24 }}>
        <button className="btn btn-primary" onClick={save} disabled={saving} data-testid="save-settings-btn">
          {saving ? '⏳ Saving…' : '💾 Save Settings'}
        </button>
      </div>

      {/* ── Ingestion & Network Armor Hub (Pillars 1-5) ── */}
      <IngestionArmorHubSection settings={s} onUpdateSetting={set} addToast={addToast} />

      {/* ── Centralized Backup & Restore Hub ── */}
      <BackupRestoreHubSection addToast={addToast} />

      {/* ── Configuration Files & Paths Inspector ── */}
      <ConfigPathsSection addToast={addToast} />

      {/* ── Granular Data & Storage Reset Center ── */}
      <GranularResetSection addToast={addToast} />
    </div>
  )
}

function BackupRestoreHubSection({ addToast }: { addToast: (msg: string, t?: ToastType) => void }) {
  const [showKeysModal, setShowKeysModal] = useState(false)
  const [showSnapshotModal, setShowSnapshotModal] = useState(false)
  const [keysJson, setKeysJson] = useState('')
  const [keysPassphrase, setKeysPassphrase] = useState('')
  const [snapshotJson, setSnapshotJson] = useState('')
  const [snapshotPassphrase, setSnapshotPassphrase] = useState('')
  const [loading, setLoading] = useState(false)

  const exportKeysPlaintext = () => {
    window.location.href = '/api/settings/keys/export?mode=plaintext'
    addToast('Downloading plaintext API keys backup...', 'info')
  }

  const exportKeysEncrypted = () => {
    const pw = prompt('Enter a passphrase to encrypt your API Keys Vault backup (min 4 characters):')
    if (pw === null) return
    if (pw.trim().length < 4) {
      addToast('Passphrase must be at least 4 characters', 'error')
      return
    }
    window.location.href = `/api/settings/keys/export?mode=encrypted&passphrase=${encodeURIComponent(pw.trim())}`
    addToast('Downloading encrypted API keys vault...', 'info')
  }

  const handlePurgeInvalidKeys = async () => {
    if (!confirm('Ping all configured API keys and permanently remove any that return HTTP 400, 401, or 403 (Invalid/Unauthorized)?')) return
    try {
      const res = await apiPost<{ status: string; message: string; purged_count: number }>('/api/settings/keys/purge-invalid')
      addToast(res.message || `Purged ${res.purged_count} dead keys`, 'success')
    } catch (err) {
      addToast('Failed to purge keys: ' + (err instanceof Error ? err.message : String(err)), 'error')
    }
  }

  const exportSnapshotPlaintext = () => {
    window.location.href = '/api/settings/snapshot/export?mode=plaintext'
    addToast('Downloading full system snapshot...', 'info')
  }

  const exportSnapshotEncrypted = () => {
    const pw = prompt('Enter a passphrase to encrypt your Full System Snapshot (min 4 characters):')
    if (pw === null) return
    if (pw.trim().length < 4) {
      addToast('Passphrase must be at least 4 characters', 'error')
      return
    }
    window.location.href = `/api/settings/snapshot/export?mode=encrypted&passphrase=${encodeURIComponent(pw.trim())}`
    addToast('Downloading encrypted system snapshot...', 'info')
  }

  const exportConfigOnly = async () => {
    try {
      const data = await apiGet<Record<string, unknown>>('/api/settings/export')
      const blob = new Blob([JSON.stringify({ app: "YouTube Shorts Automation Studio", version: "1.0", exported_at: new Date().toISOString(), settings: data }, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `shorts_studio_config_${new Date().toISOString().split('T')[0]}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      addToast('Config backup downloaded!', 'success')
    } catch (e: unknown) {
      addToast(`Config export failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    }
  }

  const handleConfigImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      const text = await file.text()
      const parsed = JSON.parse(text)
      await apiPost('/api/settings/import', parsed)
      addToast('Configuration restored successfully! Reloading...', 'success')
      setTimeout(() => window.location.reload(), 1200)
    } catch (e: unknown) {
      addToast(`Config restore failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      e.target.value = ''
    }
  }

  const handleKeysFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = ev => setKeysJson(ev.target?.result as string || '')
    reader.readAsText(file)
  }

  const executeRestoreKeys = async () => {
    if (!keysJson.trim()) {
      addToast('Please choose a file or paste keys JSON', 'error')
      return
    }
    setLoading(true)
    try {
      const parsed = JSON.parse(keysJson.trim())
      const res = await apiPost<{ imported: number; skipped: number }>('/api/settings/keys/import', {
        payload: parsed,
        passphrase: keysPassphrase.trim() || undefined
      })
      addToast(`API keys restored! Added: ${res.imported}, Skipped: ${res.skipped}`, 'success')
      setShowKeysModal(false)
      setKeysJson('')
      setKeysPassphrase('')
    } catch (e: unknown) {
      addToast(`Keys restore failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleSnapshotFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = ev => setSnapshotJson(ev.target?.result as string || '')
    reader.readAsText(file)
  }

  const executeRestoreSnapshot = async () => {
    if (!snapshotJson.trim()) {
      addToast('Please choose a file or paste snapshot JSON', 'error')
      return
    }
    setLoading(true)
    try {
      const parsed = JSON.parse(snapshotJson.trim())
      const res = await apiPost<{ channels?: { imported: number }; keys?: { imported: number } }>('/api/settings/snapshot/import', {
        payload: parsed,
        passphrase: snapshotPassphrase.trim() || undefined
      })
      const ch = res.channels || { imported: 0 }
      const k = res.keys || { imported: 0 }
      addToast(`Snapshot restored! Settings updated, Channels: ${ch.imported || 0} added, Keys: ${k.imported || 0} added`, 'success')
      setShowSnapshotModal(false)
      setSnapshotJson('')
      setSnapshotPassphrase('')
      setTimeout(() => window.location.reload(), 1200)
    } catch (e: unknown) {
      addToast(`Snapshot restore failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="card" style={{ marginBottom: 24 }} data-testid="backup-restore-hub-card">
      <div className="card-header">
        <div className="card-title">🔐 System Backup &amp; Restore Hub</div>
        <span className="badge badge-info" style={{ fontSize: 11 }}>Dual-Mode Safety</span>
      </div>
      <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 16 }}>
        Export and restore credentials, monitored channels, and runtime configurations.
        Supports both <strong>Plaintext JSON</strong> for transparent inspection and <strong>Password-Protected AES-256-GCM Encrypted JSON</strong> (PBKDF2-HMAC-SHA256) for secure offsite archival.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
        {/* Full System Snapshot */}
        <div style={{ background: 'rgba(0,0,0,0.3)', padding: 14, borderRadius: 6, border: '1px solid var(--border)', display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: '#f59e0b', marginBottom: 6 }}>
            📦 Full System Snapshot
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', flexGrow: 1, margin: '0 0 12px' }}>
            Unified archive containing Settings, Monitored Channels &amp; Upload Defaults, and the API Key Vault.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="btn btn-ghost btn-sm" style={{ flex: 1 }} onClick={exportSnapshotPlaintext}>
                📥 Plaintext
              </button>
              <button className="btn btn-ghost btn-sm" style={{ flex: 1 }} onClick={exportSnapshotEncrypted}>
                🔒 Encrypted
              </button>
            </div>
            <button className="btn btn-primary btn-sm" style={{ background: '#f59e0b', borderColor: '#f59e0b' }} onClick={() => setShowSnapshotModal(true)}>
              📤 Restore Snapshot…
            </button>
          </div>
        </div>

        {/* API Keys Vault */}
        <div style={{ background: 'rgba(0,0,0,0.3)', padding: 14, borderRadius: 6, border: '1px solid var(--border)', display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: '#10b981', marginBottom: 6 }}>
            🔑 API Keys Vault
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', flexGrow: 1, margin: '0 0 12px' }}>
            Backup configured Groq, Gemini, OpenRouter, and OpenAI credentials with multi-provider rate limits.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="btn btn-ghost btn-sm" style={{ flex: 1 }} onClick={exportKeysPlaintext}>
                📥 Plaintext
              </button>
              <button className="btn btn-ghost btn-sm" style={{ flex: 1 }} onClick={exportKeysEncrypted}>
                🔒 Encrypted
              </button>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="btn btn-primary btn-sm" style={{ flex: 1, background: '#10b981', borderColor: '#10b981' }} onClick={() => setShowKeysModal(true)}>
                📤 Restore Keys…
              </button>
              <button className="btn btn-danger btn-sm" style={{ background: 'rgba(239, 68, 68, 0.2)', border: '1px solid #ef4444', color: '#fca5a5' }} onClick={handlePurgeInvalidKeys} title="Auto-ping and purge all keys with 401/400/403 errors">
                🧹 Purge Dead
              </button>
            </div>
          </div>
        </div>

        {/* Runtime Settings Baseline */}
        <div style={{ background: 'rgba(0,0,0,0.3)', padding: 14, borderRadius: 6, border: '1px solid var(--border)', display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: '#38bdf8', marginBottom: 6 }}>
            ⚙️ Runtime Settings Only
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', flexGrow: 1, margin: '0 0 12px' }}>
            Export or import pipeline duration boundaries, video encoding quality, and whisper parameters.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <button className="btn btn-ghost btn-sm" onClick={exportConfigOnly}>
              📥 Download Config (.json)
            </button>
            <label className="btn btn-primary btn-sm" style={{ textAlign: 'center', cursor: 'pointer', background: '#38bdf8', borderColor: '#38bdf8', margin: 0 }}>
              📤 Restore Config…
              <input type="file" accept=".json,application/json" style={{ display: 'none' }} onChange={handleConfigImportFile} />
            </label>
          </div>
        </div>
      </div>

      {/* Keys Restore Modal */}
      {showKeysModal && (
        <div style={{
          position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.85)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000, padding: 16
        }}>
          <div style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8,
            width: '90%', maxWidth: 500, padding: 20
          }}>
            <h3 style={{ margin: '0 0 10px', color: '#10b981', fontSize: 18 }}>
              🔑 Restore API Keys Vault
            </h3>
            <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 14 }}>
              Upload an <code>api_keys_backup.json</code> (or encrypted backup) file.
            </p>

            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
                Choose Backup File:
              </label>
              <input type="file" accept=".json,application/json" className="form-input" onChange={handleKeysFile} />
            </div>

            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
                Or Paste JSON:
              </label>
              <textarea
                className="form-input"
                style={{ fontFamily: 'monospace', fontSize: 12, minHeight: 80 }}
                placeholder='{"type": "api_keys_plaintext", ...}'
                value={keysJson}
                onChange={e => setKeysJson(e.target.value)}
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
                Decryption Passphrase (If Password-Protected):
              </label>
              <input
                type="password"
                className="form-input"
                placeholder="Leave blank if plaintext backup"
                value={keysPassphrase}
                onChange={e => setKeysPassphrase(e.target.value)}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button className="btn btn-ghost" onClick={() => setShowKeysModal(false)} disabled={loading}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                style={{ background: '#10b981', borderColor: '#10b981' }}
                onClick={executeRestoreKeys}
                disabled={loading}
              >
                {loading ? '⏳ Restoring…' : 'Restore Keys'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Snapshot Restore Modal */}
      {showSnapshotModal && (
        <div style={{
          position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.85)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000, padding: 16
        }}>
          <div style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8,
            width: '90%', maxWidth: 500, padding: 20
          }}>
            <h3 style={{ margin: '0 0 10px', color: '#f59e0b', fontSize: 18 }}>
              📦 Restore Full System Snapshot
            </h3>
            <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 14 }}>
              Restores runtime parameters, channel monitors, and the API key vault in a single atomic operation without server restart.
            </p>

            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
                Choose Snapshot File:
              </label>
              <input type="file" accept=".json,application/json" className="form-input" onChange={handleSnapshotFile} />
            </div>

            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
                Or Paste Snapshot JSON:
              </label>
              <textarea
                className="form-input"
                style={{ fontFamily: 'monospace', fontSize: 12, minHeight: 80 }}
                placeholder='{"version": 1, "type": "full_system_snapshot", ...}'
                value={snapshotJson}
                onChange={e => setSnapshotJson(e.target.value)}
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>
                Decryption Passphrase (If Protected):
              </label>
              <input
                type="password"
                className="form-input"
                placeholder="Enter passphrase if key archive is encrypted"
                value={snapshotPassphrase}
                onChange={e => setSnapshotPassphrase(e.target.value)}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button className="btn btn-ghost" onClick={() => setShowSnapshotModal(false)} disabled={loading}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                style={{ background: '#f59e0b', borderColor: '#f59e0b' }}
                onClick={executeRestoreSnapshot}
                disabled={loading}
              >
                {loading ? '⏳ Restoring…' : 'Restore Snapshot'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

interface PathItem {
  name: string
  description: string
  path: string
  exists: boolean
  type?: string
  size_mb?: number
  file_count?: number
}

function ConfigPathsSection({ addToast }: { addToast: (msg: string, t?: ToastType) => void }) {
  const [paths, setPaths] = useState<Record<string, PathItem>>({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiGet<Record<string, PathItem>>('/api/settings/config-paths')
      .then(d => { if (d) setPaths(d) })
      .catch(() => { /* fallback */ })
      .finally(() => setLoading(false))
  }, [])

  const copyPath = (p: string) => {
    navigator.clipboard.writeText(p)
      .then(() => addToast('📋 Path copied to clipboard!', 'success'))
      .catch(() => addToast('Failed to copy', 'error'))
  }

  return (
    <div className="card" style={{ marginBottom: 24 }} data-testid="config-paths-card">
      <div className="card-header">
        <div className="card-title">📂 Configuration Files & Storage Directory Paths</div>
        <span className="badge badge-info" style={{ fontSize: 11 }}>Filesystem Inspector</span>
      </div>
      <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 14 }}>
        Canonical filesystem locations for configuration templates, active SQLite databases, encryption keys, and media directories.
        The application is self-healing: if any storage folder is missing, it is automatically re-created on boot.
      </p>

      {loading ? (
        <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>Loading filesystem inventory…</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: '25%' }}>Component</th>
                <th style={{ width: '45%' }}>Absolute Path</th>
                <th style={{ width: '18%' }}>Status</th>
                <th style={{ width: '12%', textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(paths).map(([key, item]) => (
                <tr key={key}>
                  <td>
                    <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{item.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{item.description}</div>
                  </td>
                  <td>
                    <code style={{ fontSize: 11, color: '#38bdf8', wordBreak: 'break-all' }}>{item.path}</code>
                  </td>
                  <td>
                    {item.exists ? (
                      <span className="badge badge-success" style={{ fontSize: 11 }}>
                        Ready {item.size_mb !== undefined ? `(${item.size_mb} MB)` : ''}
                      </span>
                    ) : (
                      <span className="badge badge-muted" style={{ fontSize: 11 }}>Not Created</span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <button className="btn btn-ghost btn-sm" onClick={() => copyPath(item.path)} title="Copy path">
                      📋 Copy
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

interface StorageInventory {
  storage: {
    downloads: { file_count: number; size_mb: number }
    outputs: { file_count: number; size_mb: number }
    temp: { file_count: number; size_mb: number }
  }
  database: {
    publishing_logs: number
    clips: number
    clip_candidates: number
    source_videos: number
    transcripts: number
    channels: number
    users: number
    total_records: number
  }
  total_storage_mb: number
}

function GranularResetSection({ addToast }: { addToast: (msg: string, t?: ToastType) => void }) {
  const [inv, setInv] = useState<StorageInventory | null>(null)
  const [opts, setOpts] = useState({
    delete_raw_downloads: false,
    delete_rendered_outputs: false,
    delete_temp_files: false,
    delete_publishing_logs: false,
    delete_clips_and_candidates: false,
    delete_pipeline_history: false,
    delete_channels: false,
    reset_settings: false,
    delete_api_keys: false,
    delete_users: false,
    preserve_admin: true,
  })
  const [showModal, setShowModal] = useState(false)
  const [confirmKeyword, setConfirmKeyword] = useState('')
  const [executing, setExecuting] = useState(false)

  const loadInventory = () => {
    apiGet<StorageInventory>('/api/settings/storage-inventory')
      .then(d => { if (d) setInv(d) })
      .catch(() => { /* fallback */ })
  }

  useEffect(() => {
    loadInventory()
  }, [])

  const toggleOpt = (key: keyof typeof opts) => {
    setOpts(prev => ({ ...prev, [key]: !prev[key] }))
  }

  const applyPreset = (preset: 'temp' | 'media' | 'history' | 'factory' | 'clear') => {
    if (preset === 'clear') {
      setOpts({
        delete_raw_downloads: false,
        delete_rendered_outputs: false,
        delete_temp_files: false,
        delete_publishing_logs: false,
        delete_clips_and_candidates: false,
        delete_pipeline_history: false,
        delete_channels: false,
        reset_settings: false,
        delete_api_keys: false,
        delete_users: false,
        preserve_admin: true,
      })
    } else if (preset === 'temp') {
      setOpts(prev => ({ ...prev, delete_temp_files: true }))
    } else if (preset === 'media') {
      setOpts(prev => ({ ...prev, delete_raw_downloads: true, delete_rendered_outputs: true, delete_temp_files: true }))
    } else if (preset === 'history') {
      setOpts(prev => ({ ...prev, delete_publishing_logs: true, delete_clips_and_candidates: true, delete_pipeline_history: true }))
    } else if (preset === 'factory') {
      setOpts({
        delete_raw_downloads: true,
        delete_rendered_outputs: true,
        delete_temp_files: true,
        delete_publishing_logs: true,
        delete_clips_and_candidates: true,
        delete_pipeline_history: true,
        delete_channels: true,
        reset_settings: true,
        delete_api_keys: true,
        delete_users: true,
        preserve_admin: true,
      })
    }
  }

  const isTotalWipe =
    opts.delete_raw_downloads &&
    opts.delete_rendered_outputs &&
    opts.delete_temp_files &&
    opts.delete_publishing_logs &&
    opts.delete_clips_and_candidates &&
    opts.delete_pipeline_history &&
    opts.delete_channels &&
    opts.reset_settings &&
    opts.delete_api_keys

  const anySelected = Object.entries(opts).some(([k, v]) => k !== 'preserve_admin' && Boolean(v))

  // Estimate reclaimable
  let reclaimMb = 0.0
  let reclaimRecords = 0
  if (inv) {
    if (opts.delete_temp_files) reclaimMb += inv.storage.temp.size_mb
    if (opts.delete_raw_downloads) reclaimMb += inv.storage.downloads.size_mb
    if (opts.delete_rendered_outputs) reclaimMb += inv.storage.outputs.size_mb

    if (opts.delete_publishing_logs) reclaimRecords += inv.database.publishing_logs
    if (opts.delete_clips_and_candidates) reclaimRecords += (inv.database.clips + inv.database.clip_candidates)
    if (opts.delete_pipeline_history) reclaimRecords += (inv.database.source_videos + inv.database.transcripts)
    if (opts.delete_channels) reclaimRecords += inv.database.channels
    if (opts.delete_users) reclaimRecords += inv.database.users
  }

  const runReset = async () => {
    setExecuting(true)
    try {
      const res = await apiPost<any>('/api/settings/reset', {
        ...opts,
        confirm_code: confirmKeyword.trim().toUpperCase(),
        is_total_wipe: isTotalWipe
      })
      addToast(`Reset complete! Freed ${res.total_files_deleted || 0} files (${res.total_reclaimed_mb || 0} MB)`, 'success')
      setShowModal(false)
      setConfirmKeyword('')
      applyPreset('clear')
      loadInventory()
    } catch (e: unknown) {
      addToast(`Reset failed: ${e instanceof Error ? e.message : String(e)}`, 'error')
    } finally {
      setExecuting(false)
    }
  }

  return (
    <div className="card" style={{ borderColor: 'rgba(239,68,68,0.4)', marginBottom: 24 }} data-testid="granular-reset-card">
      <div className="card-header" style={{ background: 'rgba(239,68,68,0.08)' }}>
        <div className="card-title" style={{ color: '#ef4444' }}>⚠️ Granular Data, Storage & System Reset Center</div>
        <span className="badge badge-danger">Danger Zone</span>
      </div>
      <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 14 }}>
        Prune temporary cache, wipe raw media, reset database history, or restore factory defaults.
        <strong> Zero-Crash Guarantee:</strong> Reset actions never terminate or shut down the server.
      </p>

      {/* Telemetry pill row */}
      {inv && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 16 }}>
          <div style={{ background: 'var(--bg-card)', padding: 10, borderRadius: 6, border: '1px solid var(--border)', textAlign: 'center' }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Total Purgeable Storage</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#38bdf8' }}>{inv.total_storage_mb} MB</div>
          </div>
          <div style={{ background: 'var(--bg-card)', padding: 10, borderRadius: 6, border: '1px solid var(--border)', textAlign: 'center' }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Database Records</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#f59e0b' }}>{inv.database.total_records}</div>
          </div>
          <div style={{ background: 'var(--bg-card)', padding: 10, borderRadius: 6, border: '1px solid var(--border)', textAlign: 'center' }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Selected Reclaim</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#10b981' }}>{reclaimMb.toFixed(1)} MB / {reclaimRecords} rec</div>
          </div>
        </div>
      )}

      {/* Quick Presets */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>Presets:</span>
        <button className="btn btn-ghost btn-sm" onClick={() => applyPreset('temp')}>🧹 Clean Temp</button>
        <button className="btn btn-ghost btn-sm" onClick={() => applyPreset('media')}>🎬 Purge Media</button>
        <button className="btn btn-ghost btn-sm" onClick={() => applyPreset('history')}>📊 Reset History</button>
        <button className="btn btn-ghost btn-sm" style={{ color: '#ef4444' }} onClick={() => applyPreset('factory')}>⚠️ Factory Wipe</button>
        <button className="btn btn-ghost btn-sm" style={{ marginLeft: 'auto' }} onClick={() => applyPreset('clear')}>✕ Clear</button>
      </div>

      {/* Checkboxes grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
        {/* Storage */}
        <div style={{ background: 'rgba(0,0,0,0.3)', padding: 12, borderRadius: 6, border: '1px solid var(--border)' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#38bdf8', marginBottom: 10 }}>💾 Media Storage</div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: 8 }}>
            <input type="checkbox" checked={opts.delete_temp_files} onChange={() => toggleOpt('delete_temp_files')} />
            <span>Temp Scratch & VAD Cache</span>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: 8 }}>
            <input type="checkbox" checked={opts.delete_raw_downloads} onChange={() => toggleOpt('delete_raw_downloads')} />
            <span>Raw Video Downloads</span>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
            <input type="checkbox" checked={opts.delete_rendered_outputs} onChange={() => toggleOpt('delete_rendered_outputs')} />
            <span>Rendered Master Shorts</span>
          </label>
        </div>

        {/* Database */}
        <div style={{ background: 'rgba(0,0,0,0.3)', padding: 12, borderRadius: 6, border: '1px solid var(--border)' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#f59e0b', marginBottom: 10 }}>🗄️ Database & History</div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: 8 }}>
            <input type="checkbox" checked={opts.delete_publishing_logs} onChange={() => toggleOpt('delete_publishing_logs')} />
            <span>Publishing & Audit Logs</span>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: 8 }}>
            <input type="checkbox" checked={opts.delete_clips_and_candidates} onChange={() => toggleOpt('delete_clips_and_candidates')} />
            <span>Clips & Candidates</span>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: 8 }}>
            <input type="checkbox" checked={opts.delete_pipeline_history} onChange={() => toggleOpt('delete_pipeline_history')} />
            <span>Source Videos & Pipelines</span>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
            <input type="checkbox" checked={opts.delete_channels} onChange={() => toggleOpt('delete_channels')} />
            <span>Channel Profiles</span>
          </label>
        </div>

        {/* Settings & Keys */}
        <div style={{ background: 'rgba(0,0,0,0.3)', padding: 12, borderRadius: 6, border: '1px solid var(--border)' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#ef4444', marginBottom: 10 }}>🔐 Settings & Credentials</div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: 8 }}>
            <input type="checkbox" checked={opts.reset_settings} onChange={() => toggleOpt('reset_settings')} />
            <span>Factory Settings Baseline</span>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: 8 }}>
            <input type="checkbox" checked={opts.delete_api_keys} onChange={() => toggleOpt('delete_api_keys')} />
            <span>Clear API Key Vault</span>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer', marginBottom: 8 }}>
            <input type="checkbox" checked={opts.delete_users} onChange={() => toggleOpt('delete_users')} />
            <span>Reset User Accounts</span>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, cursor: 'pointer', color: '#10b981', marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--border)' }}>
            <input type="checkbox" checked={opts.preserve_admin} onChange={() => toggleOpt('preserve_admin')} />
            <span>Preserve Admin Session</span>
          </label>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
        <button
          className="btn btn-danger"
          disabled={!anySelected}
          onClick={() => setShowModal(true)}
          data-testid="trigger-reset-modal-btn"
        >
          🗑️ Execute Reset Action…
        </button>
      </div>

      {/* Confirmation Modal */}
      {showModal && (
        <div style={{
          position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.85)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000
        }}>
          <div style={{
            background: 'var(--bg-card)', border: '1px solid #ef4444', borderRadius: 8,
            width: '90%', maxWidth: 500, padding: 20
          }}>
            <h3 style={{ margin: '0 0 12px', color: '#ef4444', fontSize: 18 }}>
              ⚠️ Confirm Destructive Action
            </h3>
            <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 14 }}>
              The selected components will be permanently deleted. Cleaned directories are instantly re-created with zero server disruption.
            </p>

            <div style={{ background: 'rgba(0,0,0,0.5)', padding: 12, borderRadius: 6, marginBottom: 14, fontSize: 12 }}>
              <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6 }}>Estimated Reclaim:</div>
              <div style={{ color: '#10b981' }}>{reclaimMb.toFixed(1)} MB storage / {reclaimRecords} DB records</div>
            </div>

            {isTotalWipe && (
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 12, color: '#ef4444', marginBottom: 6, fontWeight: 600 }}>
                  Destructive Total Wipe: Type <code>RESET</code> or <code>DELETE</code> to confirm:
                </div>
                <input
                  className="form-input"
                  style={{ textAlign: 'center', fontWeight: 700, borderColor: '#ef4444', color: '#ef4444' }}
                  value={confirmKeyword}
                  onChange={e => setConfirmKeyword(e.target.value)}
                  placeholder="Type RESET"
                />
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button className="btn btn-ghost" onClick={() => setShowModal(false)} disabled={executing}>
                Cancel
              </button>
              <button
                className="btn btn-danger"
                disabled={executing || (isTotalWipe && !['RESET', 'DELETE'].includes(confirmKeyword.trim().toUpperCase()))}
                onClick={runReset}
                data-testid="confirm-final-reset-btn"
              >
                {executing ? '⏳ Purging…' : 'Confirm & Purge'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function IngestionArmorHubSection({
  settings: s,
  onUpdateSetting: set,
  addToast,
}: {
  settings: Settings
  onUpdateSetting: <K extends keyof Settings>(k: K, v: Settings[K]) => void
  addToast: (msg: string, t?: ToastType) => void
}) {
  // OAuth State
  const [oauthStatus, setOauthStatus] = useState<any>({ connected: false, status: 'DISCONNECTED' })
  const [oauthLoading, setOauthLoading] = useState(false)
  const [showOauthModal, setShowOauthModal] = useState(false)
  const [deviceData, setDeviceData] = useState<any>(null)
  const [pollIntervalId, setPollIntervalId] = useState<any>(null)

  // Harvester State
  const [harvesterStatus, setHarvesterStatus] = useState<any>(null)
  const [harvesting, setHarvesting] = useState(false)

  // Cookies Vault State
  const [cookiesStatus, setCookiesStatus] = useState<any>(null)
  const [cookiesText, setCookiesText] = useState('')
  const [savingCookies, setSavingCookies] = useState(false)

  // Proxy Mesh State
  const [proxyStatus, setProxyStatus] = useState<any>(null)
  const [proxyInput, setProxyInput] = useState('')
  const [proxyTestResult, setProxyTestResult] = useState<any>(null)
  const [testingProxy, setTestingProxy] = useState(false)

  // Hotfolder State
  const [hotfolderStatus, setHotfolderStatus] = useState<any>(null)
  const [scanningHotfolder, setScanningHotfolder] = useState(false)

  const refreshAllStatus = async () => {
    try {
      const [oauth, harv, cks, prx, htf] = await Promise.all([
        apiGet<any>('/api/ingest/oauth/status').catch(() => null),
        apiGet<any>('/api/ingest/harvester/status').catch(() => null),
        apiGet<any>('/api/ingest/cookies/status').catch(() => null),
        apiGet<any>('/api/ingest/proxy/status').catch(() => null),
        apiGet<any>('/api/ingest/hotfolder/status').catch(() => null),
      ])
      if (oauth) setOauthStatus(oauth)
      if (harv) setHarvesterStatus(harv)
      if (cks) setCookiesStatus(cks)
      if (prx) {
        setProxyStatus(prx)
        if (prx.proxies && Array.isArray(prx.proxies)) {
          setProxyInput(prx.proxies.join('\n'))
        }
      }
      if (htf) setHotfolderStatus(htf)
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    refreshAllStatus()
    return () => {
      if (pollIntervalId) clearInterval(pollIntervalId)
    }
  }, [])

  const startOAuthFlow = async () => {
    setOauthLoading(true)
    try {
      const res = await apiPost<any>('/api/ingest/oauth/start', {})
      if (res && res.device_code) {
        setDeviceData(res)
        setShowOauthModal(true)
        const iv = setInterval(async () => {
          try {
            const pollRes = await apiPost<any>('/api/ingest/oauth/poll', { device_code: res.device_code })
            if (pollRes && pollRes.status === 'success') {
              clearInterval(iv)
              setShowOauthModal(false)
              addToast('✅ Google Ingestion Account linked successfully!', 'success')
              refreshAllStatus()
            } else if (pollRes && (pollRes.status === 'expired' || pollRes.status === 'denied')) {
              clearInterval(iv)
              setShowOauthModal(false)
              addToast(`OAuth stopped: ${pollRes.message}`, 'error')
            }
          } catch {
            // waiting for operator input at google.com/device
          }
        }, (res.interval || 5) * 1000)
        setPollIntervalId(iv)
      }
    } catch (e: any) {
      addToast(`Could not initiate Device OAuth: ${e?.message || e}`, 'error')
    } finally {
      setOauthLoading(false)
    }
  }

  const cancelOAuthFlow = () => {
    if (pollIntervalId) clearInterval(pollIntervalId)
    setShowOauthModal(false)
    setDeviceData(null)
  }

  const revokeOAuth = async () => {
    if (!confirm('Unlink Google Ingestion Account? This revokes active tokens.')) return
    try {
      await apiPost('/api/ingest/oauth/revoke', {})
      addToast('YouTube OAuth session disconnected.', 'info')
      refreshAllStatus()
    } catch (e: any) {
      addToast(`Revoke failed: ${e?.message || e}`, 'error')
    }
  }

  const runHarvester = async () => {
    setHarvesting(true)
    try {
      const res = await apiPost<any>('/api/ingest/harvester/run', {})
      addToast(`✅ Session harvested: ${res.cookies_count} cookies generated via ${res.engine}`, 'success')
      refreshAllStatus()
    } catch (e: any) {
      addToast(`Harvest failed: ${e?.message || e}`, 'error')
    } finally {
      setHarvesting(false)
    }
  }

  const saveCookies = async () => {
    if (!cookiesText.trim()) {
      addToast('Paste Netscape cookies content before saving', 'error')
      return
    }
    setSavingCookies(true)
    try {
      await apiPost('/api/ingest/cookies/save', { cookies_content: cookiesText })
      addToast('✅ Manual cookies vault updated', 'success')
      setCookiesText('')
      refreshAllStatus()
    } catch (e: any) {
      addToast(`Save failed: ${e?.message || e}`, 'error')
    } finally {
      setSavingCookies(false)
    }
  }

  const handleProxySave = (text: string) => {
    setProxyInput(text)
    const list = text.split('\n').map(l => l.trim()).filter(Boolean)
    set('INGESTION_PROXY_POOL', list)
  }

  const testProxies = async () => {
    setTestingProxy(true)
    setProxyTestResult(null)
    try {
      const res = await apiPost<any>('/api/ingest/proxy/test', {})
      setProxyTestResult(res)
      addToast('Proxy egress diagnostic ping complete', 'info')
      refreshAllStatus()
    } catch (e: any) {
      addToast(`Proxy test failed: ${e?.message || e}`, 'error')
    } finally {
      setTestingProxy(false)
    }
  }

  const scanHotfolder = async () => {
    setScanningHotfolder(true)
    try {
      const res = await apiPost<any>('/api/ingest/hotfolder/scan', {})
      addToast(`Hotfolder scan finished: ${res.ingested_count} files ingested`, 'success')
      refreshAllStatus()
    } catch (e: any) {
      addToast(`Scan failed: ${e?.message || e}`, 'error')
    } finally {
      setScanningHotfolder(false)
    }
  }

  return (
    <div style={{ marginTop: 24, marginBottom: 28 }} data-testid="ingestion-armor-hub">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>
            🛡️ Ingestion & Network Armor Hub
          </h2>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-muted)' }}>
            Triple-Tier Authentication, Anti-Bot Defense, Multi-Mode Safety Gate, and Master Video Ingestion.
          </p>
        </div>
        <button className="btn btn-ghost" onClick={refreshAllStatus} title="Refresh status">
          🔄 Refresh Diagnostics
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* Card 1: Content Policy Triage */}
        <div className="card">
          <div className="card-header">
            <div className="card-title">🚦 Content Safety & Policy Gate (Pillar 2)</div>
          </div>
          <div className="form-group" style={{ marginBottom: 12 }}>
            <label className="form-label">Pre-Flight Policy Triage Mode</label>
            <select
              className="form-select"
              value={s.INGESTION_POLICY_MODE || 'two_tier'}
              onChange={e => set('INGESTION_POLICY_MODE', e.target.value)}
              data-testid="setting-INGESTION_POLICY_MODE"
            >
              <option value="two_tier">Two-Tier Safety Triage (Recommended: Auth + Sensitive Advisory)</option>
              <option value="strict_gate">Strict Pre-Ingestion Gate (Hold restricted in Pending Queue)</option>
              <option value="auto_skip">Auto-Skip Restricted Content (Channel Protection)</option>
            </select>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.4 }}>
              {(s.INGESTION_POLICY_MODE || 'two_tier') === 'two_tier' &&
                '💡 Evaluates age gates & Restricted Mode. If credentials are held in Tier 1-3 Vault, downloads and attaches SENSITIVE_CONTENT badge for operator review; fails fast otherwise.'}
              {s.INGESTION_POLICY_MODE === 'strict_gate' &&
                '⚠️ Immediately stops pipeline for restricted or age-gated videos, queuing them for operator manual release.'}
              {s.INGESTION_POLICY_MODE === 'auto_skip' &&
                '🛡️ Silently skips restricted or age-gated videos to protect channel monetization algorithms.'}
            </div>
          </div>
        </div>

        {/* Card 2: Tier 1 Device OAuth2 */}
        <div className="card">
          <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="card-title">🔐 Tier 1: YouTube Device OAuth2 (RFC-8628)</div>
            {oauthStatus.connected ? (
              <span className="badge badge-success">CONNECTED</span>
            ) : (
              <span className="badge badge-muted">DISCONNECTED</span>
            )}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 12 }}>
            {oauthStatus.connected ? (
              <div>
                <p style={{ margin: '0 0 6px', color: 'var(--text-primary)' }}>
                  ✅ Active Google Account session linked. Tokens encrypted via AES-256-GCM.
                </p>
                <div style={{ fontSize: 11, fontFamily: 'monospace' }}>
                  Expires in: {Math.floor((oauthStatus.expires_in_sec || 0) / 60)}m | Auto-Refresh: {oauthStatus.has_refresh_token ? 'Enabled' : 'No Refresh Token'}
                </div>
              </div>
            ) : (
              <p style={{ margin: 0 }}>
                Link a Google Account via TV Device flow to bypass Restricted Mode and anti-bot challenges.
              </p>
            )}
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            {oauthStatus.connected ? (
              <button className="btn btn-danger" onClick={revokeOAuth} style={{ fontSize: 12 }}>
                Unlink Account
              </button>
            ) : (
              <button className="btn btn-primary" onClick={startOAuthFlow} disabled={oauthLoading} style={{ fontSize: 12 }}>
                {oauthLoading ? '⏳ Contacting Google…' : '🔗 Link Google Account'}
              </button>
            )}
          </div>
        </div>

        {/* Card 3: Tier 2 Session Harvester */}
        <div className="card">
          <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="card-title">🤖 Tier 2: Headless Session Harvester</div>
            {harvesterStatus?.fresh ? (
              <span className="badge badge-success">ACTIVE ({harvesterStatus.cookies_count} cookies)</span>
            ) : (
              <span className="badge badge-warning">{harvesterStatus?.exists ? 'STALE' : 'EMPTY'}</span>
            )}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 12 }}>
            Exports fresh visitor session parameters to <code>storage/harvester_cookies.txt</code>.
            {harvesterStatus?.exists && (
              <div style={{ marginTop: 4, fontSize: 11 }}>
                Age: {harvesterStatus.age_minutes}m | Size: {(harvesterStatus.size_bytes / 1024).toFixed(1)} KB
              </div>
            )}
          </div>
          <button className="btn btn-secondary" onClick={runHarvester} disabled={harvesting} style={{ fontSize: 12 }}>
            {harvesting ? '⏳ Harvesting Cookies…' : '⚡ Run Harvester Now'}
          </button>
        </div>

        {/* Card 4: Tier 3 Cookies Vault */}
        <div className="card">
          <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="card-title">🍪 Tier 3: Cookies Vault (Netscape)</div>
            {cookiesStatus?.exists ? (
              <span className="badge badge-info">{cookiesStatus.cookies_count} cookies stored</span>
            ) : (
              <span className="badge badge-muted">NO COOKIES</span>
            )}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 8 }}>
            Paste raw Netscape <code>cookies.txt</code> to update <code>storage/cookies.txt</code> fallback:
          </div>
          <textarea
            className="form-input"
            rows={2}
            style={{ fontFamily: 'monospace', fontSize: 11, marginBottom: 8 }}
            placeholder="# Netscape HTTP Cookie File&#10;.youtube.com TRUE / TRUE ..."
            value={cookiesText}
            onChange={e => setCookiesText(e.target.value)}
          />
          <button className="btn btn-secondary" onClick={saveCookies} disabled={savingCookies} style={{ fontSize: 12 }}>
            {savingCookies ? '⏳ Saving…' : '💾 Save Cookies Vault'}
          </button>
        </div>

        {/* Card 5: Proxy Mesh & Bot Armor */}
        <div className="card" style={{ gridColumn: 'span 2' }}>
          <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="card-title">🌐 Dynamic Proxy Mesh & Anti-Bot Armor (Pillar 3)</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Strict Proxy Enforcement:</span>
              <Toggle
                id="INGESTION_PROXY_STRICT_MODE"
                checked={Boolean(s.INGESTION_PROXY_STRICT_MODE)}
                onChange={v => set('INGESTION_PROXY_STRICT_MODE', v)}
              />
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <div>
              <label className="form-label">Proxy Pool (one HTTP/HTTPS/SOCKS5 URL per line)</label>
              <textarea
                className="form-input"
                rows={3}
                style={{ fontFamily: 'monospace', fontSize: 11, marginBottom: 8 }}
                placeholder="http://user:pass@proxy1.example.com:8080&#10;socks5://127.0.0.1:9050"
                value={proxyInput}
                onChange={e => handleProxySave(e.target.value)}
              />
              <div style={{ display: 'flex', gap: 10 }}>
                <button className="btn btn-secondary" onClick={testProxies} disabled={testingProxy} style={{ fontSize: 12 }}>
                  {testingProxy ? '⏳ Pinging Egress Nodes…' : '📡 Test Egress & Ping Proxies'}
                </button>
              </div>
            </div>

            <div style={{ background: 'rgba(0,0,0,0.3)', padding: 12, borderRadius: 6, fontSize: 12 }}>
              <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6 }}>
                Proxy Mesh Diagnostics:
              </div>
              <div>Pool Size: {proxyStatus?.total_proxies || 0} nodes</div>
              <div>Healthy: <span style={{ color: '#10b981' }}>{proxyStatus?.healthy_proxies || 0}</span> | Degraded (429): <span style={{ color: '#ef4444' }}>{proxyStatus?.degraded_proxies || 0}</span></div>
              <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-muted)' }}>
                Posture: {s.INGESTION_PROXY_STRICT_MODE ? '🔴 Strict (Rejects direct datacenter IP)' : '🟢 Graceful Direct Fallback'}
              </div>

              {proxyTestResult && (
                <div style={{ marginTop: 8, padding: 8, background: 'rgba(255,255,255,0.04)', borderRadius: 4 }}>
                  <div style={{ fontWeight: 600 }}>Test Result:</div>
                  {Array.isArray(proxyTestResult.results) ? (
                    proxyTestResult.results.map((r: any, idx: number) => (
                      <div key={idx} style={{ fontSize: 11, marginTop: 4 }}>
                        {r.healthy ? '✅' : '❌'} {r.url}: {r.latency_ms}ms (IP: {r.external_ip || r.error})
                      </div>
                    ))
                  ) : (
                    <div style={{ fontSize: 11, marginTop: 4 }}>
                      {proxyTestResult.healthy ? '✅' : '❌'} {proxyTestResult.latency_ms}ms (IP: {proxyTestResult.external_ip || proxyTestResult.error})
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Card 6: Hotfolder Automated Watcher */}
        <div className="card" style={{ gridColumn: 'span 2' }}>
          <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="card-title">📁 Hotfolder Automated Video Ingestion (Pillar 4)</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Hotfolder Daemon:</span>
              <Toggle
                id="HOTFOLDER_ENABLED"
                checked={s.HOTFOLDER_ENABLED !== false}
                onChange={v => set('HOTFOLDER_ENABLED', v)}
              />
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
              Monitors <code>storage/hotfolder/</code> for drops (.mp4, .mov, .mkv, .webm) with Win32 lock & byte-stability protection.
              <div style={{ marginTop: 4, fontSize: 11 }}>
                Pending Drops: <b>{hotfolderStatus?.pending_files_count || 0}</b> | Processed Master Files: <b>{hotfolderStatus?.processed_files_count || 0}</b>
              </div>
            </div>
            <button className="btn btn-secondary" onClick={scanHotfolder} disabled={scanningHotfolder} style={{ fontSize: 12 }}>
              {scanningHotfolder ? '⏳ Scanning…' : '🔍 Scan Hotfolder Now'}
            </button>
          </div>
        </div>
      </div>

      {/* Modal: Google RFC-8628 Device Authorization */}
      {showOauthModal && deviceData && (
        <div style={{
          position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.85)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000
        }}>
          <div style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8,
            width: '90%', maxWidth: 480, padding: 24, textAlign: 'center'
          }}>
            <h3 style={{ margin: '0 0 12px', fontSize: 18, color: 'var(--text-primary)' }}>
              🔐 Link Google Ingestion Account
            </h3>
            <p style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 18 }}>
              Open the verification link below on any phone or browser and enter the activation code:
            </p>

            <div style={{
              background: 'rgba(255,255,255,0.06)', padding: 14, borderRadius: 6,
              marginBottom: 16, border: '1px solid var(--accent)'
            }}>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>Activation Code:</div>
              <div style={{ fontSize: 28, fontWeight: 800, letterSpacing: 4, color: 'var(--accent)', fontFamily: 'monospace' }}>
                {deviceData.user_code}
              </div>
            </div>

            <div style={{ marginBottom: 20 }}>
              <a
                href={deviceData.verification_url}
                target="_blank"
                rel="noreferrer"
                className="btn btn-primary"
                style={{ display: 'inline-block', textDecoration: 'none' }}
              >
                🌐 Open {deviceData.verification_url} ↗
              </a>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--text-muted)', fontSize: 12, marginBottom: 18 }}>
              <div className="spinner" style={{ width: 14, height: 14 }} />
              Awaiting operator authorization on Google...
            </div>

            <button className="btn btn-ghost" onClick={cancelOAuthFlow}>
              Cancel Authorization
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

