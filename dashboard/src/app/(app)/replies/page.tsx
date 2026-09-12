"use client"

import type { ColumnDef } from "@tanstack/react-table"
import { InboxIcon, Loader2Icon } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { useSession } from "@/components/app-shell"
import { DataTable } from "@/components/data-table"
import { PageHeader } from "@/components/page-header"
import { StatusBadge } from "@/components/status-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { post, query } from "@/lib/api"
import { fmtDate, humanize } from "@/lib/format"
import type { PollOut, Reply } from "@/lib/types"
import { useAction } from "@/lib/use-action"
import { useApi } from "@/lib/use-api"

const CLASSIFICATIONS = ["reply", "bounce", "out_of_office", "unsubscribe"]

export default function RepliesPage() {
  const { can } = useSession()
  const [classification, setClassification] = useState("all")
  const replies = useApi<Reply[]>(`/replies${query({ classification: classification === "all" ? null : classification })}`)
  const { run, isPending } = useAction()

  async function checkInbox() {
    const result = await run("poll", () => post<PollOut>("/replies/poll"))
    if (!result) return
    toast.success(`Checked ${result.contacts} thread(s): ${result.new_messages} new message(s)`)
    if (result.errors.length) toast.warning(result.errors.join("; "))
    replies.reload()
  }

  const columns = useMemo<ColumnDef<Reply>[]>(
    () => [
      { accessorKey: "received_at", header: "Received", cell: ({ row }) => fmtDate(row.original.received_at) },
      {
        accessorKey: "contact_name",
        header: "From",
        cell: ({ row: { original: r } }) => (
          <div>
            <div className="font-medium">{r.contact_name}</div>
            <div className="text-xs text-muted-foreground">{r.contact_email ?? "-"}</div>
          </div>
        ),
      },
      { accessorKey: "startup_name", header: "Startup" },
      {
        accessorKey: "classification",
        header: "Type",
        cell: ({ row: { original: r } }) => (
          <div className="flex flex-col items-start gap-1">
            <StatusBadge status={r.classification} />
            {r.do_not_contact && <Badge variant="destructive">Do not contact</Badge>}
          </div>
        ),
      },
      {
        accessorKey: "snippet",
        header: "Message",
        meta: { className: "min-w-72 max-w-xl whitespace-normal" },
        cell: ({ row }) => <span className="line-clamp-3 text-muted-foreground">{row.original.snippet ?? "-"}</span>,
      },
    ],
    [],
  )

  return (
    <>
      <PageHeader
        title="Replies"
        description="Answers to sent emails. The inbox is checked every 10 minutes."
        actions={
          can("contacts.act") && (
            <Button variant="outline" onClick={checkInbox} disabled={isPending("poll")}>
              {isPending("poll") ? <Loader2Icon className="animate-spin" /> : <InboxIcon />}
              Check inbox now
            </Button>
          )
        }
      />
      <DataTable
        columns={columns}
        data={replies.data}
        loading={replies.loading}
        error={replies.error?.message}
        getRowId={(r) => r.event_id}
        search="Search replies"
        empty="No replies yet."
        toolbar={
          <Select value={classification} onValueChange={setClassification}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              {CLASSIFICATIONS.map((c) => (
                <SelectItem key={c} value={c}>
                  {humanize(c)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
    </>
  )
}
