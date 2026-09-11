"use client"

import { useSession } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { put } from "@/lib/api"
import { humanize } from "@/lib/format"
import type { AppSettings } from "@/lib/types"
import { useAction } from "@/lib/use-action"

export function GeneralTab() {
  const { settings, can, reloadSettings } = useSession()
  const { run, isPending } = useAction()

  async function changeProvider(provider: string) {
    const saved = await run("provider", () => put<AppSettings>("/settings/email-provider", { provider }), `Email lookups now use ${humanize(provider)}`)
    if (saved) reloadSettings()
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Sending mode</CardTitle>
          <CardDescription>Set with APP_MODE on the server.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {settings.app_mode === "prod" ? (
            <Badge variant="destructive">Production</Badge>
          ) : (
            <Badge variant="outline" className="border-sky-200 bg-sky-50 text-sky-700">
              Dev
            </Badge>
          )}
          <p className="text-muted-foreground">
            {settings.app_mode === "prod"
              ? "Real sending stays locked on the server until it's explicitly turned on."
              : "Send saves each email as a .eml file with the CV attached. Nothing reaches an inbox."}
          </p>
        </CardContent>
      </Card>

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
