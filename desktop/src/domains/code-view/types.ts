/**
 * The code viewer's whole vocabulary: a coordinate to go to, and a file read.
 *
 * "거주지는 그래프, 에디터는 소환" — the graph is where a reader lives and the
 * editor is summoned to a place in it. So the viewer never takes a *file*; it
 * takes a `CodeTarget`, which is a file plus the reason for being there.
 *
 * Nothing here is derived. `path`/`line`/`endLine` are `functions.path`,
 * `functions.lineno` and `functions.end_lineno` of the Function DB, or a
 * `calls.lineno` call site — the same coordinates the side panel already prints.
 */

/** Where the viewer is aimed. `path` picks the file, the rest a place in it. */
export interface CodeTarget {
  /** Repo-relative, spelled exactly as the graph DB spells it (POSIX `/`). */
  path: string
  /** The line the scroll stops at. 1-based, as the DB counts. */
  line: number
  /** The end of the highlighted range. Equal to `line` highlights one line. */
  endLine: number
  /** What we came to look at, for the header and the tooltip. */
  label: string
}

/** Why a file could not be handed over. Each is one sentence on screen. */
export type SourceProblem =
  | 'no-repo'
  | 'not-in-graph'
  | 'unsupported'
  | 'missing'
  | 'too-large'
  | 'binary'
  | 'unreadable'

/** One text file, or the reason there is none. */
export interface SourceFile {
  ok: boolean
  problem?: SourceProblem
  detail?: string
  /** The relative path as asked for. Filled even on failure — "where?" is the
   *  answer a reader needs when nothing arrived. */
  path: string
  text: string
  lines: number
  bytes: number
  /** A monaco language id, empty when the read failed. */
  lang: string
}

/**
 * The code half of the renderer's capability surface. Reading, and only that.
 *
 * There is deliberately no write, no list and no glob: the renderer can ask for
 * one path, and main decides whether that path is one this repository's graph
 * knows about.
 */
export interface CodeViewBridge {
  getSourceFile(path: string): Promise<SourceFile>
}
