# PROGRESS - 20260820-graph-pan · implement
2026-08-20T20:18:23+09:00

attempt 3   turn 58/60   (turn-budget 90%)

## Done
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260820-graph-pan\desktop\src\domains\graph-view\ui\FunctionGraphSurface.tsx
- ran ./desktop/node_modules/.bin/electron ./desktop/node_modules/.repro/main.js 2>&1 | grep -v -E "^\[|DevTools|GPU|Autofill"
- ran ./desktop/node_modules/.bin/electron ./desktop/node_modules/.repro/main.js
- ran ./desktop/node_modules/.bin/tsc --noEmit -p desktop/tsconfig.web.json
- ran aidev verify --command "npx --prefix desktop tsc --noEmit -p desktop/tsconfig.web.json"
- ran aidev verify --command "npm test --prefix desktop"
- ran aidev verify --command "python -m pytest -q"
- ran python -m aidev.specs --base HEAD
- ran git status --porcelain
- ran tail -c 2000 "C:\Users\minsa\AppData\Local\Temp\claude\C--Users-minsa-ai-dev-orchestrator-slices-20260820-graph-pan\fe6e7dce-feb7-452c-905f-d3d0e946b2ed\task...
- ran git stash push -- desktop/src/domains/graph-view/ui/FunctionGraphSurface.tsx

## Remaining
Named by the plan and not edited yet:
- desktop/src/renderer/src/features/workspace/SurfacePane.tsx
- Minimap.tsx
- App.tsx
- SurfacePane.tsx
- layout.ts
- layout.test.ts
- test_specs.py
- src/app.ts
- tests/test_specs.py
- theme.css
- desktop/src/domains/graph-view/layout.ts
- desktop/src/domains/graph-view/layout.test.ts
- tasks/graph-pan.md
- desktop/tsconfig.json
- tsconfig.web.json
- ... 2 more

## Last words

The Python failure is pre-existing and unrelated (the whole slice touches zero Python). Done.

## What was wrong

The `<svg>` is made `pointer-events: none` while `panning || spaceHeld` (`FunctionGraphSurface.tsx:888`). **`pointer-events` is an inherited property**, so that one line turns off hit-testing for the entire graph subtree — every file box, every function row. `panning` was React state cleared only by `endPan`, which fired only if the container div received a matching `pointerup`/`pointercancel`. Meanwhile `startPan` claimed the pointer capture at *pointerdown*, before the pan was known to be a pan. Any sequence where the container never got that release left `pan.current` non-null and `panning` true permanently — and from then on rows could not be clicked at all. `selected` never changes, so the curved calls/callers edges (`edges` → `edgesFor`) and the box dimming (`focus`/`focusBoxes`), both driven purely by `selected`, never appear. Bare-canvas panning and wheel zoom keep working throughout, because with the svg inert every pointerdown falls straight through to the container — which matches the reported shape exactly.

I reached this by elimination, and the elimination...

## Resume

    aidev pipeline --repo C:\Users\minsa\ai-dev-orchestrator --resume-slice 20260820-graph-pan
