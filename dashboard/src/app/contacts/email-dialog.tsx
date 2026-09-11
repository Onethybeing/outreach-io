"use client"

import { Loader2Icon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { put } from "@/lib/api"
import type { Contact } from "@/lib/types"
import { useAction } from "@/lib/use-action"

export function EmailDialog({ contact, onClose, onSaved }: { contact: Contact | null; onClose: () => void; onSaved: () => void }) {
  const [email, setEmail] = useState(contact?.email ?? "")
  const { run, isPending } = useAction()

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!contact) return
    const saved = await run("save", () => put<Contact>(`/contacts/${contact.id}/email`, { email: email.trim() }), "Email saved")
    if (saved) {
      onSaved()
      onClose()
    }
  }

  return (
    <Dialog open={contact !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form onSubmit={save} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Enter {contact?.name}&apos;s email</DialogTitle>
            <DialogDescription>For an address you found yourself. It skips the lookup, so you can write a draft right away.</DialogDescription>
          </DialogHeader>
          <Input type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={isPending("save")}>
              {isPending("save") && <Loader2Icon className="animate-spin" />}
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
