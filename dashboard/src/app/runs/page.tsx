import { RunsView } from "./runs-view"

export default async function RunsPage({ searchParams }: PageProps<"/runs">) {
  const { resume, run } = await searchParams
  return (
    <RunsView
      initialResumeId={typeof resume === "string" ? resume : undefined}
      initialRunId={typeof run === "string" ? run : undefined}
    />
  )
}
