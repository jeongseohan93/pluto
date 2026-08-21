# PROGRESS - 20260821-work-order-6 · implement
2026-08-22T01:17:25+09:00

attempt 1   turn 181/180   (stage ended incomplete)

## Done
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-work-order-6\aidev\workspace.py
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-work-order-6\aidev\pipeline.py
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-work-order-6\tests\fake_pipeline_claude.py
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-work-order-6\tests\test_workspace.py
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-work-order-6\tests\test_pipeline.py
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-work-order-6\README.md
- edited C:\Users\minsa\ai-dev-orchestrator-slices\20260821-work-order-6\aidev\workorder.py
- ran sed -n '1,80p' aidev/graph/build.py; echo "=== db_path/open_db ==="; grep -n "def db_path" -A 20 aidev/graph/build.py; grep -n "def open_db" -A 25 aidev/grap...
- ran sed -n '120,260p' tests/test_pipeline.py
- ran sed -n '3555,3620p' tests/test_pipeline.py; echo "=== run_slice helper ==="; grep -n "def run_slice" -A 12 tests/test_pipeline.py; echo "=== epic 370 ==="; s...
- ran sed -n '300,400p' tests/test_verify.py
- ran grep -n "Install git" -A 3 aidev/pipeline.py
- ran python -c "import aidev.pipeline, aidev.workorder, aidev.workspace, aidev.graph.db; print('ok')" && python -m aidev.specs 2>&1 | head -30
- ran python -c "import aidev.pipeline, aidev.workorder, aidev.workspace, aidev.graph.db; print('ok')"
- ran python -m aidev.specs --repo . --base HEAD --head HEAD 2>&1 | head -5; python -c "import aidev.pipeline; print('pipeline ok')"
- ran python -m aidev.specs --repo . --base HEAD --head HEAD
- ran grep -n "^def script\|def script(" -A 15 tests/test_verify.py | head -25; echo "=== guarded ==="; grep -n "def guarded" -A 12 tests/test_verify.py
- ran grep -rn "AIDEV_VERIFY_COMMANDS" aidev/ | head
- ran sed -n '285,340p' aidev/cli.py
- ran aidev verify --command "python -m pytest -q tests/test_workorder.py tests/test_pipeline.py -x -q"
- ran python - <<'PY' import pathlib p = pathlib.Path("tests/test_pipeline.py") text = p.read_text(encoding="utf-8") text = text.replace( ' for key in ("workspace"...
- ran sed -n '2617,2720p' tests/test_pipeline.py

## Remaining
Named by the plan and not edited yet:
- tests/test_workorder.py
- aidev/graph/db.py
- tests/test_verify.py
- plan.md
- approvals/plan.md
- approvals/plan-reapproval-NNN.md
- /requirement.md
- /plan.md
- /slice.json
- /failure.md
- diagnosis.md
- progress.md
- /amends/NNN.md
- /evil.py
- aidev/thing.py
- ... 6 more

## Last words

I'll start by getting oriented in the codebase.

Now the workspace snapshot machinery.

Now the graph DB lookup:

Now the prompts:

Now `build_prompt`:

Now wire it into `run_setup`, `run_verify`, `finish_stage`, and `run_pipeline`:

Now `run_verify`:

Now `finish_stage` (plan validation) and `run_stage` (prompt slot):

Now `run_pipeline` itself:

Now `render_summary`, `launch_slice`, `continue_slice`, `amend_slice`, `reopen_plan`, and the `--no-worktree` refusal:

Now the amend cycle's own work order, and `open_amend`:

Now `reopen_plan` must drop the seal so a replan has to produce a fresh order:

Now `_pending_files` / temp-file scan over `status_entries`:

Now let's run the existing suite to see the damage:

Now the verify-engine guard tests:

Now let me run the unit tests first, which are fast:

Now the integration tests for the Done Criteria:

Now the README:

Good — `self`/`cls` are exempt. Let me add specs to the four bare helpers:

## Resume

    aidev pipeline --repo C:\Users\minsa\ai-dev-orchestrator --resume-slice 20260821-work-order-6
