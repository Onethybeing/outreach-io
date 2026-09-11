import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const TONES = {
  good: "border-emerald-200 bg-emerald-50 text-emerald-700",
  info: "border-sky-200 bg-sky-50 text-sky-700",
  warn: "border-amber-200 bg-amber-50 text-amber-800",
  bad: "border-red-200 bg-red-50 text-red-700",
  muted: "text-muted-foreground",
}

const TONE_BY_STATUS: Record<string, keyof typeof TONES> = {
  verified: "good", found: "good", approved: "good", sent: "good", completed: "good", replied: "good",
  reply: "good", active: "good", reused: "good", updated: "good",
  sent_dev: "info", generated: "info", running: "info", queued: "info", waiting: "info", out_of_office: "info",
  pending: "warn", awaiting_user: "warn", unconfirmed: "warn", unsubscribe: "warn",
  mismatch: "bad", failed: "bad", rejected: "bad", bounced: "bad", bounce: "bad",
}

const LABELS: Record<string, string> = { sent_dev: "sent (dev)", awaiting_user: "needs you", not_run: "not run" }

export function StatusBadge({ status, title, className }: { status: string | null | undefined; title?: string | null; className?: string }) {
  if (!status) return <span className="text-muted-foreground">—</span>
  return (
    <Badge variant="outline" title={title ?? undefined} className={cn(TONES[TONE_BY_STATUS[status] ?? "muted"], className)}>
      {LABELS[status] ?? status.replaceAll("_", " ")}
    </Badge>
  )
}
