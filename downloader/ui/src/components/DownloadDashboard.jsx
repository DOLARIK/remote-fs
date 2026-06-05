import { useState, useEffect } from 'react'

const CONCURRENCY_OPTIONS = [1, 2, 4, 6, 8]

function ConcurrencyPicker({ disabled }) {
  const [value, setValue] = useState(4)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetch('/api/config').then(r => r.json()).then(d => setValue(d.max_concurrent_downloads ?? 4)).catch(() => {})
  }, [])

  const update = async (n) => {
    setSaving(true)
    try {
      const res = await fetch('/api/config', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_concurrent_downloads: n }),
      })
      const d = await res.json()
      setValue(d.max_concurrent_downloads)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <span style={{ fontSize: '.85rem', color: 'var(--muted)', whiteSpace: 'nowrap' }}>
        Parallel downloads:
      </span>
      <div style={{ display: 'flex', gap: 4 }}>
        {CONCURRENCY_OPTIONS.map(n => (
          <button
            key={n}
            className={n === value ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'}
            style={{ minWidth: 36, padding: '5px 10px' }}
            disabled={disabled || saving}
            onClick={() => update(n)}
          >
            {n}
          </button>
        ))}
      </div>
      {saving && <span style={{ fontSize: '.75rem', color: 'var(--muted)' }}>Saving…</span>}
    </div>
  )
}

function fmt(bytes) {
  if (!bytes) return '0 MB'
  if (bytes >= 1e12) return (bytes / 1e12).toFixed(2) + ' TB'
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + ' GB'
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(0) + ' MB'
  return (bytes / 1e3).toFixed(0) + ' KB'
}

function fmtSpeed(bps) {
  if (!bps) return '—'
  const mbps = bps / 1e6
  return mbps >= 1 ? mbps.toFixed(1) + ' MB/s' : (bps / 1e3).toFixed(0) + ' KB/s'
}

function fmtEta(s) {
  if (!s || s <= 0) return '—'
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

function pct(done, total) {
  if (!total) return 0
  return Math.min(100, Math.round((done / total) * 100))
}

const STATUS_LABEL = {
  scanning: { label: 'Scanning files…', dot: 'dot-blue' },
  running:  { label: 'Downloading',     dot: 'dot-blue' },
  paused:   { label: 'Paused',          dot: 'dot-yellow' },
  completed:{ label: 'Complete',        dot: 'dot-green' },
  failed:   { label: 'Failed',          dot: 'dot-red' },
  idle:     { label: 'Idle',            dot: 'dot-yellow' },
}

export default function DownloadDashboard({ session, liveData, ssdConnected, onPause, onResume, onCancel, onStartNew }) {
  const [confirmCancel, setConfirmCancel] = useState(false)

  const s = { ...session, ...(liveData || {}) }
  const status = s.status || 'idle'
  const { label, dot } = STATUS_LABEL[status] || STATUS_LABEL.idle
  const progress = pct(s.downloaded_files, s.total_files)
  const barClass = status === 'completed' ? 'complete' : status === 'paused' ? 'paused' : ''
  const isRunning = status === 'running'
  const isPaused = status === 'paused'
  const isDone = status === 'completed'
  const isScanning = status === 'scanning'

  return (
    <div>
      <div className="steps">
        <div className="step done">1. Select Drive</div>
        <div className="step done">2. Select Folder</div>
        <div className="step active">3. Download</div>
      </div>

      {!ssdConnected && (
        <div className="alert alert-danger">
          ⚠️ SSD disconnected — download paused automatically. Reconnect the drive to resume.
        </div>
      )}

      {confirmCancel && (
        <div className="modal-overlay">
          <div className="modal">
            <h3>Cancel download?</h3>
            <p>The download will stop. Progress already saved to the SSD will be kept — you can resume later.</p>
            <div className="modal-actions">
              <button className="btn-secondary" onClick={() => setConfirmCancel(false)}>Keep going</button>
              <button className="btn-danger" onClick={() => { setConfirmCancel(false); onCancel() }}>Yes, cancel</button>
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 20 }}>
          <span className={`dot ${dot}`} style={{ width: 10, height: 10 }} />
          <span style={{ fontWeight: 600 }}>{label}</span>
          {s.nextcloud_folder && (
            <span style={{ color: 'var(--muted)', fontSize: '.85rem', marginLeft: 'auto' }}>
              📁 {s.nextcloud_folder}
            </span>
          )}
        </div>

        {/* progress bar */}
        <div style={{ marginBottom: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '.85rem', marginBottom: 6 }}>
            <span>{isScanning ? 'Scanning…' : `${s.downloaded_files ?? 0} of ${s.total_files ?? '?'} photos`}</span>
            <span>{isScanning ? '' : `${progress}%`}</span>
          </div>
          <div className="progress-wrap">
            <div className={`progress-bar ${barClass}`} style={{ width: `${isScanning ? 100 : progress}%`, animation: isScanning ? 'pulse 1s infinite' : 'none' }} />
          </div>
        </div>

        {/* stats */}
        <div className="dash-stats">
          <div className="stat-box">
            <div className="stat-value">{s.downloaded_files ?? 0}</div>
            <div className="stat-label">Photos downloaded</div>
          </div>
          <div className="stat-box">
            <div className="stat-value">{fmtSpeed(s.speed_bps)}</div>
            <div className="stat-label">Speed</div>
          </div>
          <div className="stat-box">
            <div className="stat-value">{isDone ? '🎉' : fmtEta(s.eta_seconds)}</div>
            <div className="stat-label">{isDone ? 'Done!' : 'Time remaining'}</div>
          </div>
        </div>

        {/* secondary stats */}
        <div style={{ display: 'flex', gap: 20, fontSize: '.83rem', color: 'var(--muted)', flexWrap: 'wrap' }}>
          <span>{fmt(s.downloaded_bytes ?? 0)} / {fmt(s.total_bytes ?? 0)}</span>
          {(s.skipped_files > 0) && <span>↩ {s.skipped_files} already on drive</span>}
          {(s.failed_files > 0) && <span style={{ color: 'var(--danger)' }}>✗ {s.failed_files} failed</span>}
        </div>

        {/* controls */}
        {!isDone && (
          <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <ConcurrencyPicker disabled={isScanning} />
            <div className="dash-controls" style={{ margin: 0 }}>
              {isRunning && <button className="btn-secondary" onClick={onPause}>⏸ Pause</button>}
              {isPaused && ssdConnected && <button className="btn-primary" onClick={onResume}>▶ Resume</button>}
              {!isScanning && (
                <button className="btn-danger btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setConfirmCancel(true)}>
                  Cancel
                </button>
              )}
            </div>
          </div>
        )}

        {isDone && (
          <div className="dash-controls">
            <button className="btn-primary" onClick={onStartNew}>Download another folder</button>
          </div>
        )}

        {/* in-flight files */}
        {s.current_files?.length > 0 && (
          <div className="current-files">
            <div className="current-files-title">Currently downloading</div>
            {s.current_files.map(f => (
              <div key={f} className="current-file-item">
                <span className="dot dot-blue" style={{ flexShrink: 0 }} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{f}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
