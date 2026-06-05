import { useState, useEffect } from 'react'

function FolderRow({ item, depth, selected, onSelect, onToggle, expanded }) {
  const isSelected = selected === item.path
  return (
    <div
      className={`folder-row ${isSelected ? 'selected' : ''}`}
      style={{ paddingLeft: 14 + depth * 20 }}
      onClick={() => {
        if (item.is_dir) {
          onSelect(item.path)
          onToggle(item.path)
        }
      }}
    >
      <span className="folder-toggle">
        {item.is_dir ? (expanded ? '▾' : '▸') : ''}
      </span>
      <span>{item.is_dir ? (expanded ? '📂' : '📁') : '🖼️'}</span>
      <span className="folder-name">{item.name}</span>
    </div>
  )
}

function FolderNode({ path, depth, selected, onSelect, expandedMap, setExpandedMap }) {
  const [children, setChildren] = useState(null)
  const expanded = !!expandedMap[path]

  useEffect(() => {
    if (expanded && children === null) {
      fetch(`/api/nc/browse?path=${encodeURIComponent(path)}`)
        .then(r => r.json())
        .then(d => setChildren(d.filter(i => i.is_dir)))
        .catch(() => setChildren([]))
    }
  }, [expanded])

  const toggle = (p) => setExpandedMap(prev => ({ ...prev, [p]: !prev[p] }))

  return (
    <>
      {children && expanded && children.map(child => (
        <div key={child.path}>
          <FolderRow
            item={child}
            depth={depth}
            selected={selected}
            onSelect={onSelect}
            onToggle={toggle}
            expanded={!!expandedMap[child.path]}
          />
          <FolderNode
            path={child.path}
            depth={depth + 1}
            selected={selected}
            onSelect={onSelect}
            expandedMap={expandedMap}
            setExpandedMap={setExpandedMap}
          />
        </div>
      ))}
      {expanded && children === null && (
        <div className="folder-row loading" style={{ paddingLeft: 14 + depth * 20 }}>Loading…</div>
      )}
    </>
  )
}

export default function FolderBrowser({ ssd, onSelect, onBack }) {
  const [roots, setRoots] = useState(null)
  const [selected, setSelected] = useState(null)
  const [expandedMap, setExpandedMap] = useState({})
  const [warning, setWarning] = useState(null)
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    fetch('/api/nc/browse?path=')
      .then(r => r.json())
      .then(d => setRoots(d.filter(i => i.is_dir)))
      .catch(() => setRoots([]))

    // pre-select previous folder if resuming
    if (ssd.session_folder) setSelected(ssd.session_folder)
  }, [])

  const toggle = (p) => setExpandedMap(prev => ({ ...prev, [p]: !prev[p] }))

  const handleStart = async () => {
    if (!selected) return
    setStarting(true)
    const res = await onSelect(selected, false)
    if (res?.warning === 'wrong_ssd') {
      setWarning(res)
      setStarting(false)
    }
  }

  const handleForce = async () => {
    setWarning(null)
    setStarting(true)
    await onSelect(selected, true)
  }

  return (
    <div>
      <div className="steps">
        <div className="step done">1. Select Drive</div>
        <div className="step active">2. Select Folder</div>
        <div className="step">3. Download</div>
      </div>

      {warning && (
        <div className="modal-overlay">
          <div className="modal">
            <h3>⚠️ Different folder detected</h3>
            <p>
              This drive was previously used to download <strong>{warning.existing_folder}</strong>.
              Continuing will <strong>restart the download from scratch</strong> for the new folder.
            </p>
            <div className="modal-actions">
              <button className="btn-secondary" onClick={() => setWarning(null)}>Go back</button>
              <button className="btn-danger" onClick={handleForce}>Yes, start fresh</button>
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-title">Select a folder to download</div>
        <div className="card-subtitle">Choose the folder from your Nextcloud library. All photos inside will be downloaded.</div>

        {selected && (
          <div className="folder-selected-path">
            📁 Selected: <strong>{selected}</strong>
          </div>
        )}

        <div className="folder-tree">
          {roots === null && <div className="folder-row loading">Loading folders…</div>}
          {roots?.map(item => (
            <div key={item.path}>
              <FolderRow
                item={item}
                depth={0}
                selected={selected}
                onSelect={setSelected}
                onToggle={toggle}
                expanded={!!expandedMap[item.path]}
              />
              <FolderNode
                path={item.path}
                depth={1}
                selected={selected}
                onSelect={setSelected}
                expandedMap={expandedMap}
                setExpandedMap={setExpandedMap}
              />
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn-secondary" onClick={onBack}>← Back</button>
          <button
            className="btn-primary"
            disabled={!selected || starting}
            onClick={handleStart}
          >
            {starting ? 'Starting…' : ssd.has_session && ssd.session_folder === selected ? 'Resume Download' : 'Start Download'}
          </button>
        </div>
      </div>
    </div>
  )
}
