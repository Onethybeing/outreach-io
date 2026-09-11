"use client"

import {
  BotIcon, ChartColumnIcon, FileTextIcon, InboxIcon, Loader2Icon, LogOutIcon, SettingsIcon, UserSearchIcon, UsersIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { createContext, useCallback, useContext, useMemo } from "react"

import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import { post } from "@/lib/api"
import type { AppSettings, Me } from "@/lib/types"
import { useApi } from "@/lib/use-api"
import { cn } from "@/lib/utils"

type Session = {
  me: Me
  settings: AppSettings
  /** Mirrors the backend's RBAC so the UI hides what would 403; the API is still the real check. */
  can: (permission: string) => boolean
  reloadSettings: () => void
}

const SessionContext = createContext<Session | null>(null)

export function useSession(): Session {
  const session = useContext(SessionContext)
  if (!session) throw new Error("useSession must be used inside <AppShell>")
  return session
}

const NAV = [
  { href: "/", label: "Stats", icon: ChartColumnIcon },
  { href: "/library", label: "Library", icon: FileTextIcon },
  { href: "/runs", label: "Run agent", icon: BotIcon },
  { href: "/candidates", label: "Candidates", icon: UserSearchIcon },
  { href: "/contacts", label: "Contacts", icon: UsersIcon },
  { href: "/replies", label: "Replies", icon: InboxIcon },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
]

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const me = useApi<Me>("/auth/me")
  const settings = useApi<AppSettings>(me.data ? "/settings" : null)

  const permissions = me.data?.permissions
  const can = useCallback((permission: string) => permissions?.includes(permission) ?? false, [permissions])
  const session = useMemo(
    () => (me.data && settings.data ? { me: me.data, settings: settings.data, can, reloadSettings: settings.reload } : null),
    [me.data, settings.data, settings.reload, can],
  )

  if (me.error?.status === 401) return <SignIn />
  const failure = me.error ?? settings.error
  if (failure && !session) {
    return (
      <Centered>
        <p className="text-sm text-destructive">Couldn&apos;t load the dashboard: {failure.message}</p>
        <Button variant="outline" onClick={() => (me.error ? me.reload() : settings.reload())}>
          Try again
        </Button>
      </Centered>
    )
  }
  if (!session) {
    return (
      <Centered>
        <Loader2Icon className="size-5 animate-spin text-muted-foreground" />
      </Centered>
    )
  }

  async function signOut() {
    await post("/auth/logout").catch(() => undefined)
    // A full reload, so no signed-in data survives in memory.
    window.location.reload()
  }

  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href))

  return (
    <SessionContext.Provider value={session}>
      <div className="flex min-h-screen flex-col md:flex-row">
        <aside className="border-b bg-sidebar md:sticky md:top-0 md:h-screen md:w-52 md:shrink-0 md:border-r md:border-b-0">
          <div className="flex items-center justify-between px-4 py-3 md:py-4">
            <span className="font-semibold tracking-tight">Outreach</span>
          </div>
          <nav className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:overflow-visible">
            {NAV.map(({ href, label, icon: Icon }) => (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-foreground",
                  isActive(href) && "bg-sidebar-accent font-medium text-sidebar-foreground",
                )}
              >
                <Icon className="size-4" />
                {label}
              </Link>
            ))}
          </nav>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2 md:px-6">
            <ModeBadge mode={session.settings.app_mode} />
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">
                {session.me.name ?? session.me.email} · {session.me.role}
              </span>
              <Button variant="ghost" size="sm" onClick={signOut}>
                <LogOutIcon />
                Sign out
              </Button>
            </div>
          </header>
          <main className="flex-1 space-y-6 px-4 py-6 md:px-6">{children}</main>
        </div>
      </div>
    </SessionContext.Provider>
  )
}

function ModeBadge({ mode }: { mode: string }) {
  if (mode === "prod") {
    return <Badge variant="destructive">Production mode — emails go to real inboxes</Badge>
  }
  return (
    <Badge variant="outline" className="border-sky-200 bg-sky-50 text-sky-700">
      Dev mode — emails are saved as files, nothing is sent
    </Badge>
  )
}

function SignIn() {
  return (
    <Centered>
      <h1 className="text-lg font-semibold">Outreach</h1>
      <p className="text-sm text-muted-foreground">Sign in with a Google account an admin has added.</p>
      {/* A full page load, not <Link>: /auth/login is served by the API and redirects to Google. */}
      <a href="/auth/login" className={buttonVariants()}>
        Sign in with Google
      </a>
      <p className="text-xs text-muted-foreground">
        <Link href="/about" className="underline">
          About
        </Link>{" "}
        ·{" "}
        <Link href="/privacy" className="underline">
          Privacy
        </Link>
      </p>
    </Centered>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">{children}</div>
}
