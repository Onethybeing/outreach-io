"use client"

import { HistoryIcon, KeyRoundIcon, Loader2Icon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { ConfirmDialog } from "@/components/confirm-dialog"
import { StatusBadge } from "@/components/status-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { post, put } from "@/lib/api"
import { fmtDate, humanize } from "@/lib/format"
import type { CredentialVersion, ProviderStatus, TestResult } from "@/lib/types"
import { useAction } from "@/lib/use-action"
import { useApi } from "@/lib/use-api"

// Not secret, so they're shown while typing.
const VISIBLE_FIELDS = new Set(["host", "sender_address", "client_id"])

function reportTest(provider: string, result: TestResult) {
  if (result.ok) toast.success(`${provider}: ${result.message}`)
  else toast.error(`${provider}: ${result.message}`)
}

export function VaultTab() {
  const providers = useApi<ProviderStatus[]>("/vault")
  const [rotating, setRotating] = useState<ProviderStatus | null>(null)
  const [history, setHistory] = useState<ProviderStatus | null>(null)
  const { run, isPending } = useAction()

  async function test(p: ProviderStatus) {
    const result = await run(`test:${p.provider}`, () => post<TestResult>(`/vault/${p.provider}/test`))
    if (result) reportTest(p.provider, result)
    providers.reload()
  }

  if (providers.error && !providers.data) return <p className="text-sm text-destructive">{providers.error.message}</p>

  return (
    <>
      <p className="mb-4 text-sm text-muted-foreground">
        Keys are encrypted and never shown again. A new key is tested before it replaces the old one, and the old one stays available for rollback for 7 days.
      </p>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {!providers.data && Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-40" />)}
        {providers.data?.map((p) => (
          <Card key={p.provider}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 capitalize">
                {p.provider}
                {p.configured ? (
                  <StatusBadge status={p.last_test_ok === false ? "failed" : "active"} />
                ) : (
                  <Badge variant="outline">not set</Badge>
                )}
              </CardTitle>
              <CardDescription>
                {p.configured
                  ? `v${p.version} · ••••${p.last4} · saved ${fmtDate(p.created_at)}${p.created_by ? ` by ${p.created_by}` : ""}`
                  : `Needs: ${p.fields.map(humanize).join(", ")}`}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-muted-foreground">
                {p.last_tested_at ? `Last test ${p.last_test_ok ? "passed" : "failed"} ${fmtDate(p.last_tested_at)}` : "Not tested yet"}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" onClick={() => setRotating(p)}>
                  <KeyRoundIcon /> {p.configured ? "Rotate" : "Add key"}
                </Button>
                {p.configured && (
                  <Button size="sm" variant="outline" onClick={() => test(p)} disabled={isPending(`test:${p.provider}`)}>
                    {isPending(`test:${p.provider}`) && <Loader2Icon className="animate-spin" />} Test
                  </Button>
                )}
                {p.configured && (
                  <Button size="sm" variant="ghost" onClick={() => setHistory(p)}>
                    <HistoryIcon /> History
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      <RotateDialog key={rotating?.provider ?? "none"} provider={rotating} onClose={() => setRotating(null)} onSaved={providers.reload} />
      <HistoryDialog key={`history:${history?.provider ?? "none"}`} provider={history} onClose={() => setHistory(null)} onChanged={providers.reload} />
    </>
  )
}

function RotateDialog({ provider, onClose, onSaved }: { provider: ProviderStatus | null; onClose: () => void; onSaved: () => void }) {
  const [values, setValues] = useState<Record<string, string>>({})
  const { run, isPending } = useAction()

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!provider) return
    const result = await run("save", () => put<TestResult>(`/vault/${provider.provider}`, { values }), (r) => r.message)
    if (result) {
      onSaved()
      onClose()
    }
  }

  return (
    <Dialog open={provider !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form onSubmit={save} className="space-y-4" autoComplete="off">
          <DialogHeader>
            <DialogTitle className="capitalize">{provider?.configured ? `Rotate ${provider.provider} key` : `Add ${provider?.provider} key`}</DialogTitle>
            <DialogDescription>It&apos;s tested first; if the test fails, nothing changes.</DialogDescription>
          </DialogHeader>
          {provider?.fields.map((field) => (
            <div key={field} className="space-y-1.5">
              <Label htmlFor={`vault-${field}`}>{humanize(field)}</Label>
              <Input
                id={`vault-${field}`}
                type={VISIBLE_FIELDS.has(field) ? "text" : "password"}
                autoComplete="off"
                required
                value={values[field] ?? ""}
                onChange={(e) => setValues((prev) => ({ ...prev, [field]: e.target.value }))}
              />
            </div>
          ))}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={isPending("save")}>
              {isPending("save") && <Loader2Icon className="animate-spin" />}
              Test and save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function HistoryDialog({ provider, onClose, onChanged }: { provider: ProviderStatus | null; onClose: () => void; onChanged: () => void }) {
  const versions = useApi<CredentialVersion[]>(provider ? `/vault/${provider.provider}/versions` : null)
  const [target, setTarget] = useState<number | null>(null)
  const { run } = useAction()

  async function rollback(version: number) {
    if (!provider) return
    const result = await run("rollback", () => post<TestResult>(`/vault/${provider.provider}/rollback/${version}`), (r) => r.message)
    if (result) {
      versions.reload()
      onChanged()
    }
  }

  return (
    <Dialog open={provider !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="capitalize">{provider?.provider} key history</DialogTitle>
          <DialogDescription>Retired keys can be restored for 7 days.</DialogDescription>
        </DialogHeader>
        {!versions.data ? (
          <Skeleton className="h-32" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Version</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Key</TableHead>
                <TableHead>Saved</TableHead>
                <TableHead>Retired</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {versions.data.map((v) => (
                <TableRow key={v.version}>
                  <TableCell>v{v.version}</TableCell>
                  <TableCell>
                    <StatusBadge status={v.status} />
                  </TableCell>
                  <TableCell className="font-mono">••••{v.last4}</TableCell>
                  <TableCell>{fmtDate(v.created_at)}</TableCell>
                  <TableCell>{fmtDate(v.retired_at)}</TableCell>
                  <TableCell className="text-right">
                    {v.status === "retired" && (
                      <Button size="sm" variant="outline" onClick={() => setTarget(v.version)}>
                        Roll back
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <ConfirmDialog
          open={target !== null}
          onOpenChange={(open) => !open && setTarget(null)}
          title={`Restore v${target}?`}
          description="It's tested first. The current key is retired and can be restored the same way."
          confirmLabel="Roll back"
          onConfirm={() => (target === null ? undefined : rollback(target))}
        />
      </DialogContent>
    </Dialog>
  )
}
