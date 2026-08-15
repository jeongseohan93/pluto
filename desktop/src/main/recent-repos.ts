/**
 * The list of repositories the human has opened.
 *
 * Lives in Electron's `userData`, never inside a target repository: Pluto
 * writes exactly one file into a repo, and this is not it. Electron-free for
 * the same reason as `aidev-store` — it is unit-testable as plain fs.
 */
import { readJsonTolerant, writeTextAtomic } from './aidev-store'

/** Enough to cover the repos one person switches between, and no more. */
export const RECENT_LIMIT = 8

export function readRecents(file: string): string[] {
  const data = readJsonTolerant(file)
  const raw = data && Array.isArray(data.recent) ? data.recent : []
  return dedupe(raw.filter((item): item is string => typeof item === 'string' && item !== ''))
}

/** Most recent first. Returns the new list so the caller need not re-read. */
export function addRecent(file: string, root: string): string[] {
  const recent = dedupe([root, ...readRecents(file)])
  writeTextAtomic(file, `${JSON.stringify({ version: 1, recent }, null, 2)}\n`)
  return recent
}

/** Case-insensitive, because `C:\x` and `c:\x` are one folder on Windows. */
function dedupe(paths: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const path of paths) {
    const key = path.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(path)
    if (out.length >= RECENT_LIMIT) break
  }
  return out
}
