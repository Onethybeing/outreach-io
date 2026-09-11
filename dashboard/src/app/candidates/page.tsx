import { CandidatesView } from "./candidates-view"

export default async function CandidatesPage({ searchParams }: PageProps<"/candidates">) {
  const { run } = await searchParams
  return <CandidatesView initialRunId={typeof run === "string" ? run : undefined} />
}
