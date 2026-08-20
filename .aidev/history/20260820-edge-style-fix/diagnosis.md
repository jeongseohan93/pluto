# DIAGNOSIS
## Cause
This worktree has no `desktop/node_modules` (measured: no `desktop/node_modules/.bin/tsc`), so `npx` found no local compiler, downloaded the registry decoy package named `tsc`, and it exited 1 — TypeScript never ran and the implemented code (which is complete and self-consistent) was never checked.

## Fix direction
Install the dependencies from inside the fix session, then re-run: `aidev verify --command "npm ci --prefix desktop"` (`desktop/package-lock.json` exists; the repair stage's only Bash grant is `Bash(aidev verify:*)` per `DIET_TOOLS`, so a bare `npm ci` will be denied). With `desktop/node_modules/.bin/tsc` present, `npx --prefix desktop` resolves the local binary instead of the registry and the declared command exits 0.
That declared command still checks nothing — `desktop/tsconfig.json` is a solution file (`"files": []` + references), so `-p desktop` without `-b` compiles zero files — so also run `aidev verify --command "npm run typecheck --prefix desktop"` and `... "npm test --prefix desktop"`, which are the first runs that will ever type-check the new `layout.ts`/`FunctionGraphSurface.tsx` and execute the new `layout.test.ts`; fix any genuine type errors they surface in place.
The `setup: npm ci --prefix desktop` line implement already added is correct but cannot help this attempt: front matter is resolved once per launch, so it only takes effect on the next launch/resume. Leave it; do not touch the product code for this failure, and report `python -m pytest -q` results as-is (pre-existing failures are not this slice's).

## Where
- `tasks/edge-style-fix.md`:5 - `test_commands: npx --prefix desktop tsc --noEmit -p desktop` — runs before any install exists, and is a hollow gate even when it passes
- `tasks/edge-style-fix.md`:4 - `setup: npm ci --prefix desktop` is present but was not in effect for this run
- `aidev/pipeline.py`:5243 - `_config` fixes `setup_command`/`test_commands` at launch; editing front matter now changes nothing this run
- `aidev/pipeline.py`:2345 - `DIET_TOOLS` — implement/repair may only run `aidev verify`, so every command must go through `aidev verify --command "…"`
- `desktop/tsconfig.json`:2 - `"files": []` + `references`; needs `-b`, or use the `typecheck` script instead
- `desktop/package.json`:13 - `typecheck` = `typecheck:node` + `typecheck:web`, the only scripts that actually cover `src/domains/**` and the `.tsx`
- `desktop/node_modules` - absent; this is the whole failure
