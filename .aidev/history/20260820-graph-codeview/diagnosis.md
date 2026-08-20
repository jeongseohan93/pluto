# DIAGNOSIS
## Cause
`desktop/node_modules` does not exist in this worktree (the task declares no `setup:`), so `npx --prefix desktop tsc` found no local typescript and fetched the unrelated npm package named `tsc`, which prints that banner and exits 1.

## Fix direction
Give the slice a real install step and a real check: in `tasks/graph-codeview.md` front matter add `setup: npm install --prefix desktop` and change `test_commands:` to `npm test --prefix desktop`. Use `npm install`, not `npm ci` — `monaco-editor` was added to `package.json` without touching `package-lock.json`, so `npm ci` dies with EUSAGE (lock out of sync). This fixes the error because tsc then resolves from `desktop/node_modules/.bin`, and `npm test` runs `typecheck:node` + `typecheck:web` + `tsc -p tsconfig.test.json` + `node --test`, which are the configs that actually include `src/domains/**`. Note the old command could never have verified anything anyway: `-p desktop` points at a solution tsconfig with `"files": []` and no `--build`, so it checks zero files. Consequence: the slice's TypeScript gets compiled for the first time — expect genuine errors (monaco subpath imports in `ui/monaco.ts`, `monaco-esm.d.ts`, the new IPC types) and fix them as part of this step rather than reverting to a no-op command.

## Where
- tasks/graph-codeview.md:4 - `test_commands: npx --prefix desktop tsc --noEmit -p desktop` with no `setup:` line; nothing installs typescript, and `-p desktop` would check 0 files even if it did
- desktop/package-lock.json:1 - no `monaco-editor` entry, so `npm ci --prefix desktop` cannot be used as the setup command
- desktop/package.json:26 - `"monaco-editor": "0.52.2"` added to dependencies without a matching lock update
- desktop/tsconfig.json:2 - `"files": []` solution config; only `tsconfig.node.json` / `tsconfig.web.json` / `tsconfig.test.json` actually include the new `src/domains/code-view/**` files
