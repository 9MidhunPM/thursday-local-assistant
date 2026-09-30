import { useEffect, useRef, useState } from 'react'

interface Assertion {
  id: number; kind: string; payload: Record<string, string>; evidence: string
  conversation_id: number | null; message_id: number | null; created_at: string; status: string
}

export default function MemoryReview({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [items, setItems] = useState<Assertion[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [pending, setPending] = useState<number | null>(null)
  async function refresh() {
    setLoading(true)
    try {
      const response = await fetch('/api/memory')
      if (!response.ok) throw new Error('Unable to load memory')
      setItems((await response.json()).items)
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to load memory') }
    finally { setLoading(false) }
  }
  useEffect(() => {
    if (visible) {
      dialog.current?.showModal()
      setError('')
      void refresh()
    } else dialog.current?.close()
  }, [visible])
  async function update(id: number, value?: string) {
    setPending(id)
    setError('')
    try {
      const response = await fetch(`/api/memory/${id}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(value == null ? {} : { value }),
      })
      if (!response.ok) throw new Error('Unable to change memory')
      await refresh()
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to change memory') }
    finally { setPending(null) }
  }
  return <dialog ref={dialog} className="memory-review" aria-labelledby="memory-title" onCancel={onClose} onClose={onClose}>
    <div className="memory-heading"><div><h2 id="memory-title">What Thursday remembers</h2>
      <p>Review personal facts and saved notes, check their sources, correct them, or forget them.</p></div>
      <button onClick={onClose} aria-label="Close memory review">Close</button></div>
    {error && <p role="alert">{error}</p>}
    {loading && <p role="status">Loading memory…</p>}
    {!loading && !items.length && <p>No grounded facts saved yet. Tell Thursday a preference or ask it to remember something.</p>}
    {items.map((item) => <article className="memory-entry" key={item.id}>
      <strong>{item.payload.key || item.payload.name || `${item.payload.subject} ${item.payload.predicate}`}</strong>
      <span className="memory-status">{item.status}</span>
      <form onSubmit={(event) => {
        event.preventDefault()
        const value = new FormData(event.currentTarget).get('value') as string
        void update(item.id, value)
      }}>
        <input key={`${item.id}:${item.status}:${item.payload.value || item.payload.object || item.payload.content}`}
          name="value" aria-label={`Value for ${item.payload.key || item.payload.name || item.payload.predicate}`}
          defaultValue={item.payload.value || item.payload.object || item.payload.content} required maxLength={500} disabled={item.status !== 'current'} />
        {item.status === 'current' && <><button disabled={pending != null}>Save correction</button>
          <button type="button" disabled={pending != null} onClick={() => {
            if (window.confirm('Forget this personal fact?')) void update(item.id)
          }}>Forget</button></>}
      </form>
      <blockquote>{item.evidence}</blockquote>
      <small>{new Date(item.created_at).toLocaleDateString()} · {item.conversation_id ? `Conversation ${item.conversation_id}, message ${item.message_id ?? 'unknown'}` : 'Explicit correction'}</small>
    </article>)}
  </dialog>
}
