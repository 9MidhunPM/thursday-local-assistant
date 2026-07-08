import { useCallback, useEffect, useRef, useState } from 'react'

interface Options {
  onInterim: (text: string) => void
  onFinal: () => void
}

const DEFAULT_TOOLTIP = 'Click to speak'

export function useVoiceInput({ onInterim, onFinal }: Options) {
  const [isRecording, setIsRecording] = useState(false)
  const [tooltip, setTooltip] = useState(DEFAULT_TOOLTIP)
  const [tooltipVisible, setTooltipVisible] = useState(false)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  const localCtxRef = useRef<AudioContext | null>(null)
  const silenceIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const transcribeIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onFinalRef = useRef(onFinal)
  onFinalRef.current = onFinal
  const onInterimRef = useRef(onInterim)
  onInterimRef.current = onInterim

  const flashTooltip = useCallback((msg: string, ms = 2000) => {
    setTooltip(msg)
    setTooltipVisible(true)
    setTimeout(() => {
      setTooltip(DEFAULT_TOOLTIP)
      setTooltipVisible(false)
    }, ms)
  }, [])

  const stop = useCallback(() => {
    const rec = mediaRecorderRef.current
    if (rec && rec.state !== 'inactive') {
      rec.stop()
    }
  }, [])

  const start = useCallback(async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      flashTooltip('Voice input not supported')
      return
    }

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      flashTooltip('Microphone access denied')
      return
    }

    streamRef.current = stream
    setIsRecording(true)
    setTooltip('Listening...')
    setTooltipVisible(true)
    audioChunksRef.current = []

    let recorder: MediaRecorder
    try {
      recorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' })
    } catch {
      recorder = new MediaRecorder(stream)
    }
    mediaRecorderRef.current = recorder

    // Local analyser for silence detection.
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    const localCtx = new Ctx()
    localCtxRef.current = localCtx
    const sourceNode = localCtx.createMediaStreamSource(stream)
    const localAnalyser = localCtx.createAnalyser()
    localAnalyser.fftSize = 256
    sourceNode.connect(localAnalyser)
    const dataArray = new Uint8Array(localAnalyser.frequencyBinCount)

    let isSilent = false
    silenceIntervalRef.current = setInterval(() => {
      localAnalyser.getByteFrequencyData(dataArray)
      let sum = 0
      for (let i = 0; i < dataArray.length; i++) sum += dataArray[i]
      const avg = sum / dataArray.length
      if (avg < 5) {
        if (!isSilent) {
          isSilent = true
          silenceTimerRef.current = setTimeout(() => {
            const rec = mediaRecorderRef.current
            if (rec && rec.state !== 'inactive') rec.stop()
          }, 3000)
        }
      } else if (isSilent) {
        isSilent = false
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current)
          silenceTimerRef.current = null
        }
      }
    }, 200)

    // Interim transcription polling.
    transcribeIntervalRef.current = setInterval(async () => {
      if (audioChunksRef.current.length === 0) return
      const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
      if (blob.size < 1000) return
      try {
        const res = await fetch('/api/transcribe', { method: 'POST', body: blob })
        const result = (await res.json()) as { text?: string; error?: string }
        if (result.text) onInterimRef.current(result.text)
      } catch {
        /* ignore interim errors */
      }
    }, 1500)

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) audioChunksRef.current.push(event.data)
    }

    recorder.onstop = async () => {
      setIsRecording(false)
      setTooltipVisible(false)
      setTooltip(DEFAULT_TOOLTIP)
      if (silenceIntervalRef.current) {
        clearInterval(silenceIntervalRef.current)
        silenceIntervalRef.current = null
      }
      if (transcribeIntervalRef.current) {
        clearInterval(transcribeIntervalRef.current)
        transcribeIntervalRef.current = null
      }
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current)
        silenceTimerRef.current = null
      }
      try {
        localCtx.close()
      } catch {
        /* ignore */
      }
      stream.getTracks().forEach((t) => t.stop())

      const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
      if (blob.size < 1000) return

      setTooltip('Finalizing...')
      setTooltipVisible(true)
      try {
        const res = await fetch('/api/transcribe', { method: 'POST', body: blob })
        const result = (await res.json()) as { text?: string; error?: string }
        if (result.text) {
          onInterimRef.current(result.text)
          setTimeout(() => onFinalRef.current(), 100)
        } else if (result.error) {
          console.error('Transcription error:', result.error)
          flashTooltip('Transcription failed')
        }
      } catch (err) {
        console.error('Failed to transcribe:', err)
        flashTooltip('Transcription failed')
      } finally {
        setTooltip(DEFAULT_TOOLTIP)
        setTooltipVisible(false)
      }
    }

    recorder.start(500)
  }, [flashTooltip])

  const toggle = useCallback(() => {
    if (isRecording) stop()
    else void start()
  }, [isRecording, start, stop])

  useEffect(() => {
    return () => {
      if (silenceIntervalRef.current) clearInterval(silenceIntervalRef.current)
      if (transcribeIntervalRef.current) clearInterval(transcribeIntervalRef.current)
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current)
      streamRef.current?.getTracks().forEach((t) => t.stop())
      try {
        localCtxRef.current?.close()
      } catch {
        /* ignore */
      }
    }
  }, [])

  return { isRecording, tooltip, tooltipVisible, start, stop, toggle }
}
