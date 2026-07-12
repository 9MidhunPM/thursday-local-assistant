import { useEffect, useState } from 'react'

export interface ConfirmRequest {
  id: string
  prompt: string
  timeout_sec?: number
}

interface Props {
  request: ConfirmRequest | null
  onResolved: () => void
}

export default function ConfirmBanner({ request, onResolved }: Props) {
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setBusy(false)
  }, [request?.id])

  if (!request) return null

  async function respond(approved: boolean) {
    if (!request || busy) return
    setBusy(true)
    try {
      await fetch('/api/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: request.id, approved }),
      })
    } catch (err) {
      console.error('Failed to send confirmation', err)
    } finally {
      onResolved()
      setBusy(false)
    }
  }

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="confirm-title"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0,0,0,0.55)',
        padding: 16,
      }}
    >
      <div
        style={{
          maxWidth: 440,
          width: '100%',
          background: 'var(--bg-elevated, #1a1d24)',
          border: '1px solid var(--border, #333)',
          borderRadius: 12,
          padding: 20,
          boxShadow: '0 12px 40px rgba(0,0,0,0.45)',
        }}
      >
        <h2 id="confirm-title" style={{ margin: '0 0 8px', fontSize: 16 }}>
          Confirm action
        </h2>
        <p style={{ margin: '0 0 16px', opacity: 0.9, lineHeight: 1.45 }}>{request.prompt}</p>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button type="button" disabled={busy} onClick={() => void respond(false)}>
            Deny
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void respond(true)}
            style={{ fontWeight: 600 }}
          >
            Allow
          </button>
        </div>
      </div>
    </div>
  )
}
