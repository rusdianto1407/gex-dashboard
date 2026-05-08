import { useEffect, useRef, useState } from "react"

import { api, type GexSnapshot } from "@/lib/api"

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "")
const WS_BASE = API_BASE.replace(/^http/, "ws")
const RECONNECT_DELAYS_MS = [1_000, 2_000, 5_000, 10_000, 30_000]

export type StreamStatus = "idle" | "connecting" | "live" | "fallback" | "error"

export interface GexStreamState {
  snapshot: GexSnapshot | null
  status: StreamStatus
  error: string | null
  lastUpdateAt: number | null
}

interface Options {
  symbol: string
  expiry: string | undefined
  useMock: boolean
  enabled?: boolean
}

interface HeartbeatFrame {
  type: "heartbeat"
}

function isHeartbeat(value: unknown): value is HeartbeatFrame {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { type?: unknown }).type === "heartbeat"
  )
}

/**
 * Subscribes to `/ws/gex/{symbol}?expiry=...` and surfaces the latest
 * `GexSnapshot` to React. Falls back to a one-shot REST snapshot if the
 * websocket can't be reached, and reconnects with exponential backoff.
 */
export function useGexStream({
  symbol,
  expiry,
  useMock,
  enabled = true,
}: Options): GexStreamState {
  const [snapshot, setSnapshot] = useState<GexSnapshot | null>(null)
  const [status, setStatus] = useState<StreamStatus>("idle")
  const [error, setError] = useState<string | null>(null)
  const [lastUpdateAt, setLastUpdateAt] = useState<number | null>(null)

  const cancelledRef = useRef(false)
  const wsRef = useRef<WebSocket | null>(null)
  const retryTimerRef = useRef<number | null>(null)

  useEffect(() => {
    if (!enabled || !expiry) {
      return
    }

    cancelledRef.current = false
    let attempt = 0

    const clearRetry = () => {
      if (retryTimerRef.current !== null) {
        window.clearTimeout(retryTimerRef.current)
        retryTimerRef.current = null
      }
    }

    const scheduleReconnect = () => {
      if (cancelledRef.current) return
      const delay = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)]
      attempt += 1
      retryTimerRef.current = window.setTimeout(connect, delay)
    }

    const connect = () => {
      if (cancelledRef.current) return
      setStatus("connecting")
      const params = new URLSearchParams({ expiry })
      if (useMock) params.set("use_mock", "true")
      const url = `${WS_BASE}/ws/gex/${symbol}?${params.toString()}`

      let ws: WebSocket
      try {
        ws = new WebSocket(url)
      } catch (err) {
        setError((err as Error).message)
        setStatus("error")
        scheduleReconnect()
        void seedFromRest()
        return
      }
      wsRef.current = ws

      ws.onopen = () => {
        if (cancelledRef.current) {
          ws.close()
          return
        }
        attempt = 0
        setError(null)
        setStatus("live")
      }

      ws.onmessage = (ev) => {
        if (cancelledRef.current) return
        try {
          const data = JSON.parse(ev.data as string) as unknown
          if (isHeartbeat(data)) return
          const snap = data as GexSnapshot
          setSnapshot(snap)
          setLastUpdateAt(Date.now())
          setStatus("live")
        } catch (err) {
          setError(`Bad WS frame: ${(err as Error).message}`)
        }
      }

      ws.onerror = () => {
        if (cancelledRef.current) return
        setError("websocket error")
        setStatus("error")
      }

      ws.onclose = () => {
        if (cancelledRef.current) return
        setStatus("fallback")
        scheduleReconnect()
      }
    }

    const seedFromRest = async () => {
      try {
        const snap = await api.gex(symbol, { expiry, useMock })
        if (cancelledRef.current) return
        setSnapshot(snap)
        setLastUpdateAt(Date.now())
        if (status !== "live") setStatus("fallback")
      } catch (err) {
        if (cancelledRef.current) return
        setError((err as Error).message)
        setStatus("error")
      }
    }

    void seedFromRest()
    connect()

    return () => {
      cancelledRef.current = true
      clearRetry()
      const ws = wsRef.current
      wsRef.current = null
      if (ws && ws.readyState <= WebSocket.OPEN) {
        ws.close()
      }
    }
    // status intentionally excluded: would cause reconnect loops on every transition
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, expiry, useMock, enabled])

  return { snapshot, status, error, lastUpdateAt }
}
