export type ToolStatus = 'running' | 'success' | 'error'

export interface BaseItem {
  id: string
}

export interface UserItem extends BaseItem {
  kind: 'user'
  content: string
}

export interface AgentItem extends BaseItem {
  kind: 'agent'
  content: string
  streaming?: boolean
  error?: boolean
}

export interface ThinkingItem extends BaseItem {
  kind: 'thinking'
}

export interface ToolItem extends BaseItem {
  kind: 'tool'
  tool: string
  status: ToolStatus
  args: string
  result?: string
  errorText?: string
  returnCode?: number | null
  command?: string
  durationMs?: number | null
}

export type ChatItem = UserItem | AgentItem | ThinkingItem | ToolItem

export interface ConversationSummary {
  id: number
  title: string
  created_at: string
  updated_at: string
}

export interface ConversationDetail extends ConversationSummary {
  messages: HistoryMessage[]
}

export type ConnStatus = 'connecting' | 'ready' | 'busy' | 'reconnecting'

export interface ChatState {
  items: ChatItem[]
  activeAgentId: string | null
  status: ConnStatus
  modelReady: boolean
  logs: string[]
}

/* ===== SSE event payload shapes (mirror assistant/server.py) ===== */

export interface InitData {
  busy: boolean
  model_ready: boolean
  logs: string[]
  history: HistoryMessage[]
  conversation_id?: number | null
}

export interface HistoryMessage {
  role: 'user' | 'assistant' | 'tool' | 'system'
  content?: string
  tool_calls?: { name: string; arguments: string | Record<string, unknown> }[]
  tool_name?: string
}

export interface ToolResultData {
  tool: string
  success?: boolean
  result?: string
  output?: unknown
  error?: unknown
  [key: string]: unknown
}

export interface ConfirmRequiredData {
  id: string
  prompt: string
  timeout_sec?: number
}

export type AgentEvent =
  | { type: 'init'; data: InitData }
  | { type: 'model_ready'; data: Record<string, never> }
  | { type: 'model_log'; data: { line: string } }
  | { type: 'status'; data: { busy: boolean } }
  | { type: 'user_message'; data: { content: string } }
  | { type: 'token'; data: { chunk: string } }
  | { type: 'tool_call'; data: { tool: string; arguments: string | Record<string, unknown> } }
  | { type: 'tool_chunk'; data: { tool: string; chunk: string } }
  | { type: 'tool_result'; data: ToolResultData }
  | { type: 'final_response'; data: { content: string } }
  | { type: 'tts_audio'; data: { url: string; text: string } }
  | { type: 'tts_stop'; data: Record<string, never> }
  | { type: 'error'; data: { content: string } }
  | { type: 'conversation_updated'; data: { id: number; title: string } }
  | { type: 'conversation_deleted'; data: { id: number } }
  | { type: 'confirm_required'; data: ConfirmRequiredData }
  | { type: 'confirm_resolved'; data: { id: string; approved: boolean } }
  | { type: 'shutdown'; data: { reason?: string } }
