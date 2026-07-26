import { memo } from 'react'
import { motion } from 'framer-motion'
import type { AgentItem, UserItem } from '@/types'
import { renderMarkdown, renderStreamingMarkdown } from '@/lib/markdown'

interface Props {
  item: AgentItem | UserItem
  registerContent?: (el: HTMLDivElement | null) => void
}

const enter = {
  initial: { opacity: 0, y: 10, scale: 0.985 },
  animate: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { type: 'spring', stiffness: 520, damping: 36, mass: 0.7 },
  },
  exit: { opacity: 0, y: -6, scale: 0.985, transition: { duration: 0.14 } },
}

function MessageBubbleBase({ item, registerContent }: Props) {
  if (item.kind === 'user') {
    return (
      <motion.div {...enter} className="message user" layout="position">
        {item.content}
      </motion.div>
    )
  }

  // Agent
  if (item.error) {
    return (
      <motion.div {...enter} className="message agent" layout="position">
        <span className="msg-sender">⚡ Thursday</span>
        <span style={{ color: 'var(--error-color)' }}>{item.content}</span>
      </motion.div>
    )
  }

  const html = item.streaming
    ? renderStreamingMarkdown(item.content)
    : renderMarkdown(item.content)

  return (
    <motion.div {...enter} className="message agent" layout="position">
      <span className="msg-sender">⚡ Thursday</span>
      <div ref={registerContent} dangerouslySetInnerHTML={{ __html: html }} />
    </motion.div>
  )
}

export default memo(MessageBubbleBase)
