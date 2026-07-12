import { useCallback, useReducer, useState } from 'react'
import Header from '@/components/Header'
import ChatHistory from '@/components/ChatHistory'
import Composer from '@/components/Composer'
import LogsModal from '@/components/LogsModal'
import VoiceVisualizer from '@/components/VoiceVisualizer'
import Sidebar from '@/components/Sidebar'
import ConfirmBanner, { type ConfirmRequest } from '@/components/ConfirmBanner'
import { useAudioEngine } from '@/hooks/useAudioEngine'
import { useEventStream } from '@/hooks/useEventStream'
import { useConversations } from '@/hooks/useConversations'
import { chatReducer, initialState } from '@/state/chatReducer'
import type { HistoryMessage } from '@/types'

const TTS_STORAGE_KEY = 'ttsEnabled'

export default function App() {
  const [state, dispatch] = useReducer(chatReducer, initialState)
  const [showLogs, setShowLogs] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null)
  const [ttsEnabled, setTtsEnabled] = useState<boolean>(() => {
    try {
      return localStorage.getItem(TTS_STORAGE_KEY) === 'true'
    } catch {
      return false
    }
  })

  const audio = useAudioEngine()

  const handleLoadConversation = useCallback(
    (_id: number, messages: HistoryMessage[]) => {
      dispatch({ type: 'LOAD_CONVERSATION', history: messages })
    },
    [],
  )

  const conversations = useConversations(handleLoadConversation)

  useEventStream({
    dispatch,
    onTtsAudio: (url, text) => audio.queueAudio(url, text),
    onTtsStop: () => {},
    onStopVisualizer: () => audio.stopVisualizer(),
    onInitConversation: (id) => conversations.setActiveId(id),
    onConversationUpdated: () => {
      void conversations.refresh()
    },
    onConversationDeleted: () => {
      void conversations.refresh()
    },
    onConfirmRequired: (data) => setConfirmReq(data),
    onConfirmResolved: () => setConfirmReq(null),
  })

  const handleToggleTts = useCallback((enabled: boolean) => {
    setTtsEnabled(enabled)
    try {
      localStorage.setItem(TTS_STORAGE_KEY, String(enabled))
    } catch {
      /* ignore */
    }
    if (enabled) audio.ensureAudio()
  }, [audio])

  const handleClear = useCallback(() => {
    dispatch({ type: 'CLEAR_CHAT' })
    audio.stopAll()
  }, [audio])

  const handleSend = useCallback(
    async (prompt: string) => {
      audio.ensureAudio()
      dispatch({ type: 'APPEND_USER_MESSAGE', content: prompt })
      dispatch({ type: 'START_THINKING' })
      try {
        const response = await fetch('/api/message', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt,
            tts: ttsEnabled,
            conversation_id: conversations.activeId,
          }),
        })
        if (!response.ok) {
          const text = await response.text()
          throw new Error(text || 'Unable to send message')
        }
        const data = (await response.json()) as { conversation_id?: number }
        if (data.conversation_id != null) {
          conversations.setActiveId(data.conversation_id)
        }
        // Refresh the sidebar so the auto-generated title shows up.
        void conversations.refresh()
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        dispatch({ type: 'SHOW_AGENT_ERROR', content: msg })
      }
    },
    [audio, ttsEnabled, conversations],
  )

  const handleNewChat = useCallback(async () => {
    audio.stopAll()
    dispatch({ type: 'CLEAR_CHAT' })
    await conversations.create()
    setSidebarOpen(false)
  }, [audio, conversations])

  const handleSelect = useCallback(
    async (id: number) => {
      audio.stopAll()
      await conversations.select(id)
      setSidebarOpen(false)
    },
    [audio, conversations],
  )

  const busy = state.status === 'busy'

  return (
    <>
      <Header
        status={state.status}
        ttsEnabled={ttsEnabled}
        onToggleTts={handleToggleTts}
        onClear={handleClear}
        onToggleLogs={() => setShowLogs((v) => !v)}
        onToggleSidebar={() => setSidebarOpen((v) => !v)}
      />
      <div className="app-container">
        <div className="chat-panel">
          <ChatHistory items={state.items} registerAgentContent={audio.registerAgentContent} />
          <VoiceVisualizer active={audio.isPlaying} analyserRef={audio.analyserRef} />
          <Composer disabled={busy} onSend={handleSend} ensureAudio={audio.ensureAudio} />
        </div>
      </div>
      <Sidebar
        open={sidebarOpen}
        conversations={conversations.conversations}
        activeId={conversations.activeId}
        busy={busy}
        onClose={() => setSidebarOpen(false)}
        onNew={handleNewChat}
        onSelect={handleSelect}
        onRename={conversations.rename}
        onDelete={conversations.remove}
      />
      <LogsModal visible={showLogs} logs={state.logs} onClose={() => setShowLogs(false)} />
      <ConfirmBanner request={confirmReq} onResolved={() => setConfirmReq(null)} />
    </>
  )
}
