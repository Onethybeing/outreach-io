"use client"

import { CircleAlertIcon, CircleCheckIcon, CircleSlashIcon, Loader2Icon, RefreshCwIcon, TriangleAlertIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { fmtDate } from "@/lib/format"
import type { HealthReport } from "@/lib/types"
import { useApi } from "@/lib/use-api"
import { cn } from "@/lib/utils"

const LOOK = {
  ok: { icon: CircleCheckIcon, className: "text-emerald-600 dark:text-emerald-400" },
  warn: { icon: TriangleAlertIcon, className: "text-amber-600 dark:text-amber-400" },
  fail: { icon: CircleAlertIcon, className: "text-destructive" },
  off: { icon: CircleSlashIcon, className: "text-muted-foreground" },
}

const SUMMARY = {
  ok: "Everything checks out.",
  warn: "Working, with something worth a look.",
  fail: "Something is broken.",
}

export function HealthTab() {
  const health = useApi<HealthReport>("/settings/health")
  const report = health.data

  return (
    <Card>
      <CardHeader>
        <CardTitle>System health</CardTitle>
        <CardDescription>
          {report
            ? `${SUMMARY[report.status as keyof typeof SUMMARY] ?? ""} Checked ${fmtDate(report.checked_at)}.`
            : "Checking the database, storage, prompts and every provider key."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Button variant="outline" size="sm" onClick={health.reload} disabled={health.loading}>
          {health.loading ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
          Run checks again
        </Button>

        {health.error && !report && <p className="text-sm text-destructive">{health.error.message}</p>}
        {!report && health.loading && (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        )}

        <ul className="divide-y">
          {report?.checks.map((check) => {
            const look = LOOK[check.status as keyof typeof LOOK] ?? LOOK.off
            const Icon = look.icon
            return (
              <li key={check.name} className="flex items-start gap-3 py-2.5 text-sm">
                <Icon className={cn("mt-0.5 size-4 shrink-0", look.className)} />
                <div className="min-w-0">
                  <div className="font-medium">{check.name}</div>
                  <div className="text-muted-foreground">{check.detail}</div>
                  {check.hint && check.status !== "ok" && <div className="mt-0.5 text-xs text-muted-foreground">{check.hint}</div>}
                </div>
              </li>
            )
          })}
        </ul>
      </CardContent>
    </Card>
  )
}
