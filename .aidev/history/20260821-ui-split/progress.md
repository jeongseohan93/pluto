# PROGRESS - 20260821-ui-split · diagnose
2026-08-21T01:05:11+09:00

attempt 1   turn 20/20   (turn-budget 90%)

## Done
- (no file was edited)

## Remaining
Named by the plan and not edited yet:
- tasks/ui-split.md
- .package-lock.json
- desktop/tsconfig.json
- SurfacePane.tsx
- App.tsx
- FunctionGraphSurface.tsx
- CodeViewer.tsx
- monaco.ts
- aidev/specs.py
- tests/test_graph.py
- tsconfig.test.json
- desktop/src/domains/code-view/split.ts
- desktop/src/domains/code-view/split.test.ts
- desktop/src/renderer/src/features/workspace/SurfacePane.tsx
- desktop/src/renderer/src/App.tsx
- ... 16 more

## Last words

# DIAGNOSIS
## Cause
`desktop/node_modules` does not exist in this worktree, so `npx` could not find a local `tsc`, fetched the unrelated decoy `tsc` package from the registry, and it exited 1 — nothing in the slice's diff was ever compiled.

## Fix direction
Make the declared command resolvable before it runs: install the desktop dependencies (`package-lock.json` is present, `node_modules/` is gitignored so an install cannot dirty the commit), and declare that install in the task front matter as `setup: npm ci --prefix desktop` so it is not a one-off — the engine runs `setup:` only from the front matter, never on its own. In this repair attempt the implement stage's only granted Bash shapes are `Bash(npx --prefix desktop tsc --noEmit -p desktop)` and `Bash(npx --prefix:*)`, so an install has to be invoked in that `npx --prefix …` shape to be permitted at all. Separately, the command is vacuous even when it works: `-p desktop` resolves to a solution tsconfig with `"files": []`, so it checks zero files and would pass no matter what the slice wrote — point `test_commands` at `npm --prefix desktop run typecheck` (node + web projects) so the tab/split changes are actually type-checked....

## Resume

    aidev pipeline --repo C:\Users\minsa\ai-dev-orchestrator --resume-slice 20260821-ui-split
