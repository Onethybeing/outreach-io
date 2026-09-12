"use client"

import { MoonIcon, SunIcon } from "lucide-react"
import { useTheme } from "next-themes"
import { useRef } from "react"

import { cn } from "@/lib/utils"

/**
 * A sliding sun/moon switch. Clicking it also sweeps the new theme across the page from the switch
 * itself, using the View Transitions API where the browser supports it. Browsers that don't, and
 * anyone who asked for less motion, just get the instant swap.
 *
 * The switch's own appearance is driven entirely by the `dark:` class on <html>, never by React
 * state, so the server and the first client render always agree.
 */
export function ThemeToggle({ className }: { className?: string }) {
  const { setTheme } = useTheme()
  const button = useRef<HTMLButtonElement>(null)

  function toggle() {
    // Read the class rather than next-themes' resolvedTheme, which is still undefined on the first
    // render: otherwise the very first click on an already-dark system would ask for dark again.
    const next = document.documentElement.classList.contains("dark") ? "light" : "dark"
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const start = (document as Document & { startViewTransition?: (cb: () => void) => ViewTransition })
      .startViewTransition

    if (!start || reduced) {
      setTheme(next)
      return
    }

    const box = button.current?.getBoundingClientRect()
    const x = box ? box.left + box.width / 2 : window.innerWidth / 2
    const y = box ? box.top + box.height / 2 : 0
    // Far enough to reach the corner furthest from the switch.
    const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y))

    const transition = start.call(document, () => setTheme(next))
    transition.ready
      .then(() => {
        document.documentElement.animate(
          { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
          { duration: 550, easing: "cubic-bezier(0.4, 0, 0.2, 1)", pseudoElement: "::view-transition-new(root)" },
        )
      })
      .catch(() => undefined) // a transition the browser skips is not a failure
  }

  return (
    <button
      ref={button}
      type="button"
      onClick={toggle}
      aria-label="Toggle dark mode"
      title="Toggle dark mode"
      className={cn(
        "relative inline-flex h-7 w-12 shrink-0 items-center rounded-full border transition-colors duration-500",
        "border-sky-200 bg-sky-100 dark:border-slate-700 dark:bg-slate-800",
        "focus-visible:ring-ring/50 focus-visible:ring-[3px] focus-visible:outline-none",
        className,
      )}
    >
      <span
        className={cn(
          "absolute flex size-5 translate-x-1 items-center justify-center rounded-full bg-white shadow-sm dark:translate-x-6",
          "transition-transform duration-500 [transition-timing-function:cubic-bezier(0.34,1.56,0.64,1)]",
        )}
      >
        <SunIcon className="absolute size-3 scale-100 rotate-0 text-amber-500 opacity-100 transition-all duration-300 dark:scale-0 dark:rotate-90 dark:opacity-0" />
        <MoonIcon className="absolute size-3 scale-0 -rotate-90 text-slate-700 opacity-0 transition-all duration-300 dark:scale-100 dark:rotate-0 dark:opacity-100" />
      </span>
    </button>
  )
}
