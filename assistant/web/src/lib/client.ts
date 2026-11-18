// One playback owner per document. Tabs never share an audio identity.
export const clientId = crypto.randomUUID()
let taskViewSequence = 0

export function reportTaskView(conversationId: number | null) {
  void fetch('/api/task-view', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      view_id: clientId,
      sequence: ++taskViewSequence,
      conversation_id: conversationId,
    }),
    keepalive: true,
  }).catch(() => {})
}

export function reportPlayback(generationId: string, state: 'playing' | 'idle' | 'failed') {
  void fetch('/api/playback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_id: clientId, generation_id: generationId, state }),
  }).catch(() => {})
}
