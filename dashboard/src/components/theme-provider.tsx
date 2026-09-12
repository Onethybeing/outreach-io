"use client"

import { ThemeProvider as NextThemes } from "next-themes"

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    // No disableTransitionOnChange: it stops every CSS transition during the swap, which would
    // freeze the toggle's own knob animation. The page sweep is handled by the view transition.
    <NextThemes attribute="class" defaultTheme="system" enableSystem>
      {children}
    </NextThemes>
  )
}
