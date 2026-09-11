import type { ContactView } from "@/lib/types"

import { ContactsView } from "./contacts-view"

const VIEWS: string[] = ["active", "sent", "no_email", "all"] satisfies ContactView[]

export default async function ContactsPage({ searchParams }: PageProps<"/contacts">) {
  const { view, resume } = await searchParams
  return (
    <ContactsView
      initialView={typeof view === "string" && VIEWS.includes(view) ? (view as ContactView) : "active"}
      initialResumeId={typeof resume === "string" ? resume : undefined}
    />
  )
}
