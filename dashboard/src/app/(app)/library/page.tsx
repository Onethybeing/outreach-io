"use client"

import type { ColumnDef } from "@tanstack/react-table"
import { Loader2Icon, UploadIcon } from "lucide-react"
import Link from "next/link"
import { useMemo, useRef } from "react"

import { useSession } from "@/components/app-shell"
import { DataTable } from "@/components/data-table"
import { PageHeader } from "@/components/page-header"
import { StatusBadge } from "@/components/status-badge"
import { Button, buttonVariants } from "@/components/ui/button"
import { api } from "@/lib/api"
import { fmtDate } from "@/lib/format"
import type { Resume } from "@/lib/types"
import { useAction } from "@/lib/use-action"
import { useApi } from "@/lib/use-api"

export default function LibraryPage() {
  const { can } = useSession()
  const resumes = useApi<Resume[]>("/resumes")
  const { run, isPending } = useAction()
  const fileInput = useRef<HTMLInputElement>(null)

  async function upload(file: File) {
    const form = new FormData()
    form.append("file", file)
    const saved = await run("upload", () => api<Resume>("/resumes", { method: "POST", body: form }), (r) => `Uploaded ${r.filename}`)
    if (fileInput.current) fileInput.current.value = ""
    if (saved) resumes.reload()
  }

  const columns = useMemo<ColumnDef<Resume>[]>(
    () => [
      { accessorKey: "filename", header: "CV", cell: ({ row }) => <span className="font-medium">{row.original.filename}</span> },
      { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusBadge status={row.original.status} /> },
      { accessorKey: "uploaded_at", header: "Uploaded", cell: ({ row }) => fmtDate(row.original.uploaded_at) },
      {
        id: "actions",
        header: "",
        meta: { className: "text-right" },
        cell: ({ row }) => (
          <div className="flex justify-end gap-2">
            {can("runs.start") && (
              <Link href={`/runs?resume=${row.original.id}`} className={buttonVariants({ size: "sm" })}>
                Run agent
              </Link>
            )}
            <Link href={`/contacts?resume=${row.original.id}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
              Contacts
            </Link>
          </div>
        ),
      },
    ],
    [can],
  )

  return (
    <>
      <PageHeader
        title="Library"
        description="The CVs the agent works from. Upload a PDF or DOCX, then run the agent on it."
        actions={
          can("resumes.upload") && (
            <>
              <input
                ref={fileInput}
                type="file"
                accept=".pdf,.docx"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) upload(file)
                }}
              />
              <Button onClick={() => fileInput.current?.click()} disabled={isPending("upload")}>
                {isPending("upload") ? <Loader2Icon className="animate-spin" /> : <UploadIcon />}
                Upload CV
              </Button>
            </>
          )
        }
      />
      <DataTable
        columns={columns}
        data={resumes.data}
        loading={resumes.loading}
        error={resumes.error?.message}
        getRowId={(r) => r.id}
        search="Search CVs"
        empty="No CVs yet. Upload one to get started."
      />
    </>
  )
}
