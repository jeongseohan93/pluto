/**
 * Wiring: which IPC channel reaches which domain function.
 *
 * Nothing here decides anything. It exists so the three things that *are*
 * decisions — building an argv (`domains/pipeline/commands.ts`), reading the
 * Function DB (`domains/graph-view/main/graph-store.ts`) and reading a source
 * file (`domains/code-view/main/source-store.ts`) — have exactly one caller
 * each, and so `main/index.ts` stays what it is: the window and the app
 * lifecycle.
 *
 * Two rules hold for every handler below:
 *  - **nothing throws.** A renderer awaiting a poll must get an answer with a
 *    reason in it, never a rejected promise that leaves a panel blank.
 *  - **the renderer's strings are checked twice.** `commandArgv` refuses
 *    anything unsafe by shape; this layer additionally refuses anything that
 *    does not exist in *this* repository, so a well-formed id for someone
 *    else's slice cannot start a process.
 */
import { ipcMain } from 'electron'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { IPC } from '../../shared/ide'
import { slicesRoot } from '../../main/aidev-store'
import { isRequirementPath, isSafeSegment } from '../../domains/pipeline/commands'
import type { CommandRunner } from '../../domains/pipeline/main/cli-runner'
import { listRequirements, readSliceFailure } from '../../domains/pipeline/main/store'
import type { CommandRequest, CommandStart } from '../../domains/pipeline/types'
import {
  pathInGraph,
  readGraphIndex,
  readGraphNode,
  readGraphTrace
} from '../../domains/graph-view/main/graph-store'
import { clampTraceDepth } from '../../domains/graph-view/types'
import type { GraphIndexResult, GraphTrace, TraceDirection } from '../../domains/graph-view/types'
import { readSource } from '../../domains/code-view/main/source-store'
import type { SourceFile, SourceProblem } from '../../domains/code-view/types'

export interface SliceHandlerContext {
  /** The repository main currently has open, or null. Read fresh every call. */
  repoRoot(): string | null
  runner: CommandRunner
}

/**
 * Register the v0.2.6 channels: launching, watching, finishing, and the graph —
 * plus v0.2.7's one source read and v0.2.8's one call chain.
 *
 * @param ctx  the open repository and the single command slot
 * @flow  one `ipcMain.handle` per channel, each guarded and each answering with
 *        a value rather than an exception
 */
export function registerSliceHandlers(ctx: SliceHandlerContext): void {
  ipcMain.handle(IPC.requirements, () => {
    const root = ctx.repoRoot()
    if (!root) return []
    try {
      return listRequirements(root)
    } catch {
      return []
    }
  })

  ipcMain.handle(IPC.runCommand, (_event, request: CommandRequest): CommandStart => {
    const root = ctx.repoRoot()
    if (!root) return { ok: false, error: 'no repository selected', state: ctx.runner.state() }
    const refusal = refuse(root, request)
    if (refusal) return { ok: false, error: refusal, state: ctx.runner.state() }
    try {
      return ctx.runner.start(root, request)
    } catch (err) {
      return { ok: false, error: String(err), state: ctx.runner.state() }
    }
  })

  ipcMain.handle(IPC.stopCommand, () => {
    try {
      ctx.runner.stop()
    } catch {
      // the process is already gone; the state below says so
    }
    return ctx.runner.state()
  })

  ipcMain.handle(IPC.commandState, () => ctx.runner.state())

  ipcMain.handle(IPC.sliceFailure, (_event, sliceId: string) => {
    const root = ctx.repoRoot()
    if (!root || !isSafeSegment(String(sliceId))) return null
    try {
      return readSliceFailure(root, sliceId)
    } catch {
      return null
    }
  })

  ipcMain.handle(IPC.graphIndex, (): GraphIndexResult => {
    const root = ctx.repoRoot()
    if (!root) {
      return {
        ok: false,
        problem: 'no-graph',
        detail: 'no repository selected',
        dbPath: '',
        meta: null,
        files: [],
        fileEdges: [],
        edges: []
      }
    }
    try {
      return readGraphIndex(root)
    } catch (err) {
      return {
        ok: false,
        problem: 'unreadable',
        detail: String(err),
        dbPath: '',
        meta: null,
        files: [],
        fileEdges: [],
        edges: []
      }
    }
  })

  ipcMain.handle(IPC.graphNode, (_event, id: number) => {
    const root = ctx.repoRoot()
    if (!root) return null
    try {
      return readGraphNode(root, Number(id))
    } catch {
      return null
    }
  })

  ipcMain.handle(IPC.graphTrace, (_event, request: unknown): GraphTrace | null => {
    const root = ctx.repoRoot()
    if (!root) return null
    const ask = (request ?? {}) as { id?: unknown; direction?: unknown; depth?: unknown }
    // The renderer's strings are checked twice, and a direction is a string: one
    // of exactly two values reaches the query, never whatever arrived.
    const direction: TraceDirection = ask.direction === 'callers' ? 'callers' : 'calls'
    try {
      return readGraphTrace(root, Number(ask.id), direction, clampTraceDepth(Number(ask.depth)))
    } catch {
      return null
    }
  })

  ipcMain.handle(IPC.sourceFile, (_event, path: string): SourceFile => {
    const rel = typeof path === 'string' ? path : ''
    const root = ctx.repoRoot()
    if (!root) return noSource(rel, 'no-repo', 'no repository selected')
    try {
      // The second check, and the reason this channel is not a file read: being
      // a path inside the root is not being a path this repository's graph put
      // on the screen. `readSource` then decides the rest.
      if (!pathInGraph(root, rel)) {
        return noSource(rel, 'not-in-graph', `this graph does not know ${rel}`)
      }
      return readSource(root, rel)
    } catch (err) {
      return noSource(rel, 'unreadable', String(err))
    }
  })
}

/**
 * A source refusal shaped like an answer, for the two this layer decides itself.
 *
 * @param path     what the renderer asked for
 * @param problem  which refusal it is
 * @param detail   the sentence the viewer shows under it
 */
function noSource(path: string, problem: SourceProblem, detail: string): SourceFile {
  return { ok: false, problem, detail, path, text: '', lines: 0, bytes: 0, lang: '' }
}

/**
 * Why this repository may not run this request, or null when it may.
 *
 * The existence checks are the point: shape alone would let a renderer start a
 * pipeline for a `tasks/*.md` that is not in the list it was shown.
 *
 * @param root     the open repository
 * @param request  what the renderer asked for
 * @flow  a requirement must be one this repo actually offers ; a slice id must
 *        be an existing slice directory ; everything else needs no target
 */
function refuse(root: string, request: CommandRequest): string | null {
  if (!request || typeof request !== 'object') return 'no command given'

  if (request.kind === 'pipeline') {
    if (!isRequirementPath(request.requirement)) return `not a requirement: ${request.requirement}`
    const known = listRequirements(root).some((file) => file.path === request.requirement)
    return known ? null : `no such requirement in this repository: ${request.requirement}`
  }

  if (request.kind === 'resume' || request.kind === 'merge' || request.kind === 'discard') {
    if (!isSafeSegment(request.sliceId)) return `not a slice id: ${request.sliceId}`
    return existsSync(join(slicesRoot(root), request.sliceId))
      ? null
      : `no such slice in this repository: ${request.sliceId}`
  }

  return null
}
