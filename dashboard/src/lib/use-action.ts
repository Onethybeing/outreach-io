"use client"

import { useCallback, useState } from "react"
import { toast } from "sonner"

/** Runs a mutation, toasts the outcome, and tracks which keys are in flight (for spinners). */
export function useAction() {
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set())

  const run = useCallback(
    async <T>(key: string, fn: () => Promise<T>, success?: string | ((result: T) => string)): Promise<T | undefined> => {
      setPending((prev) => new Set(prev).add(key))
      try {
        const result = await fn()
        if (success) toast.success(typeof success === "string" ? success : success(result))
        return result
      } catch (error) {
        toast.error(error instanceof Error ? error.message : String(error))
        return undefined
      } finally {
        setPending((prev) => {
          const next = new Set(prev)
          next.delete(key)
          return next
        })
      }
    },
    [],
  )

  const isPending = useCallback((key: string) => pending.has(key), [pending])
  return { run, isPending }
}
