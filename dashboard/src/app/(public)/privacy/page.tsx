import type { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "Privacy policy for Outreach",
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
            <strong>Your Google account</strong>: email address and name, taken from Google sign-in, to identify you and
            record what you did in the app.
          </li>
          <li>
            <strong>CVs you upload</strong>: the file and the text extracted from it.
          </li>
          <li>
            <strong>Business contact details</strong>: names, job titles, employers and LinkedIn profile links of people at
            the companies being contacted, found through web search and checked against Bright Data and Apollo. Work email
            addresses come from Apollo, or an operator types in an address they already had.
          </li>
          <li>
            <strong>Emails</strong>: the drafts written in the app, and the sent and received messages in the connected
            Gmail account that belong to those conversations, so replies can be matched to the right contact.
          </li>
          <li>
            <strong>Activity records</strong>: an audit log of actions taken in the app, and API-usage counters.
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
          , including the Limited Use requirements. Gmail data is used only to send the outreach emails an operator approves
          and to track answers to them. To do that, the text of an incoming reply is sent to Groq, which labels it as a reply,
          a bounce, an out-of-office note or an unsubscribe request, and a record of that call is kept in Langfuse. Both act
          only on instructions from this app. Gmail data is never sold, never used for advertising, never used to train any AI
          model, and is not shared with anyone else.
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
          <a className="underline" href="mailto:sourav.jhinjha@gmail.com">sourav.jhinjha@gmail.com</a>. The administrator
          removes the records by hand, normally within 30 days: the contact details, the record of the emails, the stored
          copies of the messages, and (removed by the administrator in the same pass) the messages in the sending mailbox
          and the diagnostic traces of anything written about you.
        </p>
        <p>
          One thing is deliberately kept: a one-way hash of your LinkedIn profile address. No name, email address or message
          text is kept with it, and it exists only so a later search can recognise that you asked to be left alone.
        </p>
        <p>
          If you ask not to be contacted, your record is marked accordingly and no further email is sent to you. Replies that
          read as an unsubscribe request are detected automatically and set the same mark without anyone having to act.
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
