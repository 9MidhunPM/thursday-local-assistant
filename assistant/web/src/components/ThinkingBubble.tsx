import { motion } from 'framer-motion'

const enter = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { type: 'spring', stiffness: 380, damping: 28 } },
  exit: { opacity: 0, scale: 0.95, transition: { duration: 0.2 } },
}

export default function ThinkingBubble() {
  return (
    <motion.div variants={enter} className="message agent" layout>
      <span className="msg-sender">⚡ Thursday</span>
      <div className="thinking-bubble">
        <div className="thinking-ripple">
          <span />
          <span />
          <span />
        </div>
        Thinking
      </div>
    </motion.div>
  )
}
