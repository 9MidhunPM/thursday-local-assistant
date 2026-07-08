import { useEffect, useRef } from 'react'

interface Props {
  visible: boolean
  logs: string[]
  onClose: () => void
}

export default function LogsModal({ visible, logs, onClose }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (visible && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [visible, logs])

  return (
    <div className={`logs-modal${visible ? ' visible' : ''}`}>
      <div className="logs-modal-header">
        <h2>Hardware Terminal</h2>
        <button className="close-logs-btn" onClick={onClose}>
          ×
        </button>
      </div>
      <div className="logs-container" ref={containerRef}>
        {logs.map((line, i) => (
          <div className="log-line" key={i}>
            {line}
          </div>
        ))}
      </div>
    </div>
  )
}
