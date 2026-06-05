export default function StatusBar({ ncStatus, ssdConnected, selectedSSD }) {
  return (
    <div className="status-bar">
      <span className="status-item">
        {ncStatus.loading ? (
          <><span className="dot dot-yellow" />Connecting…</>
        ) : ncStatus.connected ? (
          <><span className="dot dot-green" />Nextcloud connected</>
        ) : (
          <><span className="dot dot-red" />Nextcloud unreachable</>
        )}
      </span>

      {selectedSSD && (
        <span className="status-item">
          {ssdConnected ? (
            <><span className="dot dot-green" />{selectedSSD.name}</>
          ) : (
            <><span className="dot dot-red" />SSD disconnected — download paused</>
          )}
        </span>
      )}
    </div>
  )
}
