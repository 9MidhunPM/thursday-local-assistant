import { memo } from 'react'
import { motion } from 'framer-motion'
import type { AgentItem, UserItem } from '@/types'
import { renderMarkdown, renderStreamingMarkdown } from '@/lib/markdown'

interface Props {
  item: AgentItem | UserItem
  registerContent?: (el: HTMLDivElement | null) => void
}

const enter = {
  hidden: { opacity: 0, y: 12, scale: 0.98 },
  show: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { type: 'spring', stiffness: 380, damping: 30 },
  },
  exit: { opacity: 0, y: -8, scale: 0.98, transition: { duration: 0.15 } },
}

function MessageBubbleBase({ item, registerContent }: Props) {
  if (item.kind === 'user') {
    return (
      <motion.div variants={enter} className="message user" layout>
        {item.content}
      </motion.div>
    )
  }

  // Agent
  if (item.error) {
    return (
      <motion.div variants={enter} className="message agent" layout>
        <span className="msg-sender">⚡ Thursday</span>
        <span style={{ color: 'var(--error-color)' }}>{item.content}</span>
      </motion.div>
    )
  }

  const html = item.streaming
    ? renderStreamingMarkdown(item.content)
    : renderMarkdown(item.content)

  return (
    <motion.div variants={enter} className="message agent" layout>
      <span className="msg-sender">⚡ Thursday</span>
      <div ref={registerContent} dangerouslySetInnerHTML={{ __html: html }} />
    </motion.div>
  )
}

export default memo(MessageBubbleBase)
