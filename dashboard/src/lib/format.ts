import type { Rate } from "@/lib/types"

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
}

export function fmtRate(rate: Rate): string {
  return rate.value === null ? `— (n=${rate.n})` : `${(rate.value * 100).toFixed(1)}% (n=${rate.n})`
}

export function isRate(value: unknown): value is Rate {
  return typeof value === "object" && value !== null && "value" in value && "n" in value
}

export function humanize(key: string): string {
  const text = key.replaceAll("_", " ")
  return text.charAt(0).toUpperCase() + text.slice(1)
}
