import { useCallback, useEffect, useState } from 'react'
import { ApiRequestError } from './api'

/** Runs `fetcher` on mount (and whenever `deps` change), exposing the
 * loading/error/data trio every page needs. Not a generic data-fetching
 * library -- just enough to keep every page's loading/error handling
 * identical, per the Phase 6A brief's "loading/error states" requirement. */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  // When `data` last loaded successfully. `data` is deliberately kept after a
  // failed refetch, so pages can show it with a "data from HH:MM" banner
  // (ApiErrorState / StaleBanner) instead of blanking.
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)

  const refetch = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetcher()
      .then((result) => {
        if (!cancelled) {
          setData(result)
          setUpdatedAt(new Date())
          setLoading(false)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiRequestError ? err.message : 'Something went wrong.')
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, version])

  return { data, loading, error, refetch, updatedAt }
}
