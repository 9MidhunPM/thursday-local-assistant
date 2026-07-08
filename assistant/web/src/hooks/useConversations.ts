import { useCallback, useEffect, useState } from 'react'
import type { ConversationSummary } from '@/types'

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return (await res.json()) as T
}

interface UseConversationsResult {
  conversations: ConversationSummary[]
  activeId: number | null
  loading: boolean
  refresh: () => Promise<void>
  create: () => Promise<ConversationSummary>
  select: (id: number) => Promise<void>
  rename: (id: number, title: string) => Promise<void>
  remove: (id: number) => Promise<void>
  setActiveId: (id: number | null) => void
}

export function useConversations(
  onLoadConversation: (id: number, messages: import('@/types').HistoryMessage[]) => void,
): UseConversationsResult {
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const loadRef = useRefCallback(onLoadConversation)

  const refresh = useCallback(async () => {
    try {
      const list = await api<ConversationSummary[]>('/api/conversations')
      setConversations(list)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const create = useCallback(async () => {
    const conv = await api<ConversationSummary>('/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    setConversations((prev) => [conv, ...prev])
    setActiveId(conv.id)
    return conv
  }, [])

  const select = useCallback(
    async (id: number) => {
      setActiveId(id)
      const detail = await api<import('@/types').ConversationDetail>(`/api/conversations/${id}`)
      loadRef.current(id, detail.messages)
    },
    [loadRef],
  )

  const rename = useCallback(async (id: number, title: string) => {
    await api(`/api/conversations/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    })
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title } : c)),
    )
  }, [])

  const remove = useCallback(async (id: number) => {
    await api(`/api/conversations/${id}`, { method: 'DELETE' })
    setConversations((prev) => prev.filter((c) => c.id !== id))
    setActiveId((curr) => (curr === id ? null : curr))
  }, [])

  return {
    conversations,
    activeId,
    loading,
    refresh,
    create,
    select,
    rename,
    remove,
    setActiveId,
  }
}

function useRefCallback<T>(fn: T) {
  const ref = useState({ current: fn })[0]
  ref.current = fn
  return ref
}
