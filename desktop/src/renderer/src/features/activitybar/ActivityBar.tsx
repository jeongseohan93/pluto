import type { JSX } from 'react'
import { Icon, type IconName } from '@renderer/components/Icon'

export type ActivityId = 'pipeline' | 'workspaces' | 'graph' | 'changes' | 'tests' | 'telemetry'

const ITEMS: { id: ActivityId; icon: IconName; label: string }[] = [
  { id: 'pipeline', icon: 'pipeline', label: 'Pipeline' },
  { id: 'workspaces', icon: 'workspaces', label: 'Workspaces' },
  { id: 'graph', icon: 'graph', label: 'Code graph' },
  { id: 'changes', icon: 'changes', label: 'Changes' },
  { id: 'tests', icon: 'tests', label: 'Tests' },
  { id: 'telemetry', icon: 'telemetry', label: 'Telemetry' }
]

export function ActivityBar({
  value,
  onChange
}: {
  value: ActivityId
  onChange: (id: ActivityId) => void
}): JSX.Element {
  return (
    <nav
      aria-label="Activity"
      className="flex w-11 shrink-0 flex-col items-center border-r border-line bg-panel py-1"
    >
      {ITEMS.map((item) => {
        const selected = item.id === value
        return (
          <button
            key={item.id}
            type="button"
            title={item.label}
            aria-label={item.label}
            aria-current={selected}
            onClick={() => onChange(item.id)}
            className={`relative flex h-10 w-full items-center justify-center transition-colors ${
              selected ? 'text-fg' : 'text-fg-mute hover:text-fg-dim'
            }`}
          >
            {selected ? (
              <span className="absolute left-0 h-5 w-[2px] bg-accent" aria-hidden="true" />
            ) : null}
            <Icon name={item.icon} size={18} />
          </button>
        )
      })}

      <button
        type="button"
        title="Settings"
        aria-label="Settings"
        className="mt-auto flex h-10 w-full items-center justify-center text-fg-mute hover:text-fg-dim"
      >
        <Icon name="settings" size={17} />
      </button>
    </nav>
  )
}
