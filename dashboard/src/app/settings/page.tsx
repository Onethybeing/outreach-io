"use client"

import { useSession } from "@/components/app-shell"
import { PageHeader } from "@/components/page-header"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

import { AuditTab } from "./audit-tab"
import { GeneralTab } from "./general-tab"
import { PromptsTab } from "./prompts-tab"
import { UsersTab } from "./users-tab"
import { VaultTab } from "./vault-tab"

export default function SettingsPage() {
  const { can } = useSession()
  const tabs = [
    { value: "general", label: "General", show: true, content: <GeneralTab /> },
    { value: "vault", label: "API vault", show: can("vault.manage"), content: <VaultTab /> },
    { value: "prompts", label: "Prompts", show: can("prompts.view"), content: <PromptsTab /> },
    { value: "users", label: "Users", show: can("users.manage"), content: <UsersTab /> },
    { value: "audit", label: "Audit log", show: can("audit.view"), content: <AuditTab /> },
  ].filter((tab) => tab.show)

  return (
    <>
      <PageHeader title="Settings" />
      <Tabs defaultValue="general">
        <TabsList>
          {tabs.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {tabs.map((tab) => (
          <TabsContent key={tab.value} value={tab.value} className="mt-4">
            {tab.content}
          </TabsContent>
        ))}
      </Tabs>
    </>
  )
}
