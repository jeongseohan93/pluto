import type { JSX } from 'react'

/**
 * `[DEMO]` — this panel is authored mock data, not your repository.
 *
 * Measured 2026-08-19: three separate occasions of reading v0.0.1's mock graph,
 * diff or telemetry as if it were the open repo. The mock is worth keeping (it
 * settles the visual language for surfaces that are not wired yet), but only if
 * it cannot be mistaken for the truth — so every surface that serves it wears
 * this, and the ones that read real files never do.
 *
 * @param className  extra positioning classes for the corner it sits in
 */
export function DemoBadge({ className = '' }: { className?: string }): JSX.Element {
  return (
    <span
      title="Authored mock data — not read from the open repository"
      aria-label="demo data"
      className={`shrink-0 rounded-sm border border-warn/40 bg-warn/10 px-1 font-mono text-micro tracking-wide text-warn ${className}`}
    >
      DEMO
    </span>
  )
}
