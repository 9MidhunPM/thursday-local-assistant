import { useState, type FormEvent } from 'react'

export default function ConnectionGate() {
  const [token, setToken] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  async function connect(event: FormEvent) {
    event.preventDefault()
    if (!token.trim() || pending) return
    setPending(true)
    setError('')
    try {
      const response = await fetch('/api/browser-session', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token.trim()}` },
      })
      setToken('')
      if (!response.ok) {
        setError('That token wasn’t accepted. Check it and try again.')
        return
      }
      window.location.reload()
    } catch {
      setError('Thursday is unavailable. Try again when it is running.')
    } finally {
      setPending(false)
    }
  }

  return (
    <main className="connection-gate">
      <form onSubmit={(event) => { void connect(event) }}>
        <h1>Connect to Thursday</h1>
        <p>Enter the local access token configured for Thursday.</p>
        <label htmlFor="local-access-token">Local access token</label>
        <input
          id="local-access-token"
          type="password"
          autoComplete="off"
          autoFocus
          required
          value={token}
          onChange={(event) => setToken(event.target.value)}
          disabled={pending}
        />
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={pending || !token.trim()}>
          {pending ? 'Connecting…' : 'Connect'}
        </button>
      </form>
    </main>
  )
}
