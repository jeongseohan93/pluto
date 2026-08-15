import type { JSX } from 'react'
import { EmptyState } from '@renderer/components/primitives'

export function BrowserSurface(): JSX.Element {
  return (
    <EmptyState
      title="Browser verification not connected"
      hint="Reserved for the verification step that runs after tests pass. Not implemented in v0.0.1."
    />
  )
}
