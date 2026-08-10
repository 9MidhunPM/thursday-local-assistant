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
    <div className="confirm-overlay" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
      <div className="confirm-card">
        <h2 id="confirm-title" className="confirm-title">
          Confirm action
        </h2>
        <p className="confirm-body">{request.prompt}</p>
        <div className="confirm-actions">
          <button type="button" className="confirm-btn" disabled={busy} onClick={() => void respond(false)}>
            Deny
          </button>
          <button type="button" className="confirm-btn primary" disabled={busy} onClick={() => void respond(true)}>
            Allow
          </button>
        </div>
      </div>
    </div>
  )
}
