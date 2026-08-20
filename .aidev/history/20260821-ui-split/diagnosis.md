# DIAGNOSIS
## Cause
`desktop/node_modules` does not exist in this worktree, so `npx` could not find a local `tsc`, fetched the unrelated decoy `tsc` package from the registry, and it exited 1 — nothing in the slice's diff was ever compiled.

## Fix direction
Make the declared command resolvable before it runs: install the desktop dependencies (`package-lock.json` is present, `node_modules/` is gitignored so an install cannot dirty the commit), and declare that install in the task front matter as `setup: npm ci --prefix desktop` so it is not a one-off — the engine runs `setup:` only from the front matter, never on its own. In this repair attempt the implement stage's only granted Bash shapes are `Bash(npx --prefix desktop tsc --noEmit -p desktop)` and `Bash(npx --prefix:*)`, so an install has to be invoked in that `npx --prefix …` shape to be permitted at all. Separately, the command is vacuous even when it works: `-p desktop` resolves to a solution tsconfig with `"files": []`, so it checks zero files and would pass no matter what the slice wrote — point `test_commands` at `npm --prefix desktop run typecheck` (node + web projects) so the tab/split changes are actually type-checked. The implemented code is not implicated by this failure; it has never been compiled, so expect real type errors on the first genuine run.

## Where
- `tasks/ui-split.md`:4 - `test_commands: npx --prefix desktop tsc --noEmit -p desktop` assumes `desktop/node_modules`, and no `setup:` line exists to create it (`aidev/pipeline.py`:3199 runs a setup command only when the front matter declares one).
- `desktop/tsconfig.json`:2 - `"files": []` with `references` means `-p desktop` type-checks nothing; the non-empty `references` also suppresses TS18003, so it exits 0 silently.
- `desktop/package.json`:13 - `typecheck` = `typecheck:node && typecheck:web` is the command that actually compiles `src/`; use it instead of `-p desktop`.
- `aidev/pipeline.py`:2376 - with `test_commands` declared, the repair session's Bash allowlist is derived only from that command (`command_tool_rules`, line 719), which is why a bare `npm ci` would be denied.
- `desktop/src/domains/code-view/split.ts`:1 and `desktop/src/renderer/src/App.tsx` / `SurfacePane.tsx` / `FunctionGraphSurface.tsx` - implemented but never type-checked; the narrowed `SurfaceId` and the three new `FunctionGraphSurface` props are the first places to expect genuine errors once tsc really runs.
