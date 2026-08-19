import type { JSX } from 'react'
import { EmptyState } from '@renderer/components/primitives'
import { DemoBadge } from '@shared/ui/DemoBadge'

/** A placeholder, not a disconnected feature — the badge says which. */
export function BrowserSurface(): JSX.Element {
  return (
    <EmptyState
      title="Browser verification not connected"
      hint="Reserved for the verification step that runs after tests pass. Not implemented in v0.0.1."
    >
      <DemoBadge className="mt-2" />
    </EmptyState>
  )
}
