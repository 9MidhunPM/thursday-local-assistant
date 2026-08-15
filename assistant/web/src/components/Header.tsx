import { motion } from 'framer-motion'
import type { ConnStatus } from '@/types'
import type { HealthInfo } from '@/hooks/useHealth'

interface Props {
  status: ConnStatus
  ttsEnabled: boolean
  onToggleTts: (enabled: boolean) => void
  onClear: () => void
  onToggleLogs: () => void
  onOpenCodexProject: () => void
  onToggleSidebar: () => void
  health: HealthInfo
  conversationCount: number
}

function statusInfo(status: ConnStatus): { text: string; dot: string; color: string } {
  switch (status) {
    case 'busy':
      return { text: 'Thinking…', dot: 'busy', color: 'var(--warning-color)' }
    case 'connecting':
      return { text: 'Connecting…', dot: 'connecting', color: 'var(--text-dim)' }
    case 'reconnecting':
      return { text: 'Reconnecting…', dot: 'connecting', color: 'var(--text-dim)' }
    default:
      return { text: 'Ready', dot: '', color: 'var(--success-color)' }
  }
}

function shortModel(name: string): string {
  if (!name) return ''
  return name.replace(/^.*[\\/]/, '').replace(/\.gguf$/i, '')
}

export default function Header({
  status,
  ttsEnabled,
  onToggleTts,
  onClear,
  onToggleLogs,
  onOpenCodexProject,
  onToggleSidebar,
  health,
  conversationCount,
}: Props) {
  const { text, dot, color } = statusInfo(status)
  const modelLabel = shortModel(health.model) || health.provider || (health.mode ? health.mode : '')

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
        {modelLabel && (
          <div className={`meta-badge${health.modelReady ? '' : ' starting'}`} title={`${health.provider} · ${health.model}`}>
            <span className="meta-key">{health.mode || 'model'}</span>
            <span className="meta-val">{modelLabel}</span>
          </div>
        )}
      </div>
      <div className="controls-container">
        {conversationCount > 0 && (
          <span className="meta-badge" title="Saved conversations">
            <span className="meta-key">chats</span>
            <span className="meta-val">{conversationCount}</span>
          </span>
        )}
        <button className="view-logs-btn" onClick={onToggleLogs}>
          Logs
        </button>
        <button className="view-logs-btn codex-project-btn" onClick={onOpenCodexProject}>
          <span aria-hidden="true">⌘</span> Codex Project
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
