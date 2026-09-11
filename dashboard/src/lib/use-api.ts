"use client"

import { useCallback, useEffect, useState } from "react"

import { api, ApiError } from "@/lib/api"

type Loaded<T> = { path: string | null; tick: number; data?: T; error?: ApiError }

/**
 * GET `path` and keep the result. `reload()` refetches in the background and keeps showing the
 * previous data meanwhile; changing `path` drops data from the old path. `null` fetches nothing.
 */
export function useApi<T>(path: string | null) {
  const [tick, setTick] = useState(0)
  const [state, setState] = useState<Loaded<T>>({ path: null, tick: -1 })

  useEffect(() => {
    if (path === null) return
    let cancelled = false
    api<T>(path).then(
      (data) => {
        if (!cancelled) setState({ path, tick, data })
      },
      (error: unknown) => {
        if (cancelled) return
        const apiError = error instanceof ApiError ? error : new ApiError(0, String(error))
        setState((prev) => ({ path, tick, data: prev.path === path ? prev.data : undefined, error: apiError }))
      },
    )
    return () => {
      cancelled = true
    }
  }, [path, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  const mutate = useCallback(
    (update: (data: T) => T) => setState((prev) => (prev.data === undefined ? prev : { ...prev, data: update(prev.data) })),
    [],
  )

  const samePath = state.path === path
  return {
    data: samePath ? state.data : undefined,
    error: samePath ? state.error : undefined,
    loading: path !== null && (!samePath || state.tick !== tick),
    reload,
    mutate,
  }
}
