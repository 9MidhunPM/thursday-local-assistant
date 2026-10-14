import { useEffect, useRef, useState } from 'react'

export interface HealthInfo {
  modelReady: boolean
  busy: boolean
  provider: string
  model: string
  mode: string
  clients: number
  authRequired: boolean
}

const EMPTY: HealthInfo = {
  modelReady: false,
  busy: false,
  provider: '',
  model: '',
  mode: '',
  clients: 0,
  authRequired: false,
}

export function useHealth(): HealthInfo {
  const [info, setInfo] = useState<HealthInfo>(EMPTY)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    let cancelled = false

    async function fetchHealth() {
      try {
        const res = await fetch('/health', { cache: 'no-store' })
        if (!res.ok) return
        const data = (await res.json()) as Partial<HealthInfo> & {
          status?: string
          model_ready?: boolean
          tts_active?: boolean
          auth_required?: boolean
        }
        if (cancelled) return
        // The HttpOnly session is scoped to /api; check it there rather than
        // relying on a cookie being sent to the public health endpoint.
        const authRequired = data.auth_required
          ? !(await fetch('/api/browser-session', { cache: 'no-store' })).ok
          : false
        if (cancelled) return
        setInfo({
          modelReady: data.model_ready ?? data.modelReady ?? false,
          busy: data.busy ?? false,
          provider: data.provider ?? '',
          model: data.model ?? '',
          mode: data.mode ?? '',
          clients: data.clients ?? 0,
          authRequired,
        })
      } catch {
        /* server may briefly be down; keep last known state */
      }
    }

    fetchHealth()
    timer.current = setInterval(fetchHealth, 5000)

    return () => {
      cancelled = true
      if (timer.current) clearInterval(timer.current)
      timer.current = null
    }
  }, [])

  return info
}
