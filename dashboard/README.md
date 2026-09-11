# outreach-io dashboard

Next.js 16 (App Router) + shadcn/ui + TanStack Table. Every page is a client component that calls the
FastAPI backend through `/api/*`; `next.config.ts` rewrites that (and `/auth/*`) to `API_ORIGIN`.

```bash
npm install
npm run dev      # http://localhost:3000 — API_ORIGIN defaults to http://localhost:8000
npm run lint
npm run build    # standalone output, used by the Dockerfile
```

`API_ORIGIN` is read at build time (rewrites are compiled into the build), so the Docker image takes it
as a build arg. Deploy with `bash scripts/deploy_dashboard.sh` from the repo root.

- `src/lib/api.ts`, `use-api.ts`, `use-action.ts` — fetch wrapper, GET hook, mutation + toast hook
- `src/components/app-shell.tsx` — sign-in gate, navigation, mode badge, `useSession().can(permission)`
- `src/components/data-table.tsx` — sortable, searchable table with optional row selection
- `src/app/(app)/<tab>/` — one folder per tab, behind the sign-in gate
- `src/app/(public)/` — `/about` and `/privacy`, reachable without signing in
