"use client"

import { Loader2Icon } from "lucide-react"
import { useState } from "react"

import { useSession } from "@/components/app-shell"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { put } from "@/lib/api"
import { humanize } from "@/lib/format"
import type { AppSettings } from "@/lib/types"
import { useAction } from "@/lib/use-action"

export function GeneralTab() {
  const { settings, can, reloadSettings } = useSession()
  const { run, isPending } = useAction()
  const [confirmProd, setConfirmProd] = useState(false)

  async function changeMode(mode: string) {
    const saved = await run("mode", () => put<AppSettings>("/settings/mode", { mode }), (s) =>
      s.app_mode === "prod" ? "Production mode — emails now go to the real contacts" : `Dev mode — emails go to ${s.dev_redirect_email}`,
    )
    if (saved) reloadSettings()
  }

  async function changeProvider(provider: string) {
    const saved = await run("provider", () => put<AppSettings>("/settings/email-provider", { provider }), `Email lookups now use ${humanize(provider)}`)
    if (saved) reloadSettings()
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Sending mode</CardTitle>
          <CardDescription>Who receives the emails you send. Both modes send real mail through Gmail.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="flex items-center gap-2">
            {settings.app_mode === "prod" ? (
              <Badge variant="destructive">Production</Badge>
            ) : (
              <Badge variant="outline" className="border-sky-200 bg-sky-50 text-sky-700">
                Dev
              </Badge>
            )}
            {can("mode.toggle") && (
              <Button
                size="sm"
                variant={settings.app_mode === "prod" ? "outline" : "destructive"}
                disabled={isPending("mode")}
                onClick={() => (settings.app_mode === "prod" ? changeMode("dev") : setConfirmProd(true))}
              >
                {isPending("mode") && <Loader2Icon className="animate-spin" />}
                {settings.app_mode === "prod" ? "Switch back to dev" : "Switch to production"}
              </Button>
            )}
          </div>
          <p className="text-muted-foreground">
            {settings.app_mode === "prod"
              ? "Emails go to the contacts' own addresses. Real people receive them."
              : `Every email is sent to ${settings.dev_redirect_email ?? "your sending account"} instead of the contact, with the intended recipient in the subject. Contacts receive nothing.`}
          </p>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={confirmProd}
        onOpenChange={setConfirmProd}
        title="Switch to production mode?"
        description="From then on, every email you send goes to the real person it is addressed to, not to your inbox. Sending still needs you to approve each draft."
        confirmLabel="Switch to production"
        destructive
        onConfirm={() => changeMode("prod")}
      />

      <Card>
        <CardHeader>
          <CardTitle>Email lookup provider</CardTitle>
          <CardDescription>Used by Find email, for one contact or in bulk.</CardDescription>
        </CardHeader>
        <CardContent>
          <Select value={settings.email_provider} onValueChange={changeProvider} disabled={!can("settings.manage") || isPending("provider")}>
            <SelectTrigger className="w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {settings.email_providers.map((provider) => (
                <SelectItem key={provider} value={provider}>
                  {humanize(provider)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </CardContent>
      </Card>
    </div>
  )
}
