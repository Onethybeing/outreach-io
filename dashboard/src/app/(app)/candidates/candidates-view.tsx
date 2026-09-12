"use client"

import type { ColumnDef, RowSelectionState } from "@tanstack/react-table"
import { CheckIcon, ExternalLinkIcon, XIcon } from "lucide-react"
import Link from "next/link"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { useSession } from "@/components/app-shell"
import { DataTable, selectedIds } from "@/components/data-table"
import { PageHeader } from "@/components/page-header"
import { StatusBadge } from "@/components/status-badge"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { post, query } from "@/lib/api"
import type { BulkDecisionResult, Candidate, CandidateDecision } from "@/lib/types"
import { useAction } from "@/lib/use-action"
import { useApi } from "@/lib/use-api"

const STATUSES = ["pending", "approved", "rejected", "reused", "updated"]
const BULK_LIMIT = 200

export function CandidatesView({ initialRunId }: { initialRunId?: string }) {
  const { can } = useSession()
  const [status, setStatus] = useState("pending")
  const [runId, setRunId] = useState(initialRunId)
  const [selection, setSelection] = useState<RowSelectionState>({})
  const candidates = useApi<Candidate[]>(`/candidates${query({ status: status === "all" ? null : status, run_id: runId })}`)
  const { run, isPending } = useAction()
  const canAct = can("contacts.act")
  const reload = candidates.reload

  const columns = useMemo<ColumnDef<Candidate>[]>(() => {
    async function decide(candidate: Candidate, action: "approve" | "reject" | "reuse" | "update-contact") {
      const done = await run(`${candidate.id}:${action}`, () => post<CandidateDecision>(`/candidates/${candidate.id}/${action}`), (r) =>
        r.status === "approved" || r.status === "updated" ? `${candidate.name} ${r.status}. Checking they still work there` : `${candidate.name} ${r.status}`,
      )
      if (done) reload()
    }

    return [
      {
        accessorKey: "name",
        header: "Person",
        cell: ({ row: { original: c } }) => (
          <div>
            <a href={c.linkedin_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium hover:underline">
              {c.name} <ExternalLinkIcon className="size-3" />
            </a>
            <div className="text-xs text-muted-foreground">{c.title ?? "-"}</div>
          </div>
        ),
      },
      { accessorKey: "startup_name", header: "Startup" },
      {
        accessorKey: "reason",
        header: "Why",
        meta: { className: "min-w-64 max-w-md whitespace-normal" },
        cell: ({ row }) => <span className="line-clamp-3 text-muted-foreground" title={row.original.reason ?? undefined}>{row.original.reason ?? "-"}</span>,
      },
      { accessorKey: "cv_filename", header: "CV" },
      {
        accessorKey: "status",
        header: "Status",
        cell: ({ row: { original: c } }) => (
          <div className="flex flex-col items-start gap-1">
            <StatusBadge status={c.status} />
            {c.existing_contact_id && c.status === "pending" && <Badge variant="secondary">Already a contact</Badge>}
          </div>
        ),
      },
      {
        id: "actions",
        header: "",
        meta: { className: "text-right" },
        cell: ({ row: { original: c } }) => {
          if (c.status !== "pending") {
            return c.contact_id || c.existing_contact_id ? (
              <Link href="/contacts?view=all" className={buttonVariants({ variant: "ghost", size: "sm" })}>
                View contact
              </Link>
            ) : null
          }
          if (!canAct) return null
          const busy = (action: string) => isPending(`${c.id}:${action}`)
          return (
            <div className="flex justify-end gap-1">
              {c.existing_contact_id ? (
                <>
                  <Button size="sm" variant="outline" disabled={busy("reuse")} onClick={() => decide(c, "reuse")} title="Keep the existing contact as it is">
                    Reuse
                  </Button>
                  <Button size="sm" disabled={busy("update-contact")} onClick={() => decide(c, "update-contact")} title="Update the existing contact and re-verify">
                    Update
                  </Button>
                </>
              ) : (
                <Button size="sm" disabled={busy("approve")} onClick={() => decide(c, "approve")}>
                  <CheckIcon /> Approve
                </Button>
              )}
              <Button size="sm" variant="ghost" disabled={busy("reject")} onClick={() => decide(c, "reject")}>
                <XIcon /> Reject
              </Button>
            </div>
          )
        },
      },
    ]
  }, [run, isPending, canAct, reload])

  const ids = selectedIds(candidates.data, selection)

  async function bulk(action: "approve" | "reject") {
    if (ids.length > BULK_LIMIT) {
      toast.error(`Select at most ${BULK_LIMIT} at a time`)
      return
    }
    const results = await run(`bulk:${action}`, () => post<BulkDecisionResult[]>("/candidates/bulk", { action, candidate_ids: ids }))
    if (!results) return
    const failed = results.filter((r) => !r.ok)
    const ok = results.length - failed.length
    const past = action === "approve" ? "approved" : "rejected"
    if (ok) toast.success(`${ok} ${past}`)
    if (failed.length) toast.error(`${failed.length} couldn't be ${past}: ${failed[0].error}`)
    setSelection({})
    reload()
  }

  return (
    <>
      <PageHeader
        title="Candidates"
        description="People the agent found. Approve the ones worth contacting. Approving checks they still work there."
      />
      <DataTable
        columns={columns}
        data={candidates.data}
        loading={candidates.loading}
        error={candidates.error?.message}
        getRowId={(c) => c.id}
        search="Search people or startups"
        empty={status === "pending" ? "No candidates waiting for review." : "No candidates."}
        rowSelection={selection}
        onRowSelectionChange={canAct && status === "pending" ? setSelection : undefined}
        toolbar={
          <>
            <Select
              value={status}
              onValueChange={(value) => {
                setStatus(value)
                setSelection({})
              }}
            >
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {STATUSES.map((s) => (
                  <SelectItem key={s} value={s} className="capitalize">
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {runId && (
              <Badge variant="secondary" className="gap-1">
                One run only
                <button type="button" aria-label="Show all runs" onClick={() => setRunId(undefined)}>
                  <XIcon className="size-3" />
                </button>
              </Badge>
            )}
            {ids.length > 0 && (
              <div className="ml-auto flex items-center gap-2">
                <span className="text-sm text-muted-foreground">{ids.length} selected</span>
                <Button size="sm" onClick={() => bulk("approve")} disabled={isPending("bulk:approve")}>
                  Approve selected
                </Button>
                <Button size="sm" variant="outline" onClick={() => bulk("reject")} disabled={isPending("bulk:reject")}>
                  Reject selected
                </Button>
              </div>
            )}
          </>
        }
      />
    </>
  )
}
