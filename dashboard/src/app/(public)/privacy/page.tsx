import type { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "Privacy policy — Outreach",
  description: "What Outreach stores, why, and how to have it deleted.",
}

const UPDATED = "12 September 2026"

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-2xl space-y-8 px-6 py-16">
      <div className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Privacy policy</h1>
        <p className="text-sm text-muted-foreground">Last updated: {UPDATED}</p>
      </div>

      <Section title="Who runs this">
        <p>
          Outreach is a private tool operated by Sourav Jhinjha for personal job-search outreach. It is not offered to
          the public and has no sign-up: only accounts the administrator adds can sign in. Contact:{" "}
          <a className="underline" href="mailto:sourav.jhinjha@gmail.com">sourav.jhinjha@gmail.com</a>.
        </p>
      </Section>

      <Section title="What is stored">
        <ul className="list-disc space-y-1.5 pl-5">
          <li>
            <strong>Your Google account</strong> — email address and name, taken from Google sign-in, to identify you and
            record what you did in the app.
          </li>
          <li>
            <strong>CVs you upload</strong> — the file and the text extracted from it.
          </li>
          <li>
            <strong>Business contact details</strong> — names, job titles, employers, LinkedIn profile links and work email
            addresses of people at the companies being contacted, collected from search results and from Apollo and
            Bright Data.
          </li>
          <li>
            <strong>Emails</strong> — the drafts written in the app, and the sent and received messages in the connected
            Gmail account that belong to those conversations, so replies can be matched to the right contact.
          </li>
          <li>
            <strong>Activity records</strong> — an audit log of actions taken in the app, and API-usage counters.
          </li>
        </ul>
      </Section>

      <Section title="Google account data">
        <p>
          Signing in uses Google&apos;s OpenID Connect; the app receives your email address, name and profile picture URL, and
          never your password. Sending and reply tracking use the Gmail API on the connected sending account only. Outreach&apos;s
          use of information received from Google APIs follows the{" "}
          <a className="underline" href="https://developers.google.com/terms/api-services-user-data-policy" target="_blank" rel="noreferrer">
            Google API Services User Data Policy
          </a>
          , including the Limited Use requirements. Gmail data is used solely to send the outreach emails you approve and to
          detect replies to them. It is never sold, never used for advertising, and never used to train any AI model.
        </p>
      </Section>

      <Section title="Where it is stored and who can see it">
        <p>
          Data is held in a Postgres database (Neon, in the United States) and in a private Google Cloud Storage bucket, both
          reachable only by this application. Access is limited to the administrator and to the accounts they have added.
        </p>
        <p>
          Some data is passed to service providers to do their part of the work: Google Cloud (hosting), Neon (database),
          Groq (writing and classifying text), Tavily (web search), Bright Data and Apollo (company and contact lookups),
          Langfuse (tracing) and Gmail (sending and reading replies). Nothing is sold or shared for advertising.
        </p>
      </Section>

      <Section title="How long it is kept">
        <p>
          Records are kept while the tool is in use and deleted on request. If you received an email from this tool and want
          your details removed, or you replied and want the conversation deleted, email{" "}
          <a className="underline" href="mailto:sourav.jhinjha@gmail.com">sourav.jhinjha@gmail.com</a> and it will be removed,
          normally within 30 days. Asking not to be contacted is honoured immediately and permanently.
        </p>
      </Section>

      <Section title="Your choices">
        <p>
          You can ask what is held about you, ask for it to be corrected or deleted, and object to being contacted. Use the
          email address above; no account is needed.
        </p>
      </Section>

      <Section title="Changes">
        <p>Any change to this policy will appear on this page with a new date.</p>
      </Section>

      <footer className="border-t pt-4 text-xs text-muted-foreground">
        <Link href="/about" className="underline">
          About Outreach
        </Link>
      </footer>
    </main>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-medium">{title}</h2>
      <div className="space-y-2 text-sm text-muted-foreground">{children}</div>
    </section>
  )
}
