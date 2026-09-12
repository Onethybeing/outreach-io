"use client"

import { Loader2Icon, RefreshCwIcon } from "lucide-react"
import { useState } from "react"

import { useSession } from "@/components/app-shell"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api, query } from "@/lib/api"
import { fmtRate, humanize, isRate } from "@/lib/format"
import type { Rate, Resume, Stats } from "@/lib/types"
import { useAction } from "@/lib/use-action"
import { useApi } from "@/lib/use-api"

/** The name they'd recognise: their own first name, or the part of the email before the @. */
function firstName(me: { name: string | null; email: string }): string {
  const fromName = me.name?.trim().split(/\s+/)[0]
  return fromName || me.email.split("@")[0]
}

export default function StatsPage() {
  const { settings, me } = useSession()
  const [days, setDays] = useState("30")
  const [resumeId, setResumeId] = useState("all")
  // In dev mode every "send" is a dev send, so hiding them would show all zeros.
  const [includeDev, setIncludeDev] = useState(settings.app_mode === "dev")
  const resumes = useApi<Resume[]>("/resumes")
  const path = `/stats${query({ days, include_dev: includeDev, resume_id: resumeId === "all" ? null : resumeId })}`
  const stats = useApi<Stats>(path)
  const { run, isPending } = useAction()

  async function refresh() {
    // fresh=true skips the API's one-minute cache.
    const result = await run("refresh", () => api<Stats>(`${path}&fresh=true`))
    // Dropped if the filters changed while it was loading, so the numbers always match what's on screen.
    if (result) stats.mutate(() => result, path)
  }

  const data = stats.data

  return (
    <>
      <PageHeader
        title={`Hi ${firstName(me)}`}
        description="How the pipeline is doing. Every rate shows its sample size, so don't read much into small n."
        actions={
          <>
            <Select value={days} onValueChange={setDays}>
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["7", "30", "90", "365"].map((d) => (
                  <SelectItem key={d} value={d}>
                    Last {d} days
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={resumeId} onValueChange={setResumeId}>
              <SelectTrigger className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All CVs</SelectItem>
                {resumes.data?.map((r) => (
                  <SelectItem key={r.id} value={r.id}>
                    {r.filename}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Label className="flex items-center gap-2 text-sm font-normal">
              <Checkbox checked={includeDev} onCheckedChange={(v) => setIncludeDev(v === true)} />
              Include dev sends
            </Label>
            <Button variant="outline" size="sm" onClick={refresh} disabled={isPending("refresh")}>
              {isPending("refresh") ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
              Refresh
            </Button>
          </>
        }
      />

      {stats.error && !data && <p className="text-sm text-destructive">{stats.error.message}</p>}
      {!data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Kpi label="Emails sent" value={data.kpis.emails_sent} />
            <Kpi label="Reply rate" value={data.kpis.reply_rate} />
            <Kpi label="Bounce rate" value={data.kpis.bounce_rate} />
            <Kpi label="Contacts" value={data.kpis.contacts} />
            <Kpi label="Candidates found" value={data.kpis.candidates_found} />
            <Kpi label="Runs" value={data.kpis.runs} />
            <Kpi label="CVs in library" value={data.kpis.resumes} />
            <Kpi label="Avg run time" value={data.agent_health.avg_run_seconds === null ? "-" : `${Math.round(data.agent_health.avg_run_seconds)}s`} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Funnel</CardTitle>
                <CardDescription>Contacts created in the window</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {data.funnel.map((step) => {
                  const max = Math.max(1, ...data.funnel.map((s) => s.count))
                  return (
                    <div key={step.step} className="grid grid-cols-[9rem_1fr_3rem] items-center gap-2 text-sm">
                      <span className="text-muted-foreground">{humanize(step.step)}</span>
                      <div className="h-2 rounded bg-muted">
                        <div className="h-2 rounded bg-primary" style={{ width: `${(step.count / max) * 100}%` }} />
                      </div>
                      <span className="text-right tabular-nums">{step.count}</span>
                    </div>
                  )
                })}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Sent and replied per day</CardTitle>
                <CardDescription>
                  <span className="mr-3 inline-flex items-center gap-1">
                    <span className="size-2 rounded-sm bg-primary" /> sent
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <span className="size-2 rounded-sm bg-emerald-500" /> replied
                  </span>
                </CardDescription>
              </CardHeader>
              <CardContent>
                <OverTime points={data.over_time} />
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <KeyValues title="Providers" values={data.providers} />
            <KeyValues
              title="Agent health"
              values={{ runs: data.agent_health.runs, success_rate: data.agent_health.success_rate, ...prefixed("failures in ", data.agent_health.failures_by_node) }}
            />
            <KeyValues title="Quality" values={data.quality} />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>By CV</CardTitle>
            </CardHeader>
            <CardContent>
              <SimpleTable
                head={["CV", "Contacts", "Sent", "Reply rate"]}
                rows={data.per_resume.map((r) => [r.filename, r.contacts, r.sent, fmtRate(r.reply_rate)])}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>By draft prompt version</CardTitle>
            </CardHeader>
            <CardContent>
              <SimpleTable
                head={["Version", "Drafts", "Sent", "Reply rate", "Edited before approval", "Avg judge score"]}
                rows={data.per_prompt_version.map((p) => [
                  `v${p.version}`, p.drafts, p.sent, fmtRate(p.reply_rate), fmtRate(p.edited_before_approval),
                  p.avg_judge_score.value === null ? `- (n=${p.avg_judge_score.n})` : `${p.avg_judge_score.value} (n=${p.avg_judge_score.n})`,
                ])}
              />
            </CardContent>
          </Card>
        </>
      )}
    </>
  )
}

function prefixed(prefix: string, values: Record<string, number>): Record<string, number> {
  return Object.fromEntries(Object.entries(values).map(([key, value]) => [`${prefix}${key}`, value]))
}

function Kpi({ label, value }: { label: string; value: number | string | Rate }) {
  const rate = isRate(value) ? value : null
  return (
    <Card className="gap-1 py-4">
      <CardHeader className="px-4">
        <CardDescription>{label}</CardDescription>
      </CardHeader>
      <CardContent className="px-4">
        <div className="text-2xl font-semibold tabular-nums">
          {rate ? (rate.value === null ? "-" : `${(rate.value * 100).toFixed(1)}%`) : String(value)}
        </div>
        {rate && <div className="text-xs text-muted-foreground">n={rate.n}</div>}
      </CardContent>
    </Card>
  )
}

function KeyValues({ title, values }: { title: string; values: Record<string, number | Rate> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="space-y-1.5 text-sm">
          {Object.entries(values).map(([key, value]) => (
            <div key={key} className="flex justify-between gap-4">
              <dt className="text-muted-foreground">{humanize(key)}</dt>
              <dd className="text-right tabular-nums">{isRate(value) ? fmtRate(value) : value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  )
}

function OverTime({ points }: { points: Stats["over_time"] }) {
  const max = Math.max(1, ...points.map((p) => Math.max(p.sent, p.replied)))
  return (
    <div className="flex h-40 items-end gap-px">
      {points.map((p) => (
        <div key={p.date} className="flex h-full flex-1 items-end gap-px" title={`${p.date}: ${p.sent} sent, ${p.replied} replied`}>
          <div className="flex-1 rounded-t-sm bg-primary" style={{ height: `${(p.sent / max) * 100}%` }} />
          <div className="flex-1 rounded-t-sm bg-emerald-500" style={{ height: `${(p.replied / max) * 100}%` }} />
        </div>
      ))}
    </div>
  )
}

function SimpleTable({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No data in this window.</p>
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {head.map((h) => (
            <TableHead key={h}>{h}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, i) => (
          <TableRow key={i}>
            {row.map((cell, j) => (
              <TableCell key={j} className="tabular-nums">
                {cell}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
