"use client"

import type { ColumnDef, RowSelectionState } from "@tanstack/react-table"
import { EllipsisIcon, ExternalLinkIcon, Loader2Icon } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { useSession } from "@/components/app-shell"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { DataTable, selectedIds } from "@/components/data-table"
import { PageHeader } from "@/components/page-header"
import { StatusBadge } from "@/components/status-badge"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api, post, put, query } from "@/lib/api"
import { fmtDate, humanize } from "@/lib/format"
import type { BulkLookupOut, Contact, ContactView, Resume } from "@/lib/types"
import { useAction } from "@/lib/use-action"
import { useApi } from "@/lib/use-api"

import { DraftDialog, SENT, sendSuccess, sendWarning } from "./draft-dialog"
import { EmailDialog } from "./email-dialog"

const VIEWS: { value: ContactView; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "sent", label: "Sent" },
  { value: "no_email", label: "No email" },
  { value: "all", label: "All" },
]

type Confirm = { title: string; description: React.ReactNode; confirmLabel: string; destructive?: boolean; onConfirm: () => Promise<unknown> }
type BulkKind = "lookup" | "drafts" | "send"

const BULK_PATHS: Record<BulkKind, string> = {
  lookup: "/contacts/email/lookup-bulk",
  drafts: "/contacts/drafts/generate-bulk",
  send: "/contacts/send-approved",
}

const NOTHING_ELIGIBLE: Record<BulkKind, string> = {
  lookup: "No contacts need a lookup — they need verified employment and no email yet.",
  drafts: "No contacts are ready for a draft — they need an email and no draft yet.",
  send: "No approved drafts are waiting to be sent.",
}

function isBusy(c: Contact) {
  return c.verification_status === "queued" || c.verification_status === "running" || c.email_lookup_status === "running" || c.send_status === "queued"
}

export function ContactsView({ initialView, initialResumeId }: { initialView: ContactView; initialResumeId?: string }) {
  const { can, settings } = useSession()
  const [view, setView] = useState(initialView)
  const [resumeId, setResumeId] = useState(initialResumeId ?? "all")
  const [selection, setSelection] = useState<RowSelectionState>({})
  const [draftFor, setDraftFor] = useState<string | null>(null)
  const [emailFor, setEmailFor] = useState<Contact | null>(null)
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [watching, setWatching] = useState(false)
  const resumes = useApi<Resume[]>("/resumes")
  const contacts = useApi<Contact[]>(`/contacts${query({ view, resume_id: resumeId === "all" ? null : resumeId })}`)
  const { run, isPending } = useAction()
  const reload = contacts.reload
  const mode = settings.app_mode

  // Verification, lookups and bulk jobs run in the background on the server; poll while any are going.
  const anyBusy = contacts.data?.some(isBusy) ?? false
  useEffect(() => {
    if (!anyBusy && !watching) return
    const timer = setInterval(reload, 4000)
    return () => clearInterval(timer)
  }, [anyBusy, watching, reload])

  const watchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const watchForAWhile = useCallback(() => {
    // Queued bulk jobs may not have touched any row yet, so keep refreshing for a minute.
    setWatching(true)
    if (watchTimer.current) clearTimeout(watchTimer.current) // a later job extends the window, never cuts it short
    watchTimer.current = setTimeout(() => setWatching(false), 60_000)
  }, [])
  useEffect(() => () => void (watchTimer.current && clearTimeout(watchTimer.current)), [])

  const ask = useCallback((next: Confirm) => {
    setConfirm(next)
    setConfirmOpen(true)
  }, [])

  const actions = useMemo(() => {
    async function act<T>(c: Contact, key: string, fn: () => Promise<T>, success: string | ((result: T) => string)) {
      const result = await run(`${c.id}:${key}`, fn, success)
      reload()
      return result
    }

    return {
      verify: async (c: Contact) => {
        const queued = await act(c, "verify", () => post<Contact>(`/contacts/${c.id}/verify`), `Checking ${c.name}'s LinkedIn profile…`)
        if (queued) watchForAWhile()
      },
      lookup: (c: Contact) => {
        const lookedUp = c.email_lookup_status === "found" || c.email_lookup_status === "not_found"
        const force = lookedUp || !!c.email || c.verification_status !== "verified"
        const go = () =>
          act(c, "lookup", () => post<Contact>(`/contacts/${c.id}/email/lookup${force ? "?force=true" : ""}`), (r) =>
            r.email ? `Found ${r.email}` : (r.email_lookup_note ?? `No email found (${humanize(r.email_lookup_status)})`),
          )
        if (!force) return void go()
        ask({
          title: `Look up ${c.name}'s email anyway?`,
          description: c.email
            ? "They already have an email address."
            : lookedUp
              ? "They were already looked up — this spends another lookup."
              : "Their employment isn't verified, so the address may be for a job they've left.",
          confirmLabel: "Look up",
          onConfirm: go,
        })
      },
      draft: (c: Contact) => {
        const force = c.draft_status !== "none" || SENT.includes(c.send_status)
        const go = async () => {
          const drafted = await act(c, "draft", () => post<Contact>(`/contacts/${c.id}/draft/generate${force ? "?force=true" : ""}`), `Draft ready for ${c.name}`)
          if (drafted) setDraftFor(c.id)
        }
        if (!force) return void go()
        ask({
          title: `Write a new draft for ${c.name}?`,
          description: SENT.includes(c.send_status) ? "They've already been emailed." : "The new draft replaces the current one.",
          confirmLabel: "Write new draft",
          onConfirm: go,
        })
      },
      doNotContact: (c: Contact) => {
        const next = !c.do_not_contact
        const go = () =>
          act(c, "dnc", () => put<Contact>(`/contacts/${c.id}/do-not-contact`, { do_not_contact: next }), next ? `${c.name} won't be emailed again` : `${c.name} can be emailed again`)
        if (!next) return void go()
        ask({
          title: `Mark ${c.name} as do not contact?`,
          description: "No further email goes to them. You can undo this from the same menu.",
          confirmLabel: "Mark",
          onConfirm: go,
        })
      },
      erase: (c: Contact) =>
        ask({
          title: `Erase ${c.name}?`,
          description: "For a deletion request: removes this person's record and email history for good. The run they came from keeps its candidate row, without the link.",
          confirmLabel: "Erase",
          destructive: true,
          onConfirm: () => act(c, "erase", () => api(`/contacts/${c.id}`, { method: "DELETE" }), `${c.name} erased`),
        }),
      send: (c: Contact) => {
        const again = SENT.includes(c.send_status)
        ask({
          title: again ? `Email ${c.name} again?` : `Send the email to ${c.name}?`,
          description: sendWarning(mode, c.email),
          confirmLabel: again ? "Send again" : "Send",
          destructive: mode === "prod",
          onConfirm: () => act(c, "send", () => post<Contact>(`/contacts/${c.id}/send${again ? "?force=true" : ""}`), sendSuccess),
        })
      },
    }
  }, [run, reload, ask, watchForAWhile, mode])

  const columns = useMemo<ColumnDef<Contact>[]>(() => {
    const pending = (c: Contact, key: string) => isPending(`${c.id}:${key}`)
    const spinner = <Loader2Icon className="animate-spin" />

    function NextStep({ c }: { c: Contact }) {
      if (c.do_not_contact) return null
      const waiting = (label: string) => (
        <Button size="sm" variant="outline" disabled>
          {spinner} {label}
        </Button>
      )
      if (c.verification_status === "queued" || c.verification_status === "running") return waiting("Verifying")
      if (c.email_lookup_status === "running") return waiting("Finding email")
      if (c.send_status === "queued") return waiting("Sending")
      if (!c.email) {
        if (!can("contacts.act")) return null
        if (c.verification_status === "not_run" || c.verification_status === "failed") {
          return (
            <Button size="sm" onClick={() => actions.verify(c)} disabled={pending(c, "verify")}>
              {pending(c, "verify") && spinner} Verify
            </Button>
          )
        }
        if (c.verification_status === "verified" && c.email_lookup_status !== "found" && c.email_lookup_status !== "not_found") {
          return (
            <Button size="sm" onClick={() => actions.lookup(c)} disabled={pending(c, "lookup")}>
              {pending(c, "lookup") && spinner} Find email
            </Button>
          )
        }
        return (
          <Button size="sm" variant="outline" onClick={() => setEmailFor(c)}>
            Enter email
          </Button>
        )
      }
      if (c.draft_status === "none") {
        return can("drafts.act") ? (
          <Button size="sm" onClick={() => actions.draft(c)} disabled={pending(c, "draft")}>
            {pending(c, "draft") && spinner} Write draft
          </Button>
        ) : null
      }
      if (c.draft_status === "generated") {
        return (
          <Button size="sm" onClick={() => setDraftFor(c.id)}>
            Review draft
          </Button>
        )
      }
      if (!SENT.includes(c.send_status) && can("emails.send")) {
        return (
          <Button size="sm" onClick={() => actions.send(c)} disabled={pending(c, "send")}>
            {pending(c, "send") && spinner} Send
          </Button>
        )
      }
      return (
        <Button size="sm" variant="outline" onClick={() => setDraftFor(c.id)}>
          View email
        </Button>
      )
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
            <div className="text-xs text-muted-foreground">{c.verified_title ?? c.title ?? "—"}</div>
            {c.do_not_contact && <Badge variant="destructive">Do not contact</Badge>}
          </div>
        ),
      },
      {
        accessorKey: "startup_name",
        header: "Startup",
        cell: ({ row: { original: c } }) =>
          c.startup_website ? (
            <a href={c.startup_website} target="_blank" rel="noreferrer" className="hover:underline">
              {c.startup_name}
            </a>
          ) : (
            c.startup_name
          ),
      },
      { accessorKey: "cv_filename", header: "CV" },
      {
        accessorKey: "verification_status",
        header: "Employment",
        cell: ({ row: { original: c } }) => <StatusBadge status={c.verification_status} title={c.verification_note} />,
      },
      {
        accessorKey: "email",
        header: "Email",
        cell: ({ row: { original: c } }) =>
          c.email ? (
            <div>
              <div>{c.email}</div>
              <div className="text-xs text-muted-foreground">{c.email_source ?? ""}</div>
            </div>
          ) : (
            <StatusBadge status={c.email_lookup_status} title={c.email_lookup_note} />
          ),
      },
      {
        accessorKey: "draft_status",
        header: "Draft",
        cell: ({ row: { original: c } }) => (
          <div className="flex items-center gap-1">
            <StatusBadge status={c.draft_status === "none" ? null : c.draft_status} />
            {c.draft_edited && <span className="text-xs text-muted-foreground">edited</span>}
          </div>
        ),
      },
      {
        accessorKey: "send_status",
        header: "Sent",
        cell: ({ row: { original: c } }) => (
          <div>
            <StatusBadge status={c.send_status === "none" ? null : c.send_status} />
            {c.sent_at && <div className="text-xs text-muted-foreground">{fmtDate(c.sent_at)}</div>}
          </div>
        ),
      },
      { accessorKey: "reply_status", header: "Reply", cell: ({ row }) => <StatusBadge status={row.original.reply_status} /> },
      {
        id: "actions",
        header: "",
        meta: { className: "text-right" },
        cell: ({ row: { original: c } }) => (
          <div className="flex items-center justify-end gap-1">
            <NextStep c={c} />
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger className={buttonVariants({ variant: "ghost", size: "icon" })} aria-label="More actions">
                <EllipsisIcon />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {can("contacts.act") && (
                  <>
                    <DropdownMenuItem onSelect={() => actions.verify(c)} disabled={isBusy(c)}>
                      Verify employment
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => actions.lookup(c)} disabled={isBusy(c) || c.do_not_contact}>
                      Find email ({humanize(settings.email_provider)})
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => setEmailFor(c)} disabled={c.do_not_contact}>
                      Enter email manually
                    </DropdownMenuItem>
                  </>
                )}
                {can("drafts.act") && c.email && (
                  <DropdownMenuItem onSelect={() => actions.draft(c)} disabled={c.do_not_contact}>
                    {c.draft_status === "none" ? "Write draft" : "Write a new draft"}
                  </DropdownMenuItem>
                )}
                {c.draft_status !== "none" && <DropdownMenuItem onSelect={() => setDraftFor(c.id)}>Open draft</DropdownMenuItem>}
                {can("emails.send") && c.draft_status === "approved" && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={() => actions.send(c)} disabled={isBusy(c) || c.do_not_contact}>
                      {SENT.includes(c.send_status) ? "Send again" : "Send"}
                    </DropdownMenuItem>
                  </>
                )}
                {can("contacts.act") && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={() => actions.doNotContact(c)} disabled={isBusy(c)}>
                      {c.do_not_contact ? "Allow contact again" : "Mark do not contact"}
                    </DropdownMenuItem>
                  </>
                )}
                {can("contacts.delete") && (
                  <DropdownMenuItem className="text-destructive" onSelect={() => actions.erase(c)} disabled={isBusy(c)}>
                    Erase this person…
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ),
      },
    ]
  }, [actions, can, isPending, settings.email_provider])

  const ids = selectedIds(contacts.data, selection)

  async function bulk(kind: BulkKind) {
    const contactIds = ids.length ? ids : null
    const preview = await run(`bulk:${kind}`, () => post<BulkLookupOut>(BULK_PATHS[kind], { dry_run: true, contact_ids: contactIds }))
    if (!preview) return
    if (preview.eligible === 0) {
      toast.info(NOTHING_ELIGIBLE[kind])
      return
    }
    const n = preview.eligible
    const scope = ids.length ? `${n} of the ${ids.length} selected` : `${n} across all contacts`
    const copy = {
      lookup: { title: `Find emails for ${n} contact${n === 1 ? "" : "s"}?`, description: `${scope} will be looked up with ${humanize(preview.provider ?? settings.email_provider)}, in the background.`, confirmLabel: "Find emails" },
      drafts: { title: `Write ${n} draft${n === 1 ? "" : "s"}?`, description: `${scope}. You'll still review and approve each draft before anything is sent.`, confirmLabel: "Write drafts" },
      send: { title: `Send ${n} approved email${n === 1 ? "" : "s"}?`, description: `${scope}. ${sendWarning(mode)}`, confirmLabel: "Send all", destructive: mode === "prod" },
    }[kind]
    ask({
      ...copy,
      onConfirm: async () => {
        const queued = await run(`bulk:${kind}`, () => post(BULK_PATHS[kind], { dry_run: false, contact_ids: contactIds }), `Queued ${n}`)
        if (queued) {
          setSelection({})
          watchForAWhile()
        }
      },
    })
  }

  const bulkButton = (kind: BulkKind, label: string, permission: string) =>
    can(permission) && (
      <Button size="sm" variant="outline" onClick={() => bulk(kind)} disabled={isPending(`bulk:${kind}`)}>
        {isPending(`bulk:${kind}`) && <Loader2Icon className="animate-spin" />}
        {label}
      </Button>
    )

  return (
    <>
      <PageHeader title="Contacts" description="Approved people: verify they still work there, find their email, then draft, approve and send." />
      <DataTable
        columns={columns}
        data={contacts.data}
        loading={contacts.loading}
        error={contacts.error?.message}
        getRowId={(c) => c.id}
        search="Search contacts"
        empty={view === "no_email" ? "Everyone has an email." : "No contacts here yet — approve candidates first."}
        rowSelection={selection}
        onRowSelectionChange={setSelection}
        toolbar={
          <>
            <Tabs
              value={view}
              onValueChange={(value) => {
                setView(value as ContactView)
                setSelection({})
              }}
            >
              <TabsList>
                {VIEWS.map((v) => (
                  <TabsTrigger key={v.value} value={v.value}>
                    {v.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <Select value={resumeId} onValueChange={setResumeId}>
              <SelectTrigger className="w-44">
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
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <span className="text-sm text-muted-foreground">{ids.length ? `${ids.length} selected` : "Bulk (all eligible):"}</span>
              {bulkButton("lookup", "Find emails", "contacts.act")}
              {bulkButton("drafts", "Write drafts", "drafts.act")}
              {bulkButton("send", "Send approved", "emails.send")}
            </div>
          </>
        }
      />

      <DraftDialog key={draftFor ?? "none"} contactId={draftFor} onClose={() => setDraftFor(null)} onChanged={reload} />
      <EmailDialog key={emailFor?.id ?? "none"} contact={emailFor} onClose={() => setEmailFor(null)} onSaved={reload} />
      {confirm && (
        <ConfirmDialog
          open={confirmOpen}
          onOpenChange={setConfirmOpen}
          title={confirm.title}
          description={confirm.description}
          confirmLabel={confirm.confirmLabel}
          destructive={confirm.destructive}
          onConfirm={confirm.onConfirm}
        />
      )}
    </>
  )
}
