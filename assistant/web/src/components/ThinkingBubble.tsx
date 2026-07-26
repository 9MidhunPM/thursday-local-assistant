import { motion } from 'framer-motion'

const enter = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { type: 'spring', stiffness: 520, damping: 32, mass: 0.7 } },
  exit: { opacity: 0, scale: 0.96, transition: { duration: 0.18 } },
}

export default function ThinkingBubble() {
  return (
    <motion.div {...enter} className="message agent" layout="position">
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
