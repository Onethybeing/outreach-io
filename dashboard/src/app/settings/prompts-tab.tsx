"use client"

import { FlaskConicalIcon, Loader2Icon, RotateCcwIcon, SaveIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { useSession } from "@/components/app-shell"
import { ConfirmDialog } from "@/components/confirm-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { api, post } from "@/lib/api"
import { fmtDate, humanize } from "@/lib/format"
import type { NodePrompt, PromptTestOut, PromptVersion } from "@/lib/types"
import { useAction } from "@/lib/use-action"
import { useApi } from "@/lib/use-api"
import { cn } from "@/lib/utils"

export function PromptsTab() {
  const nodes = useApi<NodePrompt[]>("/prompts")
  const [choice, setChoice] = useState<string | null>(null)
  const selected = nodes.data?.find((n) => n.node === choice) ?? nodes.data?.[0]

  if (nodes.error && !nodes.data) return <p className="text-sm text-destructive">{nodes.error.message}</p>

  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
      <Card className="h-fit">
        <CardContent className="space-y-1 px-2">
          {!nodes.data && <Skeleton className="h-40" />}
          {nodes.data?.map((n) => (
            <button
              key={n.node}
              type="button"
              onClick={() => setChoice(n.node)}
              className={cn("w-full rounded-md px-2 py-2 text-left text-sm hover:bg-muted", n.node === selected?.node && "bg-muted")}
            >
              <div className="font-medium">{humanize(n.node)}</div>
              <div className="text-xs text-muted-foreground">{n.active ? `v${n.active.version} · ${n.active.model}` : "no active version"}</div>
            </button>
          ))}
        </CardContent>
      </Card>
      {/* Keyed by the active version, so activating or resetting reloads the editor. */}
      {selected && <PromptEditor key={`${selected.node}:${selected.active?.id ?? "none"}`} prompt={selected} onChanged={nodes.reload} />}
    </div>
  )
}

function PromptEditor({ prompt, onChanged }: { prompt: NodePrompt; onChanged: () => void }) {
  const { can } = useSession()
  const canEdit = can("prompts.edit")
  const [template, setTemplate] = useState(prompt.active?.template ?? "")
  const [model, setModel] = useState(prompt.active?.model ?? "")
  const [temperature, setTemperature] = useState(String(prompt.active?.temperature ?? 0.2))
  const [note, setNote] = useState("")
  const [activate, setActivate] = useState(true)
  const [variables, setVariables] = useState("")
  const [testResult, setTestResult] = useState<PromptTestOut | null>(null)
  const [diff, setDiff] = useState<{ label: string; text: string } | null>(null)
  const [resetOpen, setResetOpen] = useState(false)
  const versions = useApi<PromptVersion[]>(`/prompts/${prompt.node}/versions`)
  const { run, isPending } = useAction()

  function parseVariables(): Record<string, string> | null | undefined {
    if (!variables.trim()) return null // the API fills in sample values
    try {
      const parsed: unknown = JSON.parse(variables)
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return Object.fromEntries(Object.entries(parsed).map(([key, value]) => [key, typeof value === "string" ? value : JSON.stringify(value)]))
      }
    } catch {
      // fall through to the error below
    }
    toast.error('Variables must be a JSON object, e.g. {"company": "Acme"}')
    return undefined
  }

  async function test() {
    const vars = parseVariables()
    if (vars === undefined) return
    const result = await run("test", () => post<PromptTestOut>(`/prompts/${prompt.node}/test`, { template, model, temperature: Number(temperature), variables: vars }))
    if (result) setTestResult(result)
  }

  async function save() {
    const saved = await run(
      "save",
      () => post<PromptVersion>(`/prompts/${prompt.node}/versions`, { template, model, temperature: Number(temperature), note: note || null, activate }),
      (v) => `Saved v${v.version}${v.is_active ? " and made it active" : ""}`,
    )
    if (!saved) return
    versions.reload()
    if (saved.is_active) onChanged()
  }

  async function activateVersion(v: PromptVersion) {
    const done = await run(`activate:${v.version}`, () => post(`/prompts/${prompt.node}/activate/${v.version}`), `v${v.version} is now active`)
    if (done) onChanged()
  }

  async function showDiff(v: PromptVersion) {
    const active = prompt.active
    if (!active) return
    const result = await run(`diff:${v.version}`, () => api<{ diff: string }>(`/prompts/${prompt.node}/diff?a=${active.version}&b=${v.version}`))
    if (result) setDiff({ label: `v${active.version} → v${v.version}`, text: result.diff || "No differences." })
  }

  function load(v: PromptVersion) {
    setTemplate(v.template)
    setModel(v.model)
    setTemperature(String(v.temperature))
    toast.info(`Loaded v${v.version} into the editor`)
  }

  const variableNames = [...prompt.required_variables.map((v) => ({ v, required: true })), ...prompt.optional_variables.map((v) => ({ v, required: false }))]

  return (
    <div className="min-w-0 space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>{humanize(prompt.node)}</CardTitle>
          <CardDescription>{prompt.description}</CardDescription>
          {canEdit && (
            <CardAction>
              <Button variant="ghost" size="sm" onClick={() => setResetOpen(true)}>
                <RotateCcwIcon /> Reset to default
              </Button>
            </CardAction>
          )}
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted-foreground">Variables:</span>
            {variableNames.map(({ v, required }) => (
              <Badge key={v} variant={required ? "secondary" : "outline"} className="font-mono" title={required ? "Required" : "Optional"}>
                {`{{ ${v} }}`}
              </Badge>
            ))}
          </div>
          <Textarea value={template} onChange={(e) => setTemplate(e.target.value)} readOnly={!canEdit} className="min-h-80 font-mono text-xs" />
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1.5">
              <Label htmlFor="prompt-model">Model</Label>
              <Input id="prompt-model" value={model} onChange={(e) => setModel(e.target.value)} readOnly={!canEdit} className="w-64 font-mono text-xs" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="prompt-temperature">Temperature</Label>
              <Input id="prompt-temperature" type="number" min={0} max={2} step={0.1} value={temperature} onChange={(e) => setTemperature(e.target.value)} readOnly={!canEdit} className="w-28" />
            </div>
            {canEdit && (
              <>
                <div className="min-w-48 flex-1 space-y-1.5">
                  <Label htmlFor="prompt-note">Note</Label>
                  <Input id="prompt-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What changed and why" />
                </div>
                <Label className="flex items-center gap-2 pb-2 font-normal">
                  <Checkbox checked={activate} onCheckedChange={(v) => setActivate(v === true)} />
                  Make active
                </Label>
              </>
            )}
          </div>
          {canEdit && (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="prompt-variables">Test variables (optional JSON — blank uses sample values)</Label>
                <Textarea id="prompt-variables" value={variables} onChange={(e) => setVariables(e.target.value)} className="min-h-20 font-mono text-xs" placeholder='{"company": "Acme"}' />
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={test} disabled={isPending("test")}>
                  {isPending("test") ? <Loader2Icon className="animate-spin" /> : <FlaskConicalIcon />} Test
                </Button>
                <Button onClick={save} disabled={isPending("save")}>
                  {isPending("save") ? <Loader2Icon className="animate-spin" /> : <SaveIcon />} Save new version
                </Button>
              </div>
            </>
          )}
          {testResult && (
            <div className="grid gap-3 lg:grid-cols-2">
              <Output title="Rendered prompt" text={testResult.rendered_prompt} />
              <Output title="Model output" text={testResult.output} />
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Versions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {!versions.data ? (
            <Skeleton className="h-24" />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Version</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Note</TableHead>
                  <TableHead>Saved</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {versions.data.map((v) => (
                  <TableRow key={v.id}>
                    <TableCell>
                      v{v.version} {v.is_active && <Badge variant="secondary">active</Badge>}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {v.model} · {v.temperature}
                    </TableCell>
                    <TableCell className="max-w-64 truncate" title={v.note ?? undefined}>
                      {v.note ?? "—"}
                    </TableCell>
                    <TableCell>{fmtDate(v.created_at)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        {canEdit && (
                          <Button size="sm" variant="ghost" onClick={() => load(v)}>
                            Load
                          </Button>
                        )}
                        {!v.is_active && prompt.active && (
                          <Button size="sm" variant="ghost" onClick={() => showDiff(v)} disabled={isPending(`diff:${v.version}`)}>
                            Diff
                          </Button>
                        )}
                        {canEdit && !v.is_active && (
                          <Button size="sm" variant="outline" onClick={() => activateVersion(v)} disabled={isPending(`activate:${v.version}`)}>
                            Activate
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {diff && <Output title={`Diff ${diff.label}`} text={diff.text} diff />}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        title={`Reset ${humanize(prompt.node)} to the default?`}
        description="The built-in prompt becomes a new active version. Older versions stay in the history."
        confirmLabel="Reset"
        onConfirm={async () => {
          const done = await run("reset", () => post(`/prompts/${prompt.node}/reset`), "Reset to the default prompt")
          if (done) onChanged()
        }}
      />
    </div>
  )
}

function Output({ title, text, diff }: { title: string; text: string; diff?: boolean }) {
  return (
    <div className="min-w-0 space-y-1.5">
      <div className="text-sm font-medium">{title}</div>
      <pre className="max-h-96 overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-xs whitespace-pre-wrap">
        {diff
          ? text.split("\n").map((line, i) => (
              <div key={i} className={cn(line.startsWith("+") && "text-emerald-700", line.startsWith("-") && "text-red-700")}>
                {line || " "}
              </div>
            ))
          : text}
      </pre>
    </div>
  )
}
