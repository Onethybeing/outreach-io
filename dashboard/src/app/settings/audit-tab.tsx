"use client"

import type { ColumnDef } from "@tanstack/react-table"
import { useState } from "react"

import { DataTable } from "@/components/data-table"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, query } from "@/lib/api"
import { fmtDate } from "@/lib/format"
import type { AuditEntry } from "@/lib/types"
import { useAction } from "@/lib/use-action"
import { useApi } from "@/lib/use-api"

const PAGE = 100

const COLUMNS: ColumnDef<AuditEntry>[] = [
  { accessorKey: "created_at", header: "When", cell: ({ row }) => fmtDate(row.original.created_at) },
  { accessorKey: "user_email", header: "Who", cell: ({ row }) => row.original.user_email ?? "system" },
  { accessorKey: "action", header: "Action", cell: ({ row }) => <span className="font-mono text-xs">{row.original.action}</span> },
  {
    id: "target",
    header: "Target",
    cell: ({ row: { original: e } }) => (e.target_type ? <span className="font-mono text-xs">{`${e.target_type}:${e.target_id ?? ""}`}</span> : "—"),
  },
  {
    id: "details",
    header: "Details",
    meta: { className: "max-w-md" },
    cell: ({ row: { original: e } }) => {
      const text = e.details ? JSON.stringify(e.details) : "—"
      return (
        <span className="block truncate font-mono text-xs text-muted-foreground" title={text}>
          {text}
        </span>
      )
    },
  },
]

export function AuditTab() {
  const [draft, setDraft] = useState("")
  const [action, setAction] = useState("")

  return (
    <div className="space-y-3">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setAction(draft.trim())
        }}
        className="flex gap-2"
      >
        <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Exact action, e.g. emails.send" className="w-72" />
        <Button type="submit" variant="outline">
          Filter
        </Button>
      </form>
      <AuditList key={action} action={action} />
    </div>
  )
}

function AuditList({ action }: { action: string }) {
  const first = useApi<AuditEntry[]>(`/audit-log${query({ action, limit: PAGE })}`)
  const [more, setMore] = useState<AuditEntry[]>([])
  const [exhausted, setExhausted] = useState(false)
  const { run, isPending } = useAction()
  const rows = first.data ? [...first.data, ...more] : undefined
  const lastPageFull = (more.length ? more.length % PAGE === 0 : first.data?.length === PAGE) && !exhausted

  async function loadMore() {
    const last = rows?.at(-1)
    if (!last) return
    const page = await run("more", () => api<AuditEntry[]>(`/audit-log${query({ action, before: last.created_at, limit: PAGE })}`))
    if (!page) return
    setMore((prev) => [...prev, ...page])
    if (page.length < PAGE) setExhausted(true)
  }

  return (
    <div className="space-y-3">
      <DataTable columns={COLUMNS} data={rows} loading={first.loading} error={first.error?.message} getRowId={(e) => e.id} empty="No matching entries." />
      {lastPageFull && (
        <Button variant="outline" onClick={loadMore} disabled={isPending("more")}>
          Load older
        </Button>
      )}
    </div>
  )
}
