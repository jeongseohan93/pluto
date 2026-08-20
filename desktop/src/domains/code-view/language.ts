/**
 * Extension → monaco language id, and the extension whitelist in one function.
 *
 * One function rather than two because they are one decision: a file this app
 * has no highlighter for is also a file it has no business reading. `''` is
 * therefore both "no highlighting" and "refuse" — see `main/source-store.ts`.
 *
 * No `monaco-editor` import here on purpose. Main needs the same answer as the
 * renderer, and main must not learn about the editor to get it.
 */

/** The whole list. The graph only indexes py/ts/tsx/js; the rest is headroom. */
const BY_EXTENSION: Record<string, string> = {
  py: 'python',
  ts: 'typescript',
  tsx: 'typescript',
  js: 'javascript',
  jsx: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  json: 'json',
  md: 'markdown',
  css: 'css',
  html: 'html',
  sh: 'shell',
  yml: 'yaml',
  yaml: 'yaml',
  toml: 'ini',
  txt: 'plaintext'
}

/**
 * The monaco language id for a path, or `''` when this app will not open it.
 *
 * @param path  any path or file name; only what follows the last dot is read
 * @flow  not a string, no dot, or a dot that starts the name (`.gitignore`) ->
 *        '' ; otherwise the last extension, lowercased, looked up
 */
export function monacoLanguage(path: string): string {
  if (typeof path !== 'string') return ''
  // The *last* segment only: a dot in a directory name is not an extension.
  const name = path.split(/[\\/]/).pop() ?? ''
  const dot = name.lastIndexOf('.')
  // `dot < 1` covers both "no dot" and a leading dot, which names the file
  // rather than typing it — `.gitignore` is not a `gitignore` file.
  if (dot < 1) return ''
  return BY_EXTENSION[name.slice(dot + 1).toLowerCase()] ?? ''
}
