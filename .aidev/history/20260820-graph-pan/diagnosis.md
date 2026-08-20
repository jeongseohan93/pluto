# DIAGNOSIS
## Cause
The slice worktree has no `desktop/node_modules` (git only checks out tracked files), so `npx` found no local `tsc`, fetched the registry decoy package named `tsc`, and it exited 1 — no TypeScript ever ran, and the implemented code is untouched by this failure.

## Fix direction
Declare the install in the task front matter — add `setup: npm ci --prefix desktop` to `tasks/graph-pan.md` (the pipeline's own mechanism: `resolve_setup`/`run_setup` run it once per slice before the stage and auto-grant `Bash(npm ci:*)`; `desktop/.gitignore` keeps `node_modules` out of the commit).
Also change `test_commands` to `npm run typecheck --prefix desktop`: `npm run` resolves `tsc` from `desktop/node_modules/.bin` with cwd inside `desktop/`, so it depends on neither npx's registry fallback nor on `-p desktop` resolving relative to whatever cwd `npm exec --prefix` picks.
That second change also makes the gate real — `desktop/tsconfig.json` is a solution file (`"files": []` + references), so `tsc --noEmit -p desktop` without `-b` type-checks zero files even when it succeeds; `typecheck:node` + `typecheck:web` are what actually cover `src/domains/**` and the `.tsx`.
Expect the first honest run to surface any genuine type errors in the new `layout.ts` / `FunctionGraphSurface.tsx` code for the first time — fix those in place; nothing in the pan implementation needs redesign for this failure.

## Where
- `tasks/graph-pan.md`:4 - `test_commands: npx --prefix desktop tsc --noEmit -p desktop`, with no `setup:` line above it, so verify runs before any dependency exists
- `README.md`:247 - states the measured rule this violates: a worktree carries no `node_modules`, `npm ci` must be run explicitly
- `desktop/tsconfig.json`:2 - `"files": []` + `references`; the declared command checks nothing even when it passes (needs `-b`, or the `typecheck` script)
- `aidev/pipeline.py`:3199 - `run_setup`, the stage hook that would install deps once the front matter declares it (`resolve_setup` at :304 parses it; no shell operators allowed)
