import type {
  AgentItem,
  ChatItem,
  ChatState,
  HistoryMessage,
  ToolResultData,
} from '@/types'
import { formatToolArgs, parseToolResult, uid } from '@/lib/utils'

const WELCOME = 'Welcome back. I am Thursday, your local personal assistant. How can I help you today?'

function withoutThinking(items: ChatItem[]): ChatItem[] {
  return items.filter((i) => i.kind !== 'thinking')
}

function findLastRunningTool(items: ChatItem[], tool: string): ToolItemIndex {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]
    if (it.kind === 'tool' && it.status === 'running' && it.tool === tool) {
      return { index: i, item: it }
    }
  }
  return { index: -1, item: null }
}

interface ToolItemIndex {
  index: number
  item: Extract<ChatItem, { kind: 'tool' }> | null
}

export type ChatAction =
  | { type: 'INIT'; busy: boolean; modelReady: boolean; logs: string[]; history: HistoryMessage[] }
  | { type: 'LOAD_CONVERSATION'; history: HistoryMessage[] }
  | { type: 'APPEND_LOG'; line: string }
  | { type: 'SET_MODEL_READY' }
  | { type: 'SET_BUSY'; busy: boolean }
  | { type: 'SET_STATUS'; status: ChatState['status'] }
  | { type: 'APPEND_USER_MESSAGE'; content: string }
  | { type: 'START_THINKING' }
  | { type: 'REMOVE_THINKING' }
  | { type: 'APPEND_TOKEN'; chunk: string }
  | { type: 'ADD_TOOL_CHUNK'; tool: string; chunk: string }
  | { type: 'SET_TOOL_ARGS'; tool: string; args: string | Record<string, unknown> }
  | { type: 'SET_TOOL_RESULT'; data: ToolResultData }
  | { type: 'FINALIZE_STREAMING'; content?: string }
  | { type: 'SHOW_AGENT_ERROR'; content: string }
  | { type: 'CLEAR_CHAT' }

export const initialState: ChatState = {
  items: [{ id: uid(), kind: 'agent', content: WELCOME }],
  activeAgentId: null,
  status: 'connecting',
  modelReady: false,
  logs: [],
}

function rebuildFromHistory(history: HistoryMessage[]): ChatItem[] {
  const items: ChatItem[] = []
  for (const msg of history) {
    if (msg.role === 'user') {
      if (msg.content) items.push({ id: uid(), kind: 'user', content: msg.content })
    } else if (msg.role === 'assistant') {
      if (msg.content) {
        items.push({ id: uid(), kind: 'agent', content: msg.content })
      }
      if (msg.tool_calls) {
        for (const tc of msg.tool_calls) {
          items.push({
            id: uid(),
            kind: 'tool',
            tool: tc.name,
            status: 'running',
            args: formatToolArgs(tc.arguments),
          })
        }
      }
    } else if (msg.role === 'tool') {
      const toolName = msg.tool_name || 'tool'
      const { text, isError } = parseToolResult({
        tool: toolName,
        result: msg.content || '',
      })
      // Try to attach to the last running card for this tool; otherwise create one.
      let attached = false
      for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i]
        if (it.kind === 'tool' && it.status === 'running' && it.tool === toolName) {
          it.status = isError ? 'error' : 'success'
          it.result = text
          attached = true
          break
        }
      }
      if (!attached) {
        items.push({
          id: uid(),
          kind: 'tool',
          tool: toolName,
          status: isError ? 'error' : 'success',
          args: '',
          result: text,
        })
      }
    }
  }
  return items.length > 0 ? items : [{ id: uid(), kind: 'agent', content: WELCOME }]
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'INIT': {
      return {
        ...state,
        items: rebuildFromHistory(action.history),
        activeAgentId: null,
        logs: action.logs.slice(),
        modelReady: action.modelReady,
        status: action.busy ? 'busy' : 'ready',
      }
    }

    case 'APPEND_LOG':
      return { ...state, logs: [...state.logs, action.line] }

    case 'SET_MODEL_READY':
      return { ...state, modelReady: true }

    case 'SET_BUSY':
      return { ...state, status: action.busy ? 'busy' : 'ready' }

    case 'LOAD_CONVERSATION':
      return {
        ...state,
        items: rebuildFromHistory(action.history),
        activeAgentId: null,
        status: 'ready',
      }

    case 'SET_STATUS':
      return { ...state, status: action.status }

    case 'APPEND_USER_MESSAGE': {
      const lastUser = [...state.items].reverse().find((i) => i.kind === 'user')
      if (lastUser && lastUser.kind === 'user' && lastUser.content.includes(action.content)) {
        return state
      }
      return {
        ...state,
        items: [...state.items, { id: uid(), kind: 'user', content: action.content }],
      }
    }

    case 'START_THINKING':
      return { ...state, items: [...state.items, { id: uid(), kind: 'thinking' }] }

    case 'REMOVE_THINKING':
      if (!state.items.some((i) => i.kind === 'thinking')) return state
      return { ...state, items: withoutThinking(state.items) }

    case 'APPEND_TOKEN': {
      const cleaned = withoutThinking(state.items)
      if (state.activeAgentId) {
        const items = cleaned.map((i) =>
          i.id === state.activeAgentId && i.kind === 'agent'
            ? { ...i, content: i.content + action.chunk }
            : i,
        )
        return { ...state, items }
      }
      const newAgent: AgentItem = {
        id: uid(),
        kind: 'agent',
        content: action.chunk,
        streaming: true,
      }
      return { ...state, items: [...cleaned, newAgent], activeAgentId: newAgent.id }
    }

    case 'ADD_TOOL_CHUNK': {
      const cleaned = withoutThinking(state.items)
      const found = findLastRunningTool(cleaned, action.tool)
      if (found.item && found.index >= 0) {
        const items = cleaned.slice()
        items[found.index] = { ...found.item, args: found.item.args + action.chunk }
        return { ...state, items }
      }
      return {
        ...state,
        items: [
          ...cleaned,
          { id: uid(), kind: 'tool', tool: action.tool, status: 'running', args: action.chunk },
        ],
      }
    }

    case 'SET_TOOL_ARGS': {
      const cleaned = withoutThinking(state.items)
      const formatted = formatToolArgs(action.args)
      const found = findLastRunningTool(cleaned, action.tool)
      if (found.item && found.index >= 0) {
        const items = cleaned.slice()
        items[found.index] = { ...found.item, args: formatted }
        return { ...state, items }
      }
      return {
        ...state,
        items: [
          ...cleaned,
          { id: uid(), kind: 'tool', tool: action.tool, status: 'running', args: formatted },
        ],
      }
    }

    case 'SET_TOOL_RESULT': {
      const { text, isError } = parseToolResult(action.data)
      const found = findLastRunningTool(state.items, action.data.tool)
      if (found.item && found.index >= 0) {
        const items = state.items.slice()
        items[found.index] = {
          ...found.item,
          status: isError ? 'error' : 'success',
          result: text,
        }
        return { ...state, items }
      }
      return {
        ...state,
        items: [
          ...state.items,
          {
            id: uid(),
            kind: 'tool',
            tool: action.data.tool,
            status: isError ? 'error' : 'success',
            args: '',
            result: text,
          },
        ],
      }
    }

    case 'FINALIZE_STREAMING': {
      if (state.activeAgentId) {
        const items = state.items.map((i) =>
          i.id === state.activeAgentId && i.kind === 'agent' ? { ...i, streaming: false } : i,
        )
        return { ...state, items, activeAgentId: null }
      }
      if (action.content !== undefined) {
        return {
          ...state,
          items: withoutThinking(state.items).concat({
            id: uid(),
            kind: 'agent',
            content: action.content,
          }),
        }
      }
      return state
    }

    case 'SHOW_AGENT_ERROR': {
      const cleaned = withoutThinking(state.items)
      if (state.activeAgentId) {
        const items = cleaned.map((i) =>
          i.id === state.activeAgentId && i.kind === 'agent'
            ? { ...i, content: action.content, streaming: false, error: true }
            : i,
        )
        return { ...state, items, activeAgentId: null }
      }
      return {
        ...state,
        items: cleaned.concat({
          id: uid(),
          kind: 'agent',
          content: action.content,
          error: true,
        }),
      }
    }

    case 'CLEAR_CHAT':
      return {
        ...state,
        activeAgentId: null,
        items: [
          { id: uid(), kind: 'agent', content: "Fresh slate. I still remember what you've taught me." },
        ],
      }

    default:
      return state
  }
}
