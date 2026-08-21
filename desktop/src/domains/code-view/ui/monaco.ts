/**
 * The one file that knows monaco exists. Loaded on demand, and allowed to fail.
 *
 * Same contract as `graph-store.ts`'s `openReadonly`: a dependency that may not
 * be reachable is asked about behind one function, so the answer can be "no"
 * and the screen can still draw something. `CodeViewer` falls back to plain
 * numbered lines when this returns null, which means the requirement's three
 * verbs — load the file, scroll to the line, highlight the range — hold either
 * way.
 *
 * **No web workers.** The production window is `loadFile`, so its origin is
 * `file://`, and Chromium refuses `new Worker` there; `index.html`'s CSP has no
 * `blob:` either, so monaco's default worker URL is out too. A read-only viewer
 * needs neither: Monarch highlighting and the find widget both run on the main
 * thread. Hence `edcore.main` (editor + the built-in contributions) rather than
 * `editor.main` (which drags in the ts/json/css/html language services), and
 * hence every worker-backed option being off in `EDITOR_OPTIONS`.
 *
 * It is also why the load is `await import`: a reader who only ever looks at
 * the graph never downloads the editor chunk. 에디터는 소환이다.
 */
import type * as MonacoNs from 'monaco-editor'

export type MonacoApi = typeof MonacoNs

/** Our palette under a name, so the editor and the app are one surface. */
export const THEME = 'pluto-dark'

/** `theme.css` @theme, transcribed. Nothing here is a colour of monaco's own. */
const COLOURS: Record<string, string> = {
  'editor.background': '#0d0e10',
  'editor.foreground': '#9ea3ab',
  'editorLineNumber.foreground': '#676c74',
  'editorLineNumber.activeForeground': '#e3e5e8',
  'editor.selectionBackground': '#1c2836',
  'editor.lineHighlightBackground': '#131417',
  'editorGutter.background': '#0d0e10',
  'editorWidget.background': '#131417',
  'editorWidget.border': '#232529',
  'input.background': '#0d0e10',
  focusBorder: '#4a7fc7',
  'scrollbarSlider.background': '#2b2e3580'
}

/**
 * Read-only, worker-free, and sized like the rest of the panels.
 *
 * The second half of this object is not styling: every option turned off there
 * is one that would reach for an editor worker, which this window cannot make.
 * `find` stays on — it is `model.findMatches` on the main thread, and the
 * requirement points at it instead of a search box of our own.
 */
export const EDITOR_OPTIONS: MonacoNs.editor.IStandaloneEditorConstructionOptions = {
  readOnly: true,
  domReadOnly: true,
  automaticLayout: true,
  minimap: { enabled: false },
  fontFamily: "'Cascadia Mono', Consolas, ui-monospace, 'SFMono-Regular', monospace",
  fontSize: 12,
  lineHeight: 18,
  scrollBeyondLastLine: false,
  renderLineHighlight: 'none',
  smoothScrolling: false,
  contextmenu: false,
  // --- worker-backed, all of it. A viewer needs none of it.
  links: false,
  unicodeHighlight: {
    ambiguousCharacters: false,
    invisibleCharacters: false,
    nonBasicASCII: false
  },
  wordBasedSuggestions: 'off',
  quickSuggestions: false,
  suggestOnTriggerCharacters: false,
  occurrencesHighlight: 'off',
  codeLens: false,
  hover: { enabled: false },
  renderValidationDecorations: 'off',
  find: { addExtraSpaceOnTop: false, seedSearchStringFromSelection: 'never' }
}

/**
 * The same window and the same constraints — no workers — with one difference:
 * a human can type into it.
 *
 * Derived rather than edited in place. `EDITOR_OPTIONS` is one object shared by
 * every read-only viewer, and turning `readOnly` off there would turn it off
 * for all of them. Everything worker-backed stays off as inherited: this is a
 * text box for prose, not a language service.
 */
export const EDIT_OPTIONS: MonacoNs.editor.IStandaloneEditorConstructionOptions = {
  ...EDITOR_OPTIONS,
  readOnly: false,
  domReadOnly: false,
  // A requirement is prose. It has no columns to keep.
  wordWrap: 'on',
  renderLineHighlight: 'line',
  // The paste menu. It runs in the renderer and asks no worker anything.
  contextmenu: true
}

/** The load, kept so a second viewer does not import the editor twice. */
let pending: Promise<MonacoApi | null> | null = null

/**
 * Monaco, once per session — or null where it could not be loaded at all.
 *
 * @flow  already asked -> the same promise ; otherwise import the editor core,
 *        the four highlighters, register the theme, and hand the api back ;
 *        anything throwing on that path -> null, and the caller draws plain text
 */
export function ensureMonaco(): Promise<MonacoApi | null> {
  if (!pending) pending = load()
  return pending
}

/**
 * The import itself, in the order monaco requires: core, languages, then api.
 *
 * Takes no arguments.
 *
 * @flow  the core and the api inside one try — a missing sub-path there is a
 *        fallback, not a blank panel ; each grammar inside its own, so one
 *        missing colour scheme cannot cost the whole editor
 */
async function load(): Promise<MonacoApi | null> {
  try {
    // Not `editor.main`: that entry point pulls in the ts/json/css/html
    // language services, and those want workers this window cannot create.
    await import('monaco-editor/esm/vs/editor/edcore.main')
    // Monarch grammars only. Colouring, no analysis, no worker.
    await tryLanguage(() => import('monaco-editor/esm/vs/basic-languages/python/python.contribution'))
    await tryLanguage(() =>
      import('monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution')
    )
    await tryLanguage(() =>
      import('monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution')
    )
    // The requirement editor's language, and the only one nothing else needed.
    await tryLanguage(() =>
      import('monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution')
    )
    const api = (await import('monaco-editor/esm/vs/editor/editor.api')) as unknown as MonacoApi
    api.editor.defineTheme(THEME, { base: 'vs-dark', inherit: true, rules: [], colors: COLOURS })
    return api
  } catch {
    return null
  }
}

/**
 * One Monarch grammar, allowed to be absent.
 *
 * Separately wrapped because the alternative is what this file used to do: a
 * single try around the whole load, where one grammar sub-path that a future
 * monaco moved or dropped takes the entire editor down to the plain-text
 * fallback. A missing grammar should cost that language its colours and
 * nothing else.
 *
 * @param load  the dynamic import for one `*.contribution` module
 */
async function tryLanguage(load: () => Promise<unknown>): Promise<void> {
  try {
    await load()
  } catch {
    // This language has no colours in this build. That is all it means.
  }
}
