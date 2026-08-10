import { useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import type { ChatItem } from '@/types'
import MessageBubble from './MessageBubble'
import ThinkingBubble from './ThinkingBubble'
import ToolCard from './ToolCard'

interface Props {
  items: ChatItem[]
  registerAgentContent: (el: HTMLDivElement | null) => void
  onPrompt?: (text: string) => void
}

const WELCOME_PROMPTS = [
  'Summarize my open files',
  'What tools can you use?',
  'Check system status',
  'Remember that I prefer concise answers',
]

function isWelcome(items: ChatItem[]): boolean {
  if (items.length > 1) return false
  const only = items[0]
  return only?.kind === 'agent' && !only.error && only.content.toLowerCase().startsWith('welcome')
}

export default function ChatHistory({ items, registerAgentContent, onPrompt }: Props) {
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

  const showWelcome = isWelcome(items)

  return (
    <div className="message-container" ref={containerRef}>
      {showWelcome ? (
        <motion.div
          className="welcome"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
        >
          <div className="welcome-orb">⚡</div>
          <div className="welcome-title">How can I help?</div>
          <div className="welcome-sub">
            I&apos;m Thursday — your local assistant. I can read files, run commands, search the
            web, and remember what matters. Ask me anything, or start with one of these.
          </div>
          <div className="prompt-chips">
            {WELCOME_PROMPTS.map((p) => (
              <button
                key={p}
                className="prompt-chip"
                onClick={() => onPrompt?.(p)}
                type="button"
              >
                {p}
              </button>
            ))}
          </div>
        </motion.div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
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
        </div>
      )}
    </div>
  )
}
