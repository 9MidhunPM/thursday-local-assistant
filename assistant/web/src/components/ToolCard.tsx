import { memo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import type { ToolItem } from '@/types'

interface Props {
  item: ToolItem
}

const cardEnter = {
  hidden: { opacity: 0, y: 10, height: 0 },
  show: {
    opacity: 1,
    y: 0,
    height: 'auto',
    transition: { type: 'spring', stiffness: 360, damping: 30 },
  },
  exit: { opacity: 0, height: 0, transition: { duration: 0.2 } },
}

function ToolCardBase({ item }: Props) {
  const [expanded, setExpanded] = useState(true)

  const statusClass = `status-${item.status}`
  const statusLabel =
    item.status === 'running' ? 'Running' : item.status === 'success' ? 'Done' : 'Failed'

  return (
    <motion.div
      variants={cardEnter}
      layout
      className={`tool-card ${statusClass}${expanded ? ' expanded' : ''}`}
    >
      <div className="tool-card-header" onClick={() => setExpanded((e) => !e)}>
        <span className="tool-card-icon">⚙️</span>
        <span className="tool-card-name">{item.tool}</span>
        <span className="tool-card-status">
          {statusLabel}
          {item.status === 'running' && (
            <span className="tool-running-dots">
              <span />
              <span />
              <span />
            </span>
          )}
        </span>
        <motion.span
          className="tool-card-chevron"
          animate={{ rotate: expanded ? 180 : 0 }}
          transition={{ duration: 0.2 }}
        >
          ▾
        </motion.span>
      </div>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            style={{ overflow: 'hidden' }}
          >
            <div className="tool-card-body">
              <div className="tool-section request">
                <div className="tool-section-label">Request</div>
                <pre>{item.args || ''}</pre>
              </div>
              {item.result !== undefined && (
                <div
                  className={`tool-section result${item.status === 'error' ? ' error' : ''}`}
                >
                  <div className="tool-section-label">Result</div>
                  <pre>{item.result}</pre>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

export default memo(ToolCardBase)
