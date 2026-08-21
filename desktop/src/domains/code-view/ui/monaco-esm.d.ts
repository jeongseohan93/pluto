/**
 * The monaco entry points `monaco.ts` imports, declared so TypeScript can see them.
 *
 * `monaco-editor` ships one declaration file for the package root and plain
 * JavaScript under `esm/`. Those ESM sub-paths are what lets this app take the
 * editor without the language services — see `monaco.ts` on `file://` and web
 * workers — so they are imported anyway, and named here rather than silenced
 * with a `@ts-expect-error` that would itself break the day monaco ships types.
 *
 * Shorthand declarations on purpose: the real surface is reached through
 * `import type * as MonacoNs from 'monaco-editor'`, which is typed properly.
 * These five only need to exist.
 */
declare module 'monaco-editor/esm/vs/editor/edcore.main'
declare module 'monaco-editor/esm/vs/editor/editor.api'
declare module 'monaco-editor/esm/vs/basic-languages/python/python.contribution'
declare module 'monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution'
declare module 'monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution'
declare module 'monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution'
