# PROGRESS - 20260821-req-editor · implement
2026-08-21T13:39:50+09:00

attempt 1   turn 129/140   (turn-budget 90%)

## Done
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\domains\pipeline\types.ts
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\domains\pipeline\main\requirement-store.ts
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\domains\pipeline\main\store.ts
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\shared\ide.ts
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\preload\index.ts
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\app\main\register-handlers.ts
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\domains\code-view\ui\monaco.ts
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\domains\code-view\ui\monaco-esm.d.ts
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\domains\pipeline\ui\RequirementEditor.tsx
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\domains\pipeline\ui\LaunchPanel.tsx
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\renderer\src\features\pipeline\PipelineSidebar.tsx
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\renderer\src\App.tsx
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-req-editor\desktop\src\domains\pipeline\main\requirement-store.test.ts
- ran cd desktop && find src -name "*.tsx" -o -name "*.ts" | sort
- ran sed -n 1,40p src/renderer/src/components/Icon.tsx && echo "=== cli-runner spawn ===" && sed -n 175,215p src/domains/pipeline/main/cli-runner.ts
- ran pwd && ls
- ran cd .. && sed -n 690,715p aidev/pipeline.py && echo "=== parse_front_matter ===" && sed -n 240,280p aidev/pipeline.py
- ran cd desktop && sed -i 's/\bfunction no(path: string/function fail(path: string/; s/return no(rel,/return fail(rel,/g' src/domains/pipeline/main/requirement-st...
- ran bash /dev/stdin <<'EOF' T=$(mktemp -d) cd "$T" git init -q git config user.name t git config user.email t@l echo seed > seed.txt git add -- seed.txt git comm...
- ran npm ci --prefix "C:/Users/minsa/ai-dev-orchestrator-slices/20260821-req-editor/desktop"
- ran python -m aidev.specs --repo "C:/Users/minsa/ai-dev-orchestrator-slices/20260821-req-editor" --base f4373c9 --head HEAD
- ran npm ls -g --depth=0
- ran node --version
- ran node --test "C:/Users/minsa/ai-dev-orchestrator-slices/20260821-req-editor/desktop/src/domains/pipeline/requirement.test.ts"
- ran aidev verify --cwd "C:/Users/minsa/ai-dev-orchestrator-slices/20260821-req-editor" --command "python -c \"import re,pathlib;from aidev.pipeline import parse_...
- ran cat "C:/Users/minsa/ai-dev-orchestrator/.aidev/slices/20260821-req-editor/verify/01-1-python-c-import-re-pathlib-from-.log"
- ran sed -n 2100,2160p tests/test_pipeline.py
- ran ls -a desktop | head -20

## Remaining
Named by the plan and not edited yet:
- domains/pipeline/commands.ts
- \.md
- commands.ts
- main/cli-runner.ts
- CodeViewer.tsx
- domains/code-view/types.ts
- preload/ide.ts
- main/aidev-store.ts
- aidev/workspace.py
- aidev/pipeline.py
- /requirement.md
- pipeline.py
- desktop/tsconfig.test.json
- tsconfig.web.json
- desktop/src/domains/pipeline/requirement.ts
- ... 26 more

## Last words

Implementation is complete. Summary below.

## Files

**New**
- `desktop/src/domains/pipeline/requirement.ts` — pure: `TASKS_DIR`, `normalizeRequirementName`, `requirementPathFor`, `requirementNameProblem`, `requirementCommitMessage`, `REQUIREMENT_TEMPLATE`, `MIN_REQUIREMENT_CHARS`. The path guard is `commands.ts`'s `isRequirementPath`, imported — launcher and editor cannot disagree about what a requirement is.
- `desktop/src/domains/pipeline/requirement.test.ts`
- `desktop/src/domains/pipeline/main/requirement-store.ts` — `readRequirement` / `saveRequirement`, `writeTextAtomic`, `git add` + `git commit -- <path>` with the `workspace.py` identity/gpgsign preamble. Never throws.
- `desktop/src/domains/pipeline/main/requirement-store.test.ts`
- `desktop/src/domains/pipeline/ui/RequirementEditor.tsx` — Monaco (editable) with a `<textarea>` fallback, overlaid on the main pane.

**Changed** — `pipeline/types.ts` (`editable`, `RequirementDoc/SaveInput/Save`, two bridge members), `shared/ide.ts` (2 channels + the "one write" doc corrected to two), `preload/index.ts` (2 members + header corrected), `app/main/register-handlers.ts` (2 handlers, shape checked again), `pipeline/main/store.ts` ...

## Resume

    aidev pipeline --repo C:\Users\minsa\ai-dev-orchestrator --resume-slice 20260821-req-editor
