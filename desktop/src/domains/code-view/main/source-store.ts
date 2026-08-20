/**
 * Reads one text file out of the open repository. Read-only, and never more.
 *
 * This is the narrowest new capability v0.2.7 adds, and it is narrowed here
 * rather than at the IPC layer for the same reason `graph-store.ts` owns its
 * own failure vocabulary: the caller should not have to remember the rules.
 * Three of them hold:
 *
 *  - the path is relative and stays inside the root (`..`, absolutes, drive
 *    letters and UNC are refused before anything touches the disk),
 *  - the extension is one `language.ts` names, and
 *  - the file is small enough and textual.
 *
 * Nothing here throws. `pipeline.py` is being rewritten by another process
 * while we read it, so missing / locked / half-written are expected, and each
 * one is a sentence on screen rather than a rejected promise.
 */
import { readFileSync, statSync } from 'node:fs'
import { resolve, sep } from 'node:path'
import { monacoLanguage } from '../language'
import type { SourceFile, SourceProblem } from '../types'

/**
 * `aidev/pipeline.py` is ~200KB. 4MiB is well past anything a person reads and
 * well short of what would stall the renderer on the way across IPC.
 */
export const MAX_BYTES = 4 * 1024 * 1024

/** How much of the head is sniffed for a NUL before calling a file text. */
const SNIFF_BYTES = 8192

/**
 * One repo-relative text file, or why not.
 *
 * @param repoRoot  the repository or worktree the app has open
 * @param relPath   the path the renderer asked for, as the graph spells it
 * @flow  not a usable relative path -> unreadable ; escapes the root ->
 *        unreadable ; an extension this app will not open -> unsupported ;
 *        no such file, or a directory -> missing ; past MAX_BYTES -> too-large ;
 *        a NUL in the head -> binary ; otherwise the text, BOM stripped
 * 주요 내부 변수: rel(구분자를 / 로 맞춘 상대 경로), abs(루트 안이라고 확인된 절대 경로)
 */
export function readSource(repoRoot: string, relPath: string): SourceFile {
  const rel = typeof relPath === 'string' ? relPath.replace(/\\/g, '/').trim() : ''
  if (!rel) return fail('', 'unreadable', 'no path given')
  if (escapes(rel)) return fail(rel, 'unreadable', `not a path inside the repository: ${rel}`)

  const lang = monacoLanguage(rel)
  if (!lang) return fail(rel, 'unsupported', `this viewer does not open ${rel}`)

  try {
    const base = resolve(repoRoot)
    const abs = resolve(base, rel)
    // The belt to the brace above: `..` is refused by shape, and this refuses
    // whatever shape did not catch — the same guard `aidev-store.ts` sliceDir
    // uses, and for the same reason.
    if (abs !== base && !abs.startsWith(base + sep)) {
      return fail(rel, 'unreadable', `path escapes ${base}`)
    }

    let stat: ReturnType<typeof statSync>
    try {
      stat = statSync(abs)
    } catch {
      return fail(rel, 'missing', `no such file in this repository: ${rel}`)
    }
    if (!stat.isFile()) return fail(rel, 'missing', `not a file: ${rel}`)
    if (stat.size > MAX_BYTES) {
      return fail(rel, 'too-large', `${rel} is ${stat.size} bytes; the viewer stops at ${MAX_BYTES}`)
    }

    const buf = readFileSync(abs)
    const head = Math.min(buf.length, SNIFF_BYTES)
    for (let i = 0; i < head; i += 1) {
      if (buf[i] === 0) return fail(rel, 'binary', `${rel} is not text`)
    }

    // CRLF is left alone: the line numbers have to be the file's own, and
    // monaco detects the ending itself.
    const raw = buf.toString('utf8')
    const text = raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw
    return { ok: true, path: rel, text, lines: text.split('\n').length, bytes: buf.length, lang }
  } catch (err) {
    return fail(rel, 'unreadable', String(err))
  }
}

/**
 * Is this relative path one that could leave the root, or is not relative?
 *
 * Asked of the spelling, before the filesystem is touched at all: a path that
 * only *resolves* back inside is still one nobody meant to send.
 *
 * @param rel  the path with its separators already normalised to `/`
 * @flow  a leading `/`, a `C:` drive letter or a UNC `//host` -> yes ; any
 *        segment that is exactly `..` -> yes ; otherwise no
 */
function escapes(rel: string): boolean {
  if (rel.startsWith('/') || rel.startsWith('//')) return true
  if (/^[a-zA-Z]:/.test(rel)) return true
  return rel.split('/').some((segment) => segment === '..')
}

/**
 * A refusal in the shape of an answer — the caller awaits a value, never a throw.
 *
 * @param path     what was asked for, so the screen can say where
 * @param problem  which refusal this is
 * @param detail   the sentence under it
 */
function fail(path: string, problem: SourceProblem, detail: string): SourceFile {
  return { ok: false, problem, detail, path, text: '', lines: 0, bytes: 0, lang: '' }
}
