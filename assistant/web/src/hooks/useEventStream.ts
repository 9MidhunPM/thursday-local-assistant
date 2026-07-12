import { useEffect, useRef } from 'react'
import type { AgentEvent } from '@/types'
import type { ChatAction } from '@/state/chatReducer'

interface Options {
  dispatch: (action: ChatAction) => void
  onTtsAudio: (url: string, text: string) => void
  onTtsStop: () => void
  onStopVisualizer: () => void
  onInitConversation: (id: number | null) => void
  onConversationUpdated: (id: number, title: string) => void
  onConversationDeleted: (id: number) => void
  onConfirmRequired?: (data: { id: string; prompt: string; timeout_sec?: number }) => void
  onConfirmResolved?: (data: { id: string; approved: boolean }) => void
}

export function useEventStream({
  dispatch,
  onTtsAudio,
  onTtsStop,
  onStopVisualizer,
  onInitConversation,
  onConversationUpdated,
  onConversationDeleted,
  onConfirmRequired,
  onConfirmResolved,
}: Options) {
  const sourceRef = useRef<EventSource | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const stoppedRef = useRef(false)

  // Keep latest callbacks without re-opening the stream.
  const cb = useRef({
    dispatch,
    onTtsAudio,
    onTtsStop,
    onStopVisualizer,
    onInitConversation,
    onConversationUpdated,
    onConversationDeleted,
    onConfirmRequired,
    onConfirmResolved,
  })
  cb.current = {
    dispatch,
    onTtsAudio,
    onTtsStop,
    onStopVisualizer,
    onInitConversation,
    onConversationUpdated,
    onConversationDeleted,
    onConfirmRequired,
    onConfirmResolved,
  }

  useEffect(() => {
    stoppedRef.current = false
    let reconnectDelay = 1000

    function handle(event: AgentEvent) {
      const {
        dispatch: d,
        onTtsAudio: audio,
        onTtsStop: stop,
        onStopVisualizer: stopVis,
        onInitConversation: initConv,
        onConversationUpdated: convUpd,
        onConversationDeleted: convDel,
        onConfirmRequired: confReq,
        onConfirmResolved: confRes,
      } = cb.current
      switch (event.type) {
        case 'init':
          d({
            type: 'INIT',
            busy: event.data.busy,
            modelReady: event.data.model_ready,
            logs: event.data.logs,
            history: event.data.history,
          })
          initConv(event.data.conversation_id ?? null)
          break
        case 'model_ready':
          d({ type: 'SET_MODEL_READY' })
          break
        case 'model_log':
          d({ type: 'APPEND_LOG', line: event.data.line })
          break
        case 'status':
          d({ type: 'SET_BUSY', busy: event.data.busy })
          break
        case 'user_message':
          d({ type: 'APPEND_USER_MESSAGE', content: event.data.content })
          break
        case 'token':
          d({ type: 'APPEND_TOKEN', chunk: event.data.chunk })
          break
        case 'tool_call':
          d({ type: 'SET_TOOL_ARGS', tool: event.data.tool, args: event.data.arguments })
          break
        case 'tool_chunk':
          d({ type: 'ADD_TOOL_CHUNK', tool: event.data.tool, chunk: event.data.chunk })
          break
        case 'tool_result':
          d({ type: 'SET_TOOL_RESULT', data: event.data })
          break
        case 'final_response':
          d({ type: 'FINALIZE_STREAMING', content: event.data.content })
          break
        case 'tts_audio':
          audio(event.data.url, event.data.text)
          break
        case 'tts_stop':
          stop()
          break
        case 'error':
          d({ type: 'SHOW_AGENT_ERROR', content: event.data.content })
          stopVis()
          break
        case 'conversation_updated':
          convUpd(event.data.id, event.data.title)
          break
        case 'conversation_deleted':
          convDel(event.data.id)
          break
        case 'confirm_required':
          confReq?.(event.data)
          break
        case 'confirm_resolved':
          confRes?.(event.data)
          break
        case 'shutdown':
          d({ type: 'SET_STATUS', status: 'reconnecting' })
          break
      }
    }

    function connect() {
      if (stoppedRef.current) return
      const es = new EventSource(`${window.location.origin}/api/events`)
      sourceRef.current = es

      es.onopen = () => {
        reconnectDelay = 1000
      }

      es.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data) as AgentEvent
          handle(event)
        } catch (err) {
          console.error('Failed to parse SSE event', err)
        }
      }

      es.onerror = () => {
        cb.current.dispatch({ type: 'SET_STATUS', status: 'reconnecting' })
        try {
          es.close()
        } catch {
          /* ignore */
        }
        sourceRef.current = null
        if (stoppedRef.current) return
        reconnectTimer.current = setTimeout(() => {
          if (reconnectDelay < 8000) reconnectDelay *= 2
          connect()
        }, reconnectDelay)
      }
    }

    connect()

    return () => {
      stoppedRef.current = true
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      if (sourceRef.current) {
        try {
          sourceRef.current.close()
        } catch {
          /* ignore */
        }
      }
      sourceRef.current = null
    }
  }, [])
}
