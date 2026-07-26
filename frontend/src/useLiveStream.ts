import { useEffect, useRef, useState } from 'react'
import { getLiveStatus, streamLiveStatus } from './api'
import type { LiveSessionStatus } from './types'

/** Tracks the dashboard-controlled paper live session via SSE, falling
 * back to a single poll if the stream never opens -- same reasoning as
 * `useJobStream`, except this stream never reaches a terminal state on
 * its own, so the connection stays open for the component's lifetime. */
export function useLiveStream(): LiveSessionStatus | null {
  const [status, setStatus] = useState<LiveSessionStatus | null>(null)
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    sourceRef.current = streamLiveStatus(setStatus)

    const fallback = window.setTimeout(() => {
      setStatus((current) => {
        if (!current) getLiveStatus().then(setStatus).catch(() => {})
        return current
      })
    }, 2000)

    return () => {
      sourceRef.current?.close()
      window.clearTimeout(fallback)
    }
  }, [])

  return status
}
