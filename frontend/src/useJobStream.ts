import { useEffect, useRef, useState } from 'react'
import { getJob, streamJob } from './api'
import type { JobOut } from './types'

/** Tracks one background job's live status via SSE, falling back to a
 * single poll if the stream never opens (e.g. a proxy that buffers
 * text/event-stream). Returns `null` until the first frame arrives. */
export function useJobStream(jobId: string | null): JobOut | null {
  const [job, setJob] = useState<JobOut | null>(null)
  const sourceRef = useRef<EventSource | null>(null)
  // Mirrors `job` synchronously -- the 2s fallback timeout closes over
  // whatever `job` was when the effect ran (always the pre-frame value,
  // a stale-closure bug: `setJob` is async, so checking `job` directly
  // inside the timeout could never see a frame that arrived in between),
  // so it needs a ref it can read live instead.
  const jobRef = useRef<JobOut | null>(null)

  useEffect(() => {
    jobRef.current = null
    setJob(null)
    if (!jobId) return undefined

    const handleFrame = (next: JobOut) => {
      jobRef.current = next
      setJob(next)
    }
    sourceRef.current = streamJob(jobId, handleFrame, handleFrame)
    // Fallback: if no frame arrives quickly (stream blocked by something
    // between browser and server), fetch status once directly so the UI
    // isn't stuck showing nothing.
    const fallback = window.setTimeout(() => {
      if (!jobRef.current) getJob(jobId).then(handleFrame).catch(() => {})
    }, 2000)

    return () => {
      sourceRef.current?.close()
      window.clearTimeout(fallback)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  return job
}
