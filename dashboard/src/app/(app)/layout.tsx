import { AppShell } from "@/components/app-shell"

// Everything in this group sits behind sign-in; the (public) group deliberately doesn't.
export default function AppLayout({ children }: LayoutProps<"/">) {
  return <AppShell>{children}</AppShell>
}
