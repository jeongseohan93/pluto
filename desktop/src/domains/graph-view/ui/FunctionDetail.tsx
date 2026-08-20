import type { JSX } from 'react'
import type { CodeTarget } from '@domains/code-view/types'
import type { GraphNodeDetail } from '@domains/graph-view/types'

/**
 * B2 — one function's whole record: what it does, what it takes, who calls it.
 *
 * Every line is a column of the Function DB shown as it is. `@param` and
 * `@flow` are not re-ordered or re-worded — the spec convention says the tags
 * are written in a fixed order, and showing them in that order is how a reader
 * can tell a complete spec from a partial one at a glance.
 *
 * Callers and calls are buttons: the panel is also the way around the graph.
 * v0.2.7 makes each of them two moves at once — the selection follows the row,
 * and so does the code viewer's aim. They are the same click because they are
 * the same intent: go and look at that.
 *
 * @param detail      the loaded node, or null while it loads
 * @param loading     is a fetch in flight?
 * @param onSelect    move to another node
 * @param onReveal    aim the code viewer at a coordinate, without opening it
 * @param onOpenCode  open the code viewer there — the [코드 보기] button
 */
export function FunctionDetail({
  detail,
  loading,
  onSelect,
  onReveal,
  onOpenCode
}: {
  detail: GraphNodeDetail | null
  loading: boolean
  onSelect: (id: number) => void
  onReveal: (target: CodeTarget) => void
  onOpenCode: (target: CodeTarget) => void
}): JSX.Element {
  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-center">
        <p className="text-tiny text-fg-mute">
          {loading ? 'Reading…' : 'Click a function to see its spec and its relations.'}
        </p>
      </div>
    )
  }

  const { fn } = detail
  const core = detail.tags.filter((tag) => tag.tag === 'param' || tag.tag === 'flow')
  const rest = detail.tags.filter((tag) => tag.tag !== 'param' && tag.tag !== 'flow')

  return (
    <div className="flex h-full min-w-0 flex-col overflow-y-auto">
      <div className="border-b border-line px-2.5 py-2">
        <p className="truncate font-mono text-tiny text-fg" title={fn.qualname}>
          {fn.qualname}
        </p>
        <div className="flex items-center gap-2">
          <p className="min-w-0 flex-1 truncate font-mono text-micro text-fg-mute" title={fn.path}>
            {fn.path}:{fn.lineno}
            {fn.endLineno > fn.lineno ? `-${fn.endLineno}` : ''} · {fn.kind || fn.lang}
            {fn.isTest ? ' · test' : ''}
          </p>
          <button
            type="button"
            onClick={() =>
              onOpenCode({
                path: fn.path,
                line: fn.lineno,
                endLine: fn.endLineno || fn.lineno,
                label: fn.qualname
              })
            }
            title={`Open ${fn.path}:${fn.lineno}`}
            className="shrink-0 rounded-sm border border-line px-1.5 py-0.5 text-micro text-fg-dim hover:bg-hover hover:text-fg"
          >
            코드 보기
          </button>
        </div>
      </div>

      <div className="px-2.5 py-2">
        {fn.summary ? (
          <p className="text-tiny text-fg-dim">{fn.summary}</p>
        ) : (
          <p className="text-tiny text-fg-mute">
            No spec. `aidev` only checks Python, so this may be by design — or it may be the
            next line someone has to write.
          </p>
        )}
        {fn.signature ? (
          <pre className="mt-1.5 overflow-x-auto rounded-sm border border-line bg-app px-2 py-1 font-mono text-micro break-words whitespace-pre-wrap text-fg-dim select-text">
            {fn.signature}
          </pre>
        ) : null}
      </div>

      {core.length > 0 ? (
        <Section label="spec">
          <ul className="px-2.5">
            {core.map((tag, i) => (
              <li key={`${tag.tag}-${i}`} className="flex gap-1.5 py-px font-mono text-micro">
                <span className="shrink-0 text-accent">@{tag.tag}</span>
                <span className="min-w-0 break-words text-fg-dim">{tag.value}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {rest.length > 0 ? (
        <Section label="other tags">
          <ul className="px-2.5">
            {rest.map((tag, i) => (
              <li key={`${tag.tag}-${i}`} className="flex gap-1.5 py-px font-mono text-micro">
                <span className="shrink-0 text-fg-mute">@{tag.tag}</span>
                <span className="min-w-0 break-words text-fg-dim">{tag.value}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section label={`calls (${detail.calls.length})`}>
        {detail.calls.length === 0 ? (
          <p className="px-2.5 text-micro text-fg-mute">nothing this graph resolved</p>
        ) : (
          <ul>
            {detail.calls.map((call, i) => (
              <li key={`${call.callLine}-${call.callee}-${i}`}>
                <Row
                  enabled={call.targetId !== null}
                  onClick={() => {
                    if (call.targetId === null) return
                    onSelect(call.targetId)
                    // The destination of a call is where the callee is
                    // *defined* — that is the coordinate the row prints.
                    if (call.targetPath !== null) {
                      const line = call.targetLine ?? 1
                      onReveal({
                        path: call.targetPath,
                        line,
                        endLine: line,
                        label: call.targetQualname ?? call.callee
                      })
                    }
                  }}
                  title={call.raw || call.callee}
                  name={call.callee}
                  where={
                    call.targetPath
                      ? `${call.targetPath}:${call.targetLine ?? '?'}`
                      : 'unresolved'
                  }
                  line={call.callLine}
                />
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section
        label={`callers (${detail.callers.length}${detail.callersTruncated > 0 ? ` +${detail.callersTruncated}` : ''})`}
      >
        {detail.callers.length === 0 ? (
          <p className="px-2.5 pb-2 text-micro text-fg-mute">nothing calls this</p>
        ) : (
          <ul className="pb-2">
            {detail.callers.map((caller, i) => (
              <li key={`${caller.callerId}-${caller.callLine}-${i}`}>
                <Row
                  enabled
                  onClick={() => {
                    onSelect(caller.callerId)
                    // The call *site*, not the caller's own first line: the row
                    // shows `path:callLine`, so that is where the click lands.
                    // An unresolved row moves too — the engine failed to pick a
                    // target, which does not make the call site less real.
                    onReveal({
                      path: caller.path,
                      line: caller.callLine,
                      endLine: caller.callLine,
                      label: caller.caller
                    })
                  }}
                  title={caller.caller}
                  name={caller.caller.split('.').pop() ?? caller.caller}
                  where={`${caller.path}:${caller.callLine}`}
                  line={caller.callerLine}
                  warn={caller.unresolved}
                />
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  )
}

/**
 * One labelled block of the panel.
 *
 * @param label     the block's heading
 * @param children  its contents
 */
function Section({ label, children }: { label: string; children: JSX.Element }): JSX.Element {
  return (
    <div className="border-t border-line pt-1.5">
      <p className="panel-label px-2.5">{label}</p>
      {children}
    </div>
  )
}

/**
 * One navigable relation row.
 *
 * @param enabled  can it be jumped to? (an unresolved call has no target)
 * @param onClick  jump
 * @param title    the full name, for the tooltip
 * @param name     the short name shown
 * @param where    path:line of the other end
 * @param line     the line this relation sits on
 * @param warn     the engine refused to resolve this edge
 */
function Row({
  enabled,
  onClick,
  title,
  name,
  where,
  line,
  warn = false
}: {
  enabled: boolean
  onClick: () => void
  title: string
  name: string
  where: string
  line: number
  warn?: boolean
}): JSX.Element {
  return (
    <button
      type="button"
      disabled={!enabled}
      onClick={onClick}
      title={`${title} — ${where}`}
      className="flex h-[20px] w-full items-center gap-2 px-2.5 text-left font-mono text-micro hover:bg-hover disabled:opacity-50 disabled:hover:bg-transparent"
    >
      <span className={`min-w-0 flex-1 truncate ${warn ? 'text-warn' : 'text-fg-dim'}`}>{name}</span>
      <span className="min-w-0 shrink truncate text-fg-mute">{where}</span>
      <span className="shrink-0 text-fg-mute">:{line}</span>
    </button>
  )
}
