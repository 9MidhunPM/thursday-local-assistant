import { useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import type { ChatItem } from '@/types'
import MessageBubble from './MessageBubble'
import ThinkingBubble from './ThinkingBubble'
import ToolCard from './ToolCard'

interface Props {
  items: ChatItem[]
  registerAgentContent: (el: HTMLDivElement | null) => void
}

const container = {
  hidden: { opacity: 1 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.035 },
  },
}

export default function ChatHistory({ items, registerAgentContent }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  // Find the last agent item so only it registers its content element for TTS highlighting.
  let lastAgentIndex = -1
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].kind === 'agent') {
      lastAgentIndex = i
      break
    }
  }

  useEffect(() => {
    const el = containerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [items])

  return (
    <div className="message-container" ref={containerRef}>
      <motion.div
        variants={container}
        initial="hidden"
        animate="show"
        style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}
      >
        <AnimatePresence initial={false}>
          {items.map((item, i) => {
            if (item.kind === 'thinking') return <ThinkingBubble key={item.id} />
            if (item.kind === 'tool') return <ToolCard key={item.id} item={item} />
            return (
              <MessageBubble
                key={item.id}
                item={item}
                registerContent={i === lastAgentIndex ? registerAgentContent : undefined}
              />
            )
          })}
        </AnimatePresence>
      </motion.div>
    </div>
  )
}
