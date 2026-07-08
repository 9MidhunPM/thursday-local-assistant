import { useCallback, useEffect, useRef, useState } from 'react'
import { useVoiceInput } from '@/hooks/useVoiceInput'

interface Props {
  disabled: boolean
  onSend: (prompt: string) => void
  ensureAudio: () => void
}

export default function Composer({ disabled, onSend, ensureAudio }: Props) {
  const [input, setInput] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const handleSubmit = useCallback(() => {
    ensureAudio()
    const prompt = input.trim()
    if (!prompt) return
    setInput('')
    onSend(prompt)
  }, [input, onSend, ensureAudio])

  const voice = useVoiceInput({
    onInterim: (text) => setInput(text),
    onFinal: () => handleSubmit(),
  })

  useEffect(() => {
    if (!disabled && inputRef.current) inputRef.current.focus()
  }, [disabled])

  return (
    <div className="composer-container">
      <form
        className="input-form"
        onSubmit={(e) => {
          e.preventDefault()
          handleSubmit()
        }}
      >
        <input
          type="text"
          ref={inputRef}
          placeholder="Ask Thursday something..."
          autoComplete="off"
          value={input}
          disabled={disabled}
          onChange={(e) => setInput(e.target.value)}
        />
        <button
          type="button"
          className={`composer-btn mic${voice.isRecording ? ' recording' : ''}`}
          title="Voice input"
          onClick={voice.toggle}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
          <span className={`mic-tooltip${voice.tooltipVisible ? ' visible' : ''}`}>
            {voice.tooltip}
          </span>
        </button>
        <button type="submit" className="composer-btn send" disabled={disabled}>
          Send
        </button>
      </form>
    </div>
  )
}
