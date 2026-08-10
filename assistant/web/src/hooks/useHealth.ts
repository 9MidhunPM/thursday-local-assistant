import { useEffect, useRef, useState } from 'react'

export interface HealthInfo {
  modelReady: boolean
  busy: boolean
  provider: string
  model: string
  mode: string
  clients: number
}

const EMPTY: HealthInfo = {
  modelReady: false,
  busy: false,
  provider: '',
  model: '',
  mode: '',
  clients: 0,
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
        }
        if (cancelled) return
        setInfo({
          modelReady: data.model_ready ?? data.modelReady ?? false,
          busy: data.busy ?? false,
          provider: data.provider ?? '',
          model: data.model ?? '',
          mode: data.mode ?? '',
          clients: data.clients ?? 0,
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
