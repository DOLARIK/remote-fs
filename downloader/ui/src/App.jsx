import { useState, useEffect, useRef, useCallback } from 'react'
import StatusBar from './components/StatusBar.jsx'
import SSDSelector from './components/SSDSelector.jsx'
import FolderBrowser from './components/FolderBrowser.jsx'
import DownloadDashboard from './components/DownloadDashboard.jsx'

// Screens: SELECT_SSD → SELECT_FOLDER → DOWNLOADING
const SCREENS = { SELECT_SSD: 'SELECT_SSD', SELECT_FOLDER: 'SELECT_FOLDER', DOWNLOADING: 'DOWNLOADING' }

export default function App() {
  const [screen, setScreen] = useState(SCREENS.SELECT_SSD)
  const [ncStatus, setNcStatus] = useState({ connected: false, loading: true })
  const [selectedSSD, setSelectedSSD] = useState(null)
  const [session, setSession] = useState(null)
  const [liveData, setLiveData] = useState(null)
  const [ssdConnected, setSsdConnected] = useState(true)
  const wsRef = useRef(null)

  // check Nextcloud connection on load
  useEffect(() => {
    fetch('/api/nc/status')
      .then(r => r.json())
      .then(d => setNcStatus({ connected: d.connected, url: d.url, loading: false }))
      .catch(() => setNcStatus({ connected: false, loading: false }))
  }, [])

  // check for existing session on load
  useEffect(() => {
    fetch('/api/session')
      .then(r => r.json())
      .then(s => {
        if (s && s.status && s.status !== 'completed' && s.status !== 'failed') {
          setSession(s)
          setScreen(SCREENS.DOWNLOADING)
        }
      })
      .catch(() => {})
  }, [])

  // WebSocket for live updates
  useEffect(() => {
    function connect() {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${window.location.host}/ws`)
      wsRef.current = ws

      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data)
        if (msg.type === 'progress' || msg.type === 'session_update') {
          setLiveData(msg)
          setSession(prev => prev ? { ...prev, ...msg } : msg)
        }
        if (msg.type === 'ssd_event') {
          setSsdConnected(msg.event === 'reconnected')
        }
        if (msg.type === 'completed') {
          setSession(prev => prev ? { ...prev, status: 'completed' } : prev)
        }
      }

      ws.onclose = () => setTimeout(connect, 3000)
    }
    connect()
    return () => wsRef.current?.close()
  }, [])

  const handleSSDSelected = useCallback((ssd) => {
    setSelectedSSD(ssd)
    setSsdConnected(true)
    setScreen(SCREENS.SELECT_FOLDER)
  }, [])

  const handleFolderSelected = useCallback(async (folder, force = false) => {
    const res = await fetch('/api/session/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssd_path: selectedSSD.mount_path, nextcloud_folder: folder, force }),
    })
    const data = await res.json()

    if (data.warning === 'wrong_ssd') {
      return data  // caller shows warning
    }

    const sessionRes = await fetch('/api/session')
    const s = await sessionRes.json()
    setSession(s)
    setScreen(SCREENS.DOWNLOADING)
    return data
  }, [selectedSSD])

  const handlePause = useCallback(() => fetch('/api/session/pause', { method: 'POST' }), [])
  const handleResume = useCallback(() => fetch('/api/session/resume', { method: 'POST' }), [])
  const handleCancel = useCallback(async () => {
    await fetch('/api/session/cancel', { method: 'POST' })
    setSession(null)
    setScreen(SCREENS.SELECT_SSD)
  }, [])

  const handleStartNew = useCallback(() => {
    setSession(null)
    setSelectedSSD(null)
    setScreen(SCREENS.SELECT_SSD)
  }, [])

  return (
    <div className="app">
      <header className="app-header">
        <h1>Photo Downloader</h1>
        <StatusBar ncStatus={ncStatus} ssdConnected={ssdConnected} selectedSSD={selectedSSD} />
      </header>

      <main className="app-main">
        {screen === SCREENS.SELECT_SSD && (
          <SSDSelector onSelect={handleSSDSelected} />
        )}
        {screen === SCREENS.SELECT_FOLDER && selectedSSD && (
          <FolderBrowser
            ssd={selectedSSD}
            onSelect={handleFolderSelected}
            onBack={() => setScreen(SCREENS.SELECT_SSD)}
          />
        )}
        {screen === SCREENS.DOWNLOADING && session && (
          <DownloadDashboard
            session={session}
            liveData={liveData}
            ssdConnected={ssdConnected}
            onPause={handlePause}
            onResume={handleResume}
            onCancel={handleCancel}
            onStartNew={handleStartNew}
          />
        )}
      </main>
    </div>
  )
}
