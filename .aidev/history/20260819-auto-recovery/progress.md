# PROGRESS - 20260819-auto-recovery · implement
2026-08-19T15:06:55+09:00

attempt 1   turn 144/160   (turn-budget 90%)

## Done
- edited /Users/jeongseohan/pluto/ai-dev-orchestrator-slices/20260819-auto-recovery/aidev/pipeline.py
- edited /Users/jeongseohan/pluto/ai-dev-orchestrator-slices/20260819-auto-recovery/aidev/epic.py
- edited /Users/jeongseohan/pluto/ai-dev-orchestrator-slices/20260819-auto-recovery/tests/fake_pipeline_claude.py
- edited /Users/jeongseohan/pluto/ai-dev-orchestrator-slices/20260819-auto-recovery/tests/test_pipeline.py
- edited /Users/jeongseohan/pluto/ai-dev-orchestrator-slices/20260819-auto-recovery/tests/test_zz_debug.py
- edited /Users/jeongseohan/pluto/ai-dev-orchestrator-slices/20260819-auto-recovery/README.md
- ran sed -n '1,120p' tests/test_pipeline.py
- ran grep -n "test_every_known_front_matter_key_has_a_reader\|test_an_unknown_front_matter_key_warns_and_is_ignored\|error_max_turns" tests/test_pipeline.py
- ran sed -n '2795,2870p' tests/test_pipeline.py; echo "==== 3395"; sed -n '3395,3436p' tests/test_pipeline.py
- ran grep -n "def tool_results" -A 25 aidev/events.py
- ran grep -n "ToolResult(" -B 5 -A 15 aidev/events.py
- ran grep -n "def _touch_file" -A 20 aidev/telemetry.py
- ran grep -n "quota_without_a_reset_time_falls_back" -A 30 tests/test_pipeline.py
- ran aidev verify 2>&1 | tail -60
- ran aidev verify --only "python -m pytest -q tests/test_pipeline.py -x -k 'honest_turn_death'" 2>&1 | tail -40
- ran aidev verify --help 2>&1 | head -40
- ran aidev verify --command "python -m pytest -q tests/test_pipeline.py -k honest_turn_death" --log-dir /tmp/aidev-verify-log 2>&1 | tail -30
- ran aidev verify --command "python -m pytest tests/test_pipeline.py -k honest_turn_death -x --tb=long -q --log-file=/dev/stdout --log-file-level=INFO" --log-dir ...
- ran aidev verify --command "python -m pytest tests/test_zz_debug.py -x -q --tb=long" --log-dir ./tmp-verify 2>&1 | tail -5
- ran rm -rf tests/test_zz_debug.py tmp-verify && ls tests/
- ran aidev verify --command "python -m pytest tests/test_pipeline.py tests/test_recovery.py -q --tb=long" --log-dir ./tmp-verify 2>&1 | tail -20

## Remaining
Named by the plan and not edited yet:
- aidev/progress.py
- telemetry.py
- runner.py
- reporter.py
- cli.py
- .aidev/history/20260819-auto-recovery/requirement.md
- aidev/recovery.py
- progress.py
- progress.md
- failure.md
- observation.md
- tests/test_recovery.py
- test_epic.py
- state.json

## Last words

I'll start by reading the key files to ground the plan in the real code.

Now the core: PipelineConfig, StageSpec, execute_stage, run_stage.

Now the CLI region: flags, `_config`, `start_slice` dry-run, `list_slices`, `resume_slice`.

Now the fake CLI and the relevant existing tests.

Now the two front-matter readers and the known-keys list:

Now `PipelineConfig`:

Now `StageSpec.disallowed` and `execute_stage` wiring:

Now `recovery_record` and the state ledger. I'll put it next to `stage_entry`:

Now the core `run_stage` restructure:

Now update `run_stage`'s docstring to match:

Now `observation_note`, beside `resume_progress_note`:

Now `run_observer`, beside `run_diagnosis`:

Now `_recovery_lines` in the summary:

Now the CLI flags and `_config` wiring:

Now the dry-run line and up-front front-matter validation:

Now the "dead process" aids: `--list` and `resume --dry-run`.

Now epic.py:

Now the fake CLI stub:

Now wire the turn-death path into `main`:

Now I'll add the end-to-end tests after the quota section:

Full suite green (558 passed). Now the spec-comment check:

Now I'll rewrite the "실패와 쿼터" section as "실패와 자동 복구 (v0.7)":

Now the 안전핀 list and the options table:

Now...

## Resume

    aidev pipeline --repo /Users/jeongseohan/pluto/ai-dev-orchestrator --resume-slice 20260819-auto-recovery
