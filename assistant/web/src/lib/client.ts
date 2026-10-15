// One playback owner per document. Tabs never share an audio identity.
export const clientId = crypto.randomUUID()

export function reportPlayback(generationId: string, state: 'playing' | 'idle' | 'failed') {
  void fetch('/api/playback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_id: clientId, generation_id: generationId, state }),
  }).catch(() => {})
}
