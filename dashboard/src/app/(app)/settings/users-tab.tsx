"use client"

import type { ColumnDef } from "@tanstack/react-table"
import { Loader2Icon, PlusIcon } from "lucide-react"
import { useMemo, useState } from "react"

import { useSession } from "@/components/app-shell"
import { DataTable } from "@/components/data-table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { patch, post } from "@/lib/api"
import { fmtDate } from "@/lib/format"
import type { Role, User } from "@/lib/types"
import { useAction } from "@/lib/use-action"
import { useApi } from "@/lib/use-api"

const ROLES: { value: Role; hint: string }[] = [
  { value: "viewer", hint: "sees everything, changes nothing" },
  { value: "operator", hint: "runs the agent, approves and sends" },
  { value: "admin", hint: "also keys, prompts, users and audit" },
]

export function UsersTab() {
  const { me } = useSession()
  const users = useApi<User[]>("/users")
  const [email, setEmail] = useState("")
  const [name, setName] = useState("")
  const [role, setRole] = useState<Role>("viewer")
  const { run, isPending } = useAction()
  const { mutate } = users

  async function add(event: React.FormEvent) {
    event.preventDefault()
    const created = await run("add", () => post<User>("/users", { email, name: name || null, role }), (u) => `Added ${u.email}. They can sign in with Google now`)
    if (created) {
      setEmail("")
      setName("")
      users.reload()
    }
  }

  const columns = useMemo<ColumnDef<User>[]>(() => {
    async function update(user: User, changes: Partial<Pick<User, "role" | "is_active">>) {
      const saved = await run(`update:${user.id}`, () => patch<User>(`/users/${user.id}`, changes), "Saved")
      if (saved) mutate((list) => list.map((u) => (u.id === saved.id ? saved : u)))
    }

    return [
      {
        accessorKey: "email",
        header: "Email",
        cell: ({ row: { original: u } }) => (
          <span className="font-medium">
            {u.email} {u.id === me.id && <Badge variant="secondary">you</Badge>}
          </span>
        ),
      },
      { accessorKey: "name", header: "Name", cell: ({ row }) => row.original.name ?? "-" },
      {
        accessorKey: "role",
        header: "Role",
        cell: ({ row: { original: u } }) => (
          <Select value={u.role} onValueChange={(value) => update(u, { role: value as Role })} disabled={isPending(`update:${u.id}`)}>
            <SelectTrigger className="w-32" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ROLES.map((r) => (
                <SelectItem key={r.value} value={r.value} className="capitalize">
                  {r.value}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ),
      },
      { accessorKey: "last_login_at", header: "Last sign-in", cell: ({ row }) => fmtDate(row.original.last_login_at) },
      {
        accessorKey: "is_active",
        header: "Access",
        cell: ({ row: { original: u } }) => (
          <Button size="sm" variant={u.is_active ? "ghost" : "outline"} onClick={() => update(u, { is_active: !u.is_active })} disabled={isPending(`update:${u.id}`)}>
            {u.is_active ? "Deactivate" : "Reactivate"}
          </Button>
        ),
      },
    ]
  }, [me.id, run, isPending, mutate])

  return (
    <div className="space-y-4">
      <Card>
        <CardContent>
          <form onSubmit={add} className="flex flex-wrap items-center gap-2">
            <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Google account email" className="w-64" />
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (optional)" className="w-48" />
            <Select value={role} onValueChange={(value) => setRole(value as Role)}>
              <SelectTrigger className="w-72">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLES.map((r) => (
                  <SelectItem key={r.value} value={r.value}>
                    <span className="capitalize">{r.value}</span> <span className="text-muted-foreground">({r.hint})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button type="submit" disabled={isPending("add")}>
              {isPending("add") ? <Loader2Icon className="animate-spin" /> : <PlusIcon />} Add user
            </Button>
          </form>
        </CardContent>
      </Card>
      <DataTable columns={columns} data={users.data} loading={users.loading} error={users.error?.message} getRowId={(u) => u.id} search="Search users" />
    </div>
  )
}
