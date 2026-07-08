import { motion } from 'framer-motion'
import type { ConnStatus } from '@/types'

interface Props {
  status: ConnStatus
  ttsEnabled: boolean
  onToggleTts: (enabled: boolean) => void
  onClear: () => void
  onToggleLogs: () => void
  onToggleSidebar: () => void
}

function statusInfo(status: ConnStatus): { text: string; dot: string; color: string } {
  switch (status) {
    case 'busy':
      return { text: 'Thinking & Executing...', dot: 'busy', color: 'var(--warning-color)' }
    case 'connecting':
      return { text: 'Connecting...', dot: 'connecting', color: 'var(--text-dim)' }
    case 'reconnecting':
      return { text: 'Reconnecting...', dot: 'connecting', color: 'var(--text-dim)' }
    default:
      return { text: 'Ready', dot: '', color: 'var(--success-color)' }
  }
}

export default function Header({
  status,
  ttsEnabled,
  onToggleTts,
  onClear,
  onToggleLogs,
  onToggleSidebar,
}: Props) {
  const { text, dot, color } = statusInfo(status)
  return (
    <header>
      <div className="logo-container">
        <button className="menu-btn" onClick={onToggleSidebar} title="Chat history">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        <div className="logo">THURSDAY</div>
        <div className="status-badge">
          <motion.div
            className={`status-dot ${dot}`}
            animate={{ backgroundColor: color, boxShadow: `0 0 8px ${color}` }}
            transition={{ duration: 0.3 }}
          />
          <span>{text}</span>
        </div>
      </div>
      <div className="controls-container">
        <button className="view-logs-btn" onClick={onToggleLogs}>
          View Logs
        </button>
        <div className="toggle-container">
          <span>Voice</span>
          <label className="switch">
            <input
              type="checkbox"
              checked={ttsEnabled}
              onChange={(e) => onToggleTts(e.target.checked)}
            />
            <span className="slider" />
          </label>
        </div>
        <button className="clear-btn" onClick={onClear}>
          Clear
        </button>
      </div>
    </header>
  )
}
