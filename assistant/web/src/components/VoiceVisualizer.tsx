import { useEffect, useRef } from 'react'

interface Props {
  active: boolean
  analyserRef: React.RefObject<AnalyserNode | null>
}

const DOT_COUNT = 300
const PHI = Math.PI * (3 - Math.sqrt(5))

export default function VoiceVisualizer({ active, analyserRef }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    function draw() {
      if (!active) return
      rafRef.current = requestAnimationFrame(draw)

      const W = canvas!.width
      const H = canvas!.height
      const centerX = W / 2
      const centerY = H / 2

      ctx!.clearRect(0, 0, W, H)

      let average = 0
      const analyser = analyserRef.current
      if (analyser) {
        const bufferLength = analyser.frequencyBinCount
        const dataArray = new Uint8Array(bufferLength)
        analyser.getByteFrequencyData(dataArray)
        let sum = 0
        for (let i = 0; i < bufferLength; i++) sum += dataArray[i]
        average = sum / bufferLength
      }

      const baseRadius = 30 + average * 0.6
      const time = Date.now() / 1500

      ctx!.shadowBlur = 6
      ctx!.shadowColor = 'rgba(56, 189, 248, 0.8)'

      for (let i = 0; i < DOT_COUNT; i++) {
        const yPos = 1 - (i / (DOT_COUNT - 1)) * 2
        const radiusAtY = Math.sqrt(1 - yPos * yPos)
        const theta = PHI * i

        let x = Math.cos(theta) * radiusAtY
        let y = yPos
        let z = Math.sin(theta) * radiusAtY

        // Rotate around Y-axis
        const cosT = Math.cos(time)
        const sinT = Math.sin(time)
        const xRot = x * cosT - z * sinT
        const zRot = x * sinT + z * cosT
        x = xRot
        z = zRot

        // Tilt around X-axis
        const tilt = 0.3
        const cosTilt = Math.cos(tilt)
        const sinTilt = Math.sin(tilt)
        const yRot = y * cosTilt - z * sinTilt
        const zFinal = y * sinTilt + z * cosTilt
        y = yRot

        const finalX = centerX + x * baseRadius
        const finalY = centerY + y * baseRadius

        const depth = (zFinal + 1) / 2
        const size = 1 + depth * 2
        const alpha = 0.2 + depth * 0.8

        ctx!.fillStyle = `rgba(56, 189, 248, ${alpha})`
        ctx!.beginPath()
        ctx!.arc(finalX, finalY, size, 0, Math.PI * 2)
        ctx!.fill()
      }

      // Glowing core
      ctx!.beginPath()
      ctx!.arc(centerX, centerY, baseRadius * 0.7, 0, Math.PI * 2)
      ctx!.fillStyle = 'rgba(56, 189, 248, 0.1)'
      ctx!.shadowBlur = 30
      ctx!.fill()

      ctx!.shadowBlur = 0
    }

    if (active) {
      rafRef.current = requestAnimationFrame(draw)
    }

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = null
      ctx.clearRect(0, 0, canvas.width, canvas.height)
    }
  }, [active, analyserRef])

  return (
    <div className={`voice-visualizer-container${active ? ' active' : ''}`}>
      <canvas id="voiceCanvas" width={160} height={160} ref={canvasRef} />
    </div>
  )
}
