import type { Metadata } from "next"
import Image from "next/image"
import Link from "next/link"

import { buttonVariants } from "@/components/ui/button"

export const metadata: Metadata = {
  title: "About Outreach",
  description: "A personal job-search assistant: it finds relevant startups, checks who to contact, and drafts emails for review.",
}

export default function AboutPage() {
  return (
    <main className="mx-auto max-w-2xl space-y-8 px-6 py-16">
      <div className="space-y-3">
        <Image src="/logo.png" alt="" width={64} height={64} className="dark:invert" priority />
        <h1 className="text-3xl font-semibold tracking-tight">Outreach</h1>
        <p className="text-lg text-muted-foreground">
          A personal job-search assistant. It reads a CV, finds startups that fit it, works out who to talk to, and writes
          the emails, but a person approves every message before it is sent.
        </p>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">How it works</h2>
        <ol className="list-decimal space-y-2 pl-5 text-sm text-muted-foreground">
          <li>Upload a CV. The agent reads it and searches for startups where that background fits.</li>
          <li>For each startup it proposes a few decision-makers, with a reason and a LinkedIn link.</li>
          <li>You approve the people worth contacting. The agent checks they still work there and looks up an address.</li>
          <li>It drafts a personal email for each one. You edit and approve it; nothing is sent without that approval.</li>
          <li>Replies are matched back to the right contact, so you can see who answered.</li>
        </ol>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Who it&apos;s for</h2>
        <p className="text-sm text-muted-foreground">
          This is a private tool run by its author for their own job search. Sign-in is limited to accounts the
          administrator has added; there is no public sign-up.
        </p>
      </section>

      <div className="flex flex-wrap items-center gap-3 pt-2">
        <Link href="/" className={buttonVariants()}>
          Open the dashboard
        </Link>
        <Link href="/privacy" className={buttonVariants({ variant: "outline" })}>
          Privacy policy
        </Link>
      </div>

      <footer className="border-t pt-4 text-xs text-muted-foreground">
        Questions: <a className="underline" href="mailto:sourav.jhinjha@gmail.com">sourav.jhinjha@gmail.com</a>
      </footer>
    </main>
  )
}
