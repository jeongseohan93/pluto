# DIAGNOSIS
## Cause
`desktop/node_modules` does not exist in this worktree, so `npx --prefix desktop tsc` fell through to the npm registry and ran the decoy package literally named `tsc` (it prints that banner and exits 1) — no TypeScript ever ran.

## Fix direction
Install the deps and use the project's own script instead of a bare `npx`: `npm --prefix desktop run typecheck` (package.json:13 → node + web configs). `tasks/req-editor.md` is the only requirement in `tasks/` with no `setup:` line — add `setup: npm ci --prefix desktop` and change `test_commands` to `npm --prefix desktop run typecheck`, matching `ui-split.md:4-5`; a resume re-reads the copied `requirement.md` (pipeline.py:5652), so the change takes effect for the next attempt.
For this attempt the granted Bash rules are only `Bash(npx --prefix desktop tsc --noEmit -p desktop)` and `Bash(npx --prefix:*)` (pipeline.py:2376), so `npx --prefix desktop npm ci` is the one permitted way to install; if that is refused, fix the front matter and the code types by hand and say plainly in progress/summary that TypeScript was not executed — do not claim a pass.
Note the declared target is also wrong on its own: `-p desktop` resolves `desktop/tsconfig.json`, a solution file with `"files": []` and only references, which without `--build` compiles nothing and would exit 0 vacuously even with node_modules present.

## Where
- tasks/req-editor.md:4 - `test_commands: npx --prefix desktop tsc --noEmit -p desktop`; no `setup:` line above it, so nothing ever installs and `npx` fetches the decoy `tsc` package
- desktop/tsconfig.json:2 - `"files": []` + references only; `tsc --noEmit -p desktop` checks zero files, so this command cannot verify the slice even after install
- desktop/package.json:13 - `typecheck` = node + web configs with `--composite false`; this is the command that actually type-checks the new pipeline/preload/ui code
- tasks/ui-split.md:4 - the working precedent (`setup: npm ci --prefix desktop` + `test_commands: npm --prefix desktop run typecheck`) this requirement should copy
