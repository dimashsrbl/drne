import { useEffect, useRef, useState } from 'react'
import type { TelemetryResponse } from '../api/types'

const WS_PATH = '/api/telemetry/ws'
const RECONNECT_DELAY_MS = 2000

function buildWsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}${WS_PATH}`
}

export type WsStatus = 'connecting' | 'open' | 'closed'

export function useTelemetryWs() {
  const [telemetry, setTelemetry] = useState<TelemetryResponse | null>(null)
  const [wsStatus, setWsStatus] = useState<WsStatus>('connecting')
  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const deadRef = useRef(false)

  useEffect(() => {
    deadRef.current = false

    function connect() {
      if (deadRef.current) return

      setWsStatus('connecting')
      const ws = new WebSocket(buildWsUrl())
      wsRef.current = ws

      ws.onopen = () => {
        if (!deadRef.current) setWsStatus('open')
      }

      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data as string) as TelemetryResponse
          if (!deadRef.current) setTelemetry(data)
        } catch {
          // ignore malformed frames
        }
      }

      ws.onerror = () => {
        ws.close()
      }

      ws.onclose = () => {
        if (deadRef.current) return
        setWsStatus('closed')
        timerRef.current = setTimeout(connect, RECONNECT_DELAY_MS)
      }
    }

    connect()

    return () => {
      deadRef.current = true
      if (timerRef.current) clearTimeout(timerRef.current)
      wsRef.current?.close()
    }
  }, [])

  return { telemetry, wsStatus }
}
