import { useCallback, useEffect, useRef, useState } from 'react'
import Mark from 'mark.js'
import { reportPlayback } from '@/lib/client'

interface QueueItem {
  buffer: AudioBuffer
  text: string
  generationId?: string
}

export function useAudioEngine() {
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const sourceRef = useRef<AudioBufferSourceNode | null>(null)
  const queueRef = useRef<QueueItem[]>([])
  const isPlayingRef = useRef(false)
  const epochRef = useRef(0)
  const fetchChainRef = useRef<Promise<void>>(Promise.resolve())
  const markInstanceRef = useRef<Mark | null>(null)
  const lastAgentContentRef = useRef<HTMLElement | null>(null)
  const stopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const [isPlaying, setIsPlaying] = useState(false)
  const [isPreparing, setIsPreparing] = useState(false)

  const initAudio = useCallback(() => {
    try {
      if (!audioCtxRef.current) {
        const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
        const ctx = new Ctx()
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 128
        analyser.smoothingTimeConstant = 0.7
        analyser.connect(ctx.destination)
        audioCtxRef.current = ctx
        analyserRef.current = analyser
      }
      if (audioCtxRef.current.state === 'suspended') {
        void audioCtxRef.current.resume()
      }
    } catch (e) {
      console.warn('AudioContext could not be initialized:', e)
    }
  }, [])

  // Initialise the audio context on the first user interaction (autoplay policy).
  useEffect(() => {
    const onFirst = () => initAudio()
    document.addEventListener('click', onFirst, { once: true })
    return () => document.removeEventListener('click', onFirst)
  }, [initAudio])

  const registerAgentContent = useCallback((el: HTMLElement | null) => {
    if (el) {
      lastAgentContentRef.current = el
      if (markInstanceRef.current) {
        markInstanceRef.current.unmark()
        markInstanceRef.current = null
      }
    }
  }, [])

  const clearHighlight = useCallback(() => {
    if (markInstanceRef.current) {
      markInstanceRef.current.unmark()
      markInstanceRef.current = null
    }
  }, [])

  const stopVisualizer = useCallback(() => {
    if (stopTimerRef.current) {
      clearTimeout(stopTimerRef.current)
      stopTimerRef.current = null
    }
    stopTimerRef.current = setTimeout(() => {
      if (!isPlayingRef.current) {
        setIsPlaying(false)
      }
    }, 600)
  }, [])

  const playNextAudio = useCallback(() => {
    if (isPlayingRef.current || queueRef.current.length === 0) {
      if (!isPlayingRef.current && queueRef.current.length === 0) {
        clearHighlight()
      }
      return
    }

    isPlayingRef.current = true
    setIsPlaying(true)
    if (stopTimerRef.current) {
      clearTimeout(stopTimerRef.current)
      stopTimerRef.current = null
    }

    const ctx = audioCtxRef.current!
    const analyser = analyserRef.current!
    const item = queueRef.current.shift()!
    const source = ctx.createBufferSource()
    sourceRef.current = source
    source.buffer = item.buffer
    source.connect(analyser)

    // Highlight the spoken text using mark.js on the active agent bubble.
    const root = lastAgentContentRef.current
    if (root) {
      if (markInstanceRef.current) {
        markInstanceRef.current.unmark()
      }
      markInstanceRef.current = new Mark(root)
      if (item.text) {
        markInstanceRef.current.mark(item.text, {
          className: 'tts-highlight',
          separateWordSearch: false,
          accuracy: 'partially',
        })
      }
    }

    source.onended = () => {
      if (sourceRef.current !== source) return
      sourceRef.current = null
      isPlayingRef.current = false
      if (queueRef.current.length > 0) {
        playNextAudio()
      } else {
        stopVisualizer()
        clearHighlight()
        if (item.generationId) reportPlayback(item.generationId, 'idle')
      }
    }

    source.start(0)
    if (item.generationId) reportPlayback(item.generationId, 'playing')
  }, [clearHighlight, stopVisualizer])

  const queueAudio = useCallback(
    (url: string, text: string, generationId?: string) => {
      const epoch = epochRef.current
      const work = async () => {
        if (epoch !== epochRef.current) return
        initAudio()
        setIsPreparing(true)
        try {
          const response = await fetch(url)
          if (!response.ok) throw new Error('Failed to fetch audio')
          const arrayBuffer = await response.arrayBuffer()
          const ctx = audioCtxRef.current
          if (!ctx) {
            setIsPreparing(false)
            return
          }
          const audioBuffer = await ctx.decodeAudioData(arrayBuffer)
          if (epoch !== epochRef.current) return
          queueRef.current.push({ buffer: audioBuffer, text, generationId })
          setIsPreparing(false)
          playNextAudio()
        } catch (err) {
          if (epoch !== epochRef.current) return
          setIsPreparing(false)
          console.error('Audio playback error:', err)
          if (generationId) reportPlayback(generationId, 'failed')
        }
      }
      fetchChainRef.current = fetchChainRef.current.then(work, work)
      return fetchChainRef.current
    },
    [initAudio, playNextAudio],
  )

  const stopAll = useCallback(() => {
    epochRef.current += 1
    fetchChainRef.current = Promise.resolve()
    queueRef.current = []
    if (sourceRef.current) {
      try {
        sourceRef.current.stop()
        sourceRef.current.disconnect()
      } catch {
        /* already stopped */
      }
      sourceRef.current = null
    }
    isPlayingRef.current = false
    setIsPlaying(false)
    setIsPreparing(false)
    clearHighlight()
  }, [clearHighlight])

  const ensureAudio = useCallback(() => {
    initAudio()
  }, [initAudio])

  const prepareAudio = useCallback(() => {
    initAudio()
    setIsPreparing(true)
  }, [initAudio])

  useEffect(() => {
    return () => {
      epochRef.current += 1
      queueRef.current = []
      if (stopTimerRef.current) clearTimeout(stopTimerRef.current)
      if (sourceRef.current) {
        try {
          sourceRef.current.stop()
        } catch {
          /* already stopped */
        }
      }
      try {
        audioCtxRef.current?.close()
      } catch {
        /* ignore */
      }
    }
  }, [])

  return {
    isPlaying,
    isPreparing,
    analyserRef,
    queueAudio,
    stopAll,
    stopVisualizer,
    ensureAudio,
    prepareAudio,
    finishPreparing: () => setIsPreparing(false),
    registerAgentContent,
  }
}
