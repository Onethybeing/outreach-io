"use client"

import { CheckIcon, FlaskConicalIcon, Loader2Icon, RotateCcwIcon, SendIcon, SparklesIcon } from "lucide-react"
import { useState } from "react"

import { useSession } from "@/components/app-shell"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { StatusBadge } from "@/components/status-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { post, put } from "@/lib/api"
import { fmtDate, humanize } from "@/lib/format"
import type { Contact, EmailEvent } from "@/lib/types"
import { useAction } from "@/lib/use-action"
import { useApi } from "@/lib/use-api"

export const SENT: string[] = ["sent", "sent_dev"]

export function sendWarning(mode: string, to?: string | null): string {
  const target = to ? ` to ${to}` : ""
  return mode === "prod"
    ? `This sends a real email${target} from your Gmail, with the CV attached.`
    : `Dev mode: the email${target} and its CV attachment are saved as a .eml file on the server. Nothing reaches an inbox.`
}

export function sendSuccess(contact: Contact): string {
  return contact.send_status === "sent_dev" ? "Saved as a dev email (.eml) — nothing was sent" : `Sent to ${contact.email}`
}

type DraftDialogProps = { contactId: string | null; onClose: () => void; onChanged: () => void }

export function DraftDialog({ contactId, onClose, onChanged }: DraftDialogProps) {
  const { can, settings } = useSession()
  const contact = useApi<Contact>(contactId ? `/contacts/${contactId}` : null)
  const emails = useApi<EmailEvent[]>(contactId ? `/contacts/${contactId}/emails` : null)
  const [edit, setEdit] = useState<{ subject: string; body: string } | null>(null)
  const [confirm, setConfirm] = useState<"send" | "rewrite" | null>(null)
  const [judgement, setJudgement] = useState<Record<string, unknown> | null>(null)
  const { run, isPending } = useAction()

  const c = contact.data
  const subject = edit?.subject ?? c?.draft_subject ?? ""
  const body = edit?.body ?? c?.draft_text ?? ""
  const dirty = !!c && edit !== null && (edit.subject !== (c.draft_subject ?? "") || edit.body !== (c.draft_text ?? ""))
  const alreadySent = !!c && SENT.includes(c.send_status)
  const editable = !!c && c.draft_status !== "none" && !alreadySent && c.send_status !== "queued" && can("drafts.act")

  function applied(next: Contact | undefined) {
    if (next) {
      contact.mutate(() => next)
      setEdit(null)
      setJudgement(null)
      onChanged()
    }
    return next
  }

  const save = async () => applied(await run("save", () => put<Contact>(`/contacts/${contactId}/draft`, { subject, body }), "Draft saved"))

  async function approve() {
    if (dirty && !(await save())) return
    applied(await run("approve", () => post<Contact>(`/contacts/${contactId}/draft/approve`), "Draft approved"))
  }

  const generate = async (force: boolean) =>
    applied(await run("generate", () => post<Contact>(`/contacts/${contactId}/draft/generate${force ? "?force=true" : ""}`), "Draft written"))

  async function send() {
    applied(await run("send", () => post<Contact>(`/contacts/${contactId}/send${alreadySent ? "?force=true" : ""}`), sendSuccess))
    emails.reload()
  }

  async function judge() {
    const result = await run("judge", () => post<Record<string, unknown>>(`/evals/contacts/${contactId}/draft`))
    if (result) setJudgement(result)
  }

  const spin = (key: string, icon: React.ReactNode) => (isPending(key) ? <Loader2Icon className="animate-spin" /> : icon)

  return (
    <Dialog open={contactId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{c ? `Email to ${c.name}` : "Email"}</DialogTitle>
          <DialogDescription>
            {c ? `${c.title ? `${c.title} at ` : ""}${c.startup_name} · ${c.email ?? "no email yet"} · CV: ${c.cv_filename}` : "Loading…"}
          </DialogDescription>
        </DialogHeader>

        {contact.error && !c && <p className="text-sm text-destructive">{contact.error.message}</p>}
        {!c ? (
          !contact.error && <Skeleton className="h-64" />
        ) : c.draft_status === "none" ? (
          <div className="space-y-3 text-sm text-muted-foreground">
            <p>No draft yet.</p>
            {can("drafts.act") && (
              <Button onClick={() => generate(false)} disabled={isPending("generate")}>
                {spin("generate", <SparklesIcon />)} Write draft
              </Button>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <StatusBadge status={c.draft_status} />
              {c.draft_edited && <Badge variant="secondary">edited</Badge>}
              {c.send_status !== "none" && <StatusBadge status={c.send_status} />}
              <span>
                Written {fmtDate(c.draft_generated_at)}
                {c.draft_approved_at && ` · approved ${fmtDate(c.draft_approved_at)}`}
                {c.sent_at && ` · sent ${fmtDate(c.sent_at)}`}
              </span>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="draft-subject">Subject</Label>
              <Input id="draft-subject" value={subject} readOnly={!editable} onChange={(e) => setEdit({ subject: e.target.value, body })} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="draft-body">Body</Label>
              <Textarea id="draft-body" value={body} readOnly={!editable} onChange={(e) => setEdit({ subject, body: e.target.value })} className="min-h-72 text-sm" />
            </div>
            {judgement && <Judgement value={judgement} />}
          </div>
        )}

        {emails.data && emails.data.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Thread</h3>
            {emails.data.map((e) => (
              <div key={e.id} className="rounded-md border p-2 text-sm">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span>
                    {e.direction === "out" ? "Sent" : "Received"} {fmtDate(e.received_at)}
                  </span>
                  {e.classification && <StatusBadge status={e.classification} />}
                </div>
                <p className="mt-1 whitespace-pre-wrap">{e.snippet ?? "—"}</p>
              </div>
            ))}
          </div>
        )}

        {c && c.draft_status !== "none" && (
          <DialogFooter className="gap-2 sm:justify-between">
            <div className="flex flex-wrap gap-2">
              {can("drafts.act") && (
                <>
                  <Button variant="ghost" onClick={() => setConfirm("rewrite")} disabled={isPending("generate") || c.do_not_contact}>
                    {spin("generate", <RotateCcwIcon />)} Rewrite
                  </Button>
                  <Button variant="ghost" onClick={judge} disabled={isPending("judge") || dirty} title={dirty ? "Save your changes first" : "Score this draft with the LLM judge"}>
                    {spin("judge", <FlaskConicalIcon />)} Judge
                  </Button>
                </>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              {editable && dirty && (
                <Button variant="outline" onClick={save} disabled={isPending("save")}>
                  Save changes
                </Button>
              )}
              {can("drafts.act") && c.draft_status === "generated" && (
                <Button onClick={approve} disabled={isPending("approve") || isPending("save")}>
                  {spin("approve", <CheckIcon />)} {dirty ? "Save and approve" : "Approve"}
                </Button>
              )}
              {can("emails.send") && c.draft_status === "approved" && c.send_status !== "queued" && (
                <Button onClick={() => setConfirm("send")} disabled={dirty || isPending("send") || !c.email || c.do_not_contact}>
                  {spin("send", <SendIcon />)} {alreadySent ? "Send again" : "Send"}
                </Button>
              )}
            </div>
          </DialogFooter>
        )}

        <ConfirmDialog
          open={confirm === "rewrite"}
          onOpenChange={(open) => !open && setConfirm(null)}
          title="Rewrite this draft?"
          description={alreadySent ? "They've already been emailed. The new draft replaces this one." : "The new draft replaces this one, including any edits."}
          confirmLabel="Rewrite"
          onConfirm={() => generate(true)}
        />
        <ConfirmDialog
          open={confirm === "send"}
          onOpenChange={(open) => !open && setConfirm(null)}
          title={alreadySent ? `Email ${c?.name} again?` : `Send to ${c?.email}?`}
          description={sendWarning(settings.app_mode, c?.email)}
          confirmLabel={alreadySent ? "Send again" : "Send"}
          destructive={settings.app_mode === "prod"}
          onConfirm={send}
        />
      </DialogContent>
    </Dialog>
  )
}

function Judgement({ value }: { value: Record<string, unknown> }) {
  return (
    <div className="rounded-md border bg-muted/40 p-3 text-sm">
      <div className="mb-2 font-medium">LLM judge</div>
      <dl className="space-y-1">
        {Object.entries(value).map(([key, v]) => (
          <div key={key} className="grid grid-cols-[10rem_1fr] gap-2">
            <dt className="text-muted-foreground">{humanize(key)}</dt>
            <dd className="whitespace-pre-wrap">
              {typeof v === "string" || typeof v === "number" || typeof v === "boolean" ? String(v) : Array.isArray(v) && v.length === 0 ? "none" : JSON.stringify(v, null, 2)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
