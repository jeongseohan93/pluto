import type { JSX } from 'react'
import type { TestCase } from '@shared/ide'
import { EmptyState } from '@renderer/components/primitives'
import { DemoBadge } from '@shared/ui/DemoBadge'

/** v0.0.1 mock test results. The real run's output is in the Run panel. */
export function TestSurface({
  tests,
  selectedSymbolName
}: {
  tests: TestCase[] | null
  selectedSymbolName: string | null
}): JSX.Element {
  if (!tests) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-tiny text-fg-mute">Loading tests…</span>
      </div>
    )
  }

  if (tests.length === 0) {
    return (
      <EmptyState title="No test run yet" hint="Tests appear here after the agent runs them." />
    )
  }

  const passed = tests.filter((t) => t.state === 'passed').length
  const failed = tests.filter((t) => t.state === 'failed').length
  const skipped = tests.filter((t) => t.state === 'skipped').length

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-7 shrink-0 items-center gap-3 border-b border-line px-3 font-mono text-micro">
        <DemoBadge />
        <span className="text-ok">{passed} passed</span>
        <span className={failed > 0 ? 'text-bad' : 'text-fg-mute'}>{failed} failed</span>
        <span className="text-fg-mute">{skipped} skipped</span>
        {selectedSymbolName ? (
          <span className="ml-auto truncate text-fg-mute">
            related to <span className="text-fg-dim">{selectedSymbolName}</span>
          </span>
        ) : null}
      </div>

      <ul className="min-h-0 flex-1 overflow-y-auto">
        {tests.map((test) => {
          const related = selectedSymbolName !== null && test.covers.includes(selectedSymbolName)
          return (
            <li
              key={test.id}
              className={`flex items-center gap-3 border-b border-line/60 px-3 py-1.5 ${
                related ? 'bg-accent-soft' : 'hover:bg-hover'
              }`}
            >
              <span
                className={`w-9 shrink-0 font-mono text-micro ${
                  test.state === 'passed'
                    ? 'text-ok'
                    : test.state === 'failed'
                      ? 'text-bad'
                      : 'text-fg-mute'
                }`}
              >
                {test.state === 'passed' ? 'PASS' : test.state === 'failed' ? 'FAIL' : 'SKIP'}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-tiny text-fg-dim" title={test.name}>
                  {test.name}
                </p>
                <p className="truncate font-mono text-micro text-fg-mute" title={test.file}>
                  {test.file} · covers {test.covers.join(', ')}
                </p>
              </div>
              <span className="shrink-0 font-mono text-micro text-fg-mute">
                {test.durationMs}ms
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
