import { useState, useEffect } from 'react'

function fmt(bytes) {
  if (bytes >= 1e12) return (bytes / 1e12).toFixed(1) + ' TB'
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + ' GB'
  return (bytes / 1e6).toFixed(0) + ' MB'
}

export default function SSDSelector({ onSelect }) {
  const [drives, setDrives] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = () => {
    setLoading(true)
    setError(null)
    fetch('/api/ssds')
      .then(r => r.json())
      .then(d => { setDrives(d); setLoading(false) })
      .catch(() => { setError('Could not read drives. Make sure /Volumes is added to Docker Desktop file sharing.'); setLoading(false) })
  }

  useEffect(() => { load() }, [])

  return (
    <div>
      <div className="steps">
        <div className="step active">1. Select Drive</div>
        <div className="step">2. Select Folder</div>
        <div className="step">3. Download</div>
      </div>

      <div className="card">
        <div className="card-title">Choose your external drive</div>
        <div className="card-subtitle">Plug in your SSD or hard drive, then select it below. Downloaded photos will be saved there.</div>

        {loading && <div className="ssd-empty">Scanning for drives…</div>}
        {error && <div className="alert alert-warning">⚠️ {error}</div>}

        {!loading && !error && drives.length === 0 && (
          <div className="ssd-empty">
            <div style={{ fontSize: '2rem', marginBottom: 12 }}>💿</div>
            <div>No external drives found.</div>
            <div style={{ fontSize: '.85rem', marginTop: 8, color: 'var(--muted)' }}>Plug in your SSD and click Refresh.</div>
          </div>
        )}

        <div className="ssd-grid">
          {drives.map(ssd => (
            <button key={ssd.mount_path} className="ssd-card" style={{ textAlign: 'left' }} onClick={() => onSelect(ssd)}>
              <div className="ssd-icon">💾</div>
              <div className="ssd-name">{ssd.name}</div>
              <div className="ssd-space">{fmt(ssd.free_bytes)} free of {fmt(ssd.total_bytes)}</div>
              {ssd.has_session ? (
                <div className="ssd-badge ssd-badge-resume">▶ Resume download</div>
              ) : (
                <div className="ssd-badge ssd-badge-new">New download</div>
              )}
              {ssd.session_folder && (
                <div style={{ fontSize: '.75rem', color: 'var(--muted)', marginTop: 4 }}>
                  Previously: {ssd.session_folder}
                </div>
              )}
            </button>
          ))}
        </div>

        <div style={{ marginTop: 20 }}>
          <button className="btn-secondary btn-sm" onClick={load}>Refresh drives</button>
        </div>
      </div>
    </div>
  )
}
