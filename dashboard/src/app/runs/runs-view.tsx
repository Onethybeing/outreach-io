"use client"

import { ExternalLinkIcon, FlaskConicalIcon, Loader2Icon, PlayIcon } from "lucide-react"
import Link from "next/link"
import { useEffect, useRef, useState } from "react"

import { useSession } from "@/components/app-shell"
import { StatusBadge } from "@/components/status-badge"
import { PageHeader } from "@/components/page-header"
import { Button, buttonVariants } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { apiUrl, post } from "@/lib/api"
import { fmtDate, humanize } from "@/lib/format"
import type { Resume, Run, RunDetail, RunEvent } from "@/lib/types"
import { useAction } from "@/lib/use-action"
import { useApi } from "@/lib/use-api"
import { cn } from "@/lib/utils"

const EVENT_KINDS = ["started", "completed", "info", "warning", "failed"]

export function RunsView({ initialResumeId, initialRunId }: { initialResumeId?: string; initialRunId?: string }) {
  const { can } = useSession()
  const resumes = useApi<Resume[]>("/resumes")
  const runs = useApi<Run[]>("/runs?limit=50")
  const { run, isPending } = useAction()

  const [resumeChoice, setResumeChoice] = useState(initialResumeId ?? "")
  const [numStartups, setNumStartups] = useState("5")
  const [numKdms, setNumKdms] = useState("3")
  const [runChoice, setRunChoice] = useState<string | null>(initialRunId ?? null)

  const resumeId = resumeChoice || resumes.data?.[0]?.id || ""
  const selectedRunId = runChoice ?? runs.data?.[0]?.id ?? null
  const filenames = new Map(resumes.data?.map((r) => [r.id, r.filename]))

  async function start() {
    const started = await run(
      "start",
      () => post<Run>("/runs", { resume_id: resumeId, num_startups: Number(numStartups), num_kdms_per_company: Number(numKdms) }),
      "Run started",
    )
    if (started) {
      setRunChoice(started.id)
      runs.reload()
    }
  }

  return (
    <>
      <PageHeader title="Run agent" description="Find startups that fit a CV and the decision-makers to contact at each." />

      {can("runs.start") && (
        <Card>
          <CardContent className="flex flex-wrap items-end gap-4">
            <div className="space-y-1.5">
              <Label>CV</Label>
              <Select value={resumeId} onValueChange={setResumeChoice}>
                <SelectTrigger className="w-64">
                  <SelectValue placeholder={resumes.data?.length === 0 ? "Upload a CV first" : "Choose a CV"} />
                </SelectTrigger>
                <SelectContent>
                  {resumes.data?.map((r) => (
                    <SelectItem key={r.id} value={r.id}>
                      {r.filename}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="num-startups">Startups (N)</Label>
              <Input id="num-startups" type="number" min={1} max={20} value={numStartups} onChange={(e) => setNumStartups(e.target.value)} className="w-28" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="num-kdms">People per startup (M)</Label>
              <Input id="num-kdms" type="number" min={1} max={10} value={numKdms} onChange={(e) => setNumKdms(e.target.value)} className="w-28" />
            </div>
            <Button onClick={start} disabled={!resumeId || isPending("start")}>
              {isPending("start") ? <Loader2Icon className="animate-spin" /> : <PlayIcon />}
              Start run
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Runs</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 px-2">
            {!runs.data && <Skeleton className="h-20" />}
            {runs.data?.length === 0 && <p className="px-2 text-sm text-muted-foreground">No runs yet.</p>}
            {runs.data?.map((r) => (
              <button
                key={r.id}
                type="button"
                onClick={() => setRunChoice(r.id)}
                className={cn("w-full rounded-md px-2 py-2 text-left text-sm hover:bg-muted", r.id === selectedRunId && "bg-muted")}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">{filenames.get(r.resume_id) ?? "CV"}</span>
                  <StatusBadge status={r.status} />
                </div>
                <div className="text-xs text-muted-foreground">
                  {fmtDate(r.created_at)} · N={r.num_startups}, M={r.num_kdms_per_company}
                </div>
              </button>
            ))}
          </CardContent>
        </Card>

        {selectedRunId ? (
          <RunPanel key={selectedRunId} runId={selectedRunId} onFinished={runs.reload} />
        ) : (
          <Card>
            <CardContent className="text-sm text-muted-foreground">Start a run to see its progress here.</CardContent>
          </Card>
        )}
      </div>
    </>
  )
}

function RunPanel({ runId, onFinished }: { runId: string; onFinished: () => void }) {
  const { can } = useSession()
  const detail = useApi<RunDetail>(`/runs/${runId}`)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [stream, setStream] = useState<"connecting" | "live" | "ended" | "lost">("connecting")
  const { run, isPending } = useAction()
  const logEnd = useRef<HTMLDivElement>(null)
  const reloadDetail = detail.reload

  useEffect(() => {
    // EventSource reconnects on its own and resumes from Last-Event-ID, which the API honours.
    const source = new EventSource(apiUrl(`/runs/${runId}/stream`))
    const onEvent = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent
      setEvents((prev) => (prev.some((e) => e.id === event.id) ? prev : [...prev, event]))
      setStream("live")
      if (event.kind === "completed" || event.kind === "failed") reloadDetail()
    }
    for (const kind of EVENT_KINDS) source.addEventListener(kind, onEvent)
    source.addEventListener("end", () => {
      source.close()
      setStream("ended")
      reloadDetail()
      onFinished()
    })
    source.addEventListener("error", (event) => {
      // A named "error" event from the API carries data (e.g. run not found); a dropped connection doesn't.
      if (event instanceof MessageEvent && event.data) {
        source.close()
        setStream("lost")
      }
    })
    source.onopen = () => setStream("live")
    return () => source.close()
  }, [runId, reloadDetail, onFinished])

  useEffect(() => {
    logEnd.current?.scrollIntoView({ block: "nearest" })
  }, [events.length])

  const data = detail.data
  const pending = data?.candidates.filter((c) => c.status === "pending").length ?? 0

  return (
    <div className="min-w-0 space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {data?.resume_filename ?? "Run"} {data && <StatusBadge status={data.status} />}
          </CardTitle>
          <CardDescription>
            {data ? `Started ${fmtDate(data.started_at ?? data.created_at)} · ${data.num_startups} startups × ${data.num_kdms_per_company} people` : "Loading…"}
          </CardDescription>
          <CardAction className="flex gap-2">
            {data?.langfuse_trace_url && (
              <a href={data.langfuse_trace_url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", size: "sm" })}>
                Trace <ExternalLinkIcon />
              </a>
            )}
            {data?.status === "completed" && can("contacts.act") && (
              <Button
                variant="outline"
                size="sm"
                disabled={isPending("judge")}
                onClick={() => run("judge", () => post(`/evals/runs/${runId}/startups`), "Judging startup relevance — scores show up in Stats")}
              >
                <FlaskConicalIcon /> Judge startups
              </Button>
            )}
            {data?.status === "completed" && (
              <Link href={`/candidates?run=${runId}`} className={buttonVariants({ size: "sm" })}>
                Review {pending} candidate{pending === 1 ? "" : "s"}
              </Link>
            )}
          </CardAction>
        </CardHeader>
        <CardContent className="space-y-3">
          {data?.error && <p className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{data.error}</p>}
          {data?.usage && (
            <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
              {Object.entries(data.usage).map(([key, value]) => (
                <span key={key}>
                  {humanize(key)}: <span className="text-foreground tabular-nums">{value}</span>
                </span>
              ))}
            </div>
          )}
          <div className="max-h-80 overflow-y-auto rounded-md border bg-muted/40 p-2 font-mono text-xs">
            {events.length === 0 && (
              <p className="text-muted-foreground">{stream === "connecting" ? "Connecting…" : "Waiting for the agent…"}</p>
            )}
            {events.map((e) => (
              <div key={e.id} className="flex gap-2 py-0.5">
                <span className="shrink-0 text-muted-foreground">{new Date(e.created_at).toLocaleTimeString()}</span>
                <span className="w-32 shrink-0 truncate text-muted-foreground">{e.node}</span>
                <span className={cn(e.kind === "failed" && "text-destructive", e.kind === "warning" && "text-amber-700", e.kind === "completed" && "text-emerald-700")}>
                  {e.message}
                </span>
              </div>
            ))}
            {stream === "lost" && <p className="text-destructive">Lost the live feed — reload the page.</p>}
            <div ref={logEnd} />
          </div>
        </CardContent>
      </Card>

      {data && data.startups.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Startups</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Startup</TableHead>
                  <TableHead>Relevance</TableHead>
                  <TableHead>People</TableHead>
                  <TableHead>About</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.startups.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="font-medium">
                      {s.website ? (
                        <a href={s.website} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:underline">
                          {s.name} <ExternalLinkIcon className="size-3" />
                        </a>
                      ) : (
                        s.name
                      )}
                    </TableCell>
                    <TableCell className="tabular-nums">{s.relevance === null ? "—" : s.relevance.toFixed(2)}</TableCell>
                    <TableCell className="tabular-nums">{data.candidates.filter((c) => c.startup_id === s.id).length}</TableCell>
                    <TableCell className="max-w-xl min-w-64 whitespace-normal text-muted-foreground">{s.description ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
