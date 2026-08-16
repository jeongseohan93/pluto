# Plan — Pipeline 자기수리 2차 (v0.4.1)

## 0. What the requirement leaves to this plan ("빈칸 5개")

The requirement file (`tasks/fix-pipeline-v0.4.1.md`, copied to `.aidev/history/20260816-fix-pipeline-v0-4-1/requirement.md`) is **truncated at line 44**, mid-sentence: `커밋 메시지 형식(`. Everything before it is complete. The five blanks it explicitly delegates are:

| # | Blank | Decision taken here |
|---|---|---|
| 1 | 결함 1b 임계값과 문구 | ≥12 files **or** ≥5 test files named by plan.md → one warning line, suggested budget 160 (§3) |
| 2 | 결함 2 `test_commands:` 문법 | comma-separated on one line, and repeatable lines accumulate (§4) |
| 3 | amend가 stage인가 새 attempt인가 | **Neither a new stage nor a new slice: a new *cycle*** that re-opens `implement`→`test` on the same record; `attempts` keep counting up (§6.1) |
| 4 | amend의 state.json 표현 | `state["amends"]` (list) + `state["amend_open"]` (bool), schema stays 2 (§6.2) |
| 5 | amend 커밋 메시지 형식 | `slice(<id>): amend<N>/<stage>` — same `slice(...)` prefix, so every existing reader keeps working (§6.3) |

No other guessing: the truncated tail is only the tail of blank 5, which is answered.

---

## 1. Files that change

| File | Change |
|---|---|
| `aidev/pipeline.py` | front-matter resolvers, per-stage turns, `test_commands`, plan-scale warning, `--push`, `--amend`, CLI wiring |
| `aidev/workspace.py` | `push_branch`, `branch_remote`, `remote_exists` |
| `aidev/storage.py` | `remember_repo` / `recent_repos` (`data/repos.json`) |
| `aidev/epic.py` | validate the new front-matter keys up front; `cfg.turns_for(DECOMPOSE)`; decompose prompt lists the four keys; pass `data_dir` to `find_epic` |
| `tests/test_pipeline.py` | ~24 new tests (§8) |
| `tests/test_workspace.py` | unit tests for the new resolvers |
| `tests/test_storage.py` | recent-repos round trip |
| `tests/test_epic.py` | a listed slice with a bad `max_turns:` is refused before anything runs |
| `tests/fake_pipeline_claude.py` | `AIDEV_FAKE_BIG_PLAN` mode (a plan naming many files) so §3 is testable |
| `README.md` | front-matter section, options table, `--amend` / `--push` in the flow diagram |

`aidev/runner.py`, `aidev/telemetry.py`, `aidev/reporter.py`, `aidev/cli.py`, `desktop/` — **unchanged**. `RunConfig.max_turns` already carries whatever it is given and already lands in `run.json`, so per-stage budgets become observable for free.

---

## 2. 결함 1a — per-stage `--max-turns`

### 2.1 Front matter (`pipeline.py`, beside `resolve_setup`)

```python
def resolve_max_turns(fields, stages=STAGES) -> Tuple[Optional[int], Dict[str, int]]:
    """(base, per-stage) from ``max_turns:``. (None, {}) when the line is absent."""
```

Accepted, splitting on `re.split(r"[,\s]+", ...)`:

```
max_turns: 120                     # every stage
max_turns: implement=140           # one stage
max_turns: implement=140, test=60
max_turns: 100, implement=140      # base + override
```

Refused with `PipelineError` (exit 2), following `resolve_gates`' rule that a line that was meant to do something and silently does nothing is worse than a stop:
- empty value (`max_turns:`)
- unknown stage name → `unknown stage 'planz' in max_turns: (stages: plan, implement, test)`
- non-integer or `<= 0` → `max_turns: needs a positive whole number, got 'abc'`
- the same stage twice with different values

Token parsing lives in a shared `parse_turn_tokens(tokens, stages, where)` so the CLI option reuses it verbatim.

### 2.2 Key normalisation

In `parse_front_matter`, one line changes:

```python
fields[key.strip().lower().replace("-", "_")] = _strip_inline_comment(value)
```

so `max-turns:` and `max_turns:` are the same key. Nothing today uses a dash, so this is additive.

### 2.3 CLI

- `--max-turns` default changes from `DEFAULT_MAX_TURNS` to `None`, so "the user said 80" is distinguishable from "the user said nothing". Help text unchanged in meaning: `per stage (default 80)`.
- new `--max-turns-stage implement=140`, `action="append"`, `dest="stage_turns"`, repeatable, parsed by `parse_turn_tokens`.
- `aidev run --max-turns` in `cli.py` is a different command and keeps its default of 80.

### 2.4 Config

```python
@dataclass
class PipelineConfig:
    max_turns: int = DEFAULT_MAX_TURNS          # the base, already resolved
    stage_max_turns: Dict[str, int] = field(default_factory=dict)

    def turns_for(self, stage: str) -> int:
        return self.stage_max_turns.get(stage, self.max_turns)
```

`_config()` gains `fields: Optional[Dict[str, str]] = None` and resolves precedence in one place:

**CLI stage > front-matter stage > CLI base > front-matter base > `DEFAULT_MAX_TURNS`.**

Call sites that already parse front matter (`launch_slice`, `continue_slice`, new `amend_slice`) pass `fields`; `epic._finish` does not and gets today's behaviour.

`execute_stage` uses `max_turns=cfg.turns_for(spec.stage)` instead of `cfg.max_turns`. `epic.decompose_spec` uses `cfg.turns_for(DECOMPOSE)` — the number the decompose prompt quotes to the model stays the number the decompose run actually gets.

---

## 3. 결함 1b — "this plan may not fit the default budget"

### 3.1 The estimator (`pipeline.py`)

```python
# Measured 2026-08-15/16: three implement stages died at turn 81 with the work
# half done. These two counts are what those three plans had in common, so they
# are the trigger - a plan naming this much work is the one that needs a raise.
PLAN_FILE_WARN = 12
PLAN_TEST_WARN = 5
PLAN_SUGGESTED_TURNS = 160          # 2x the default; what the three resumes actually cost

_PLAN_PATH_RE = re.compile(
    r"[\w./\\-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|json|toml|ya?ml|md|css|html)\b"
)

def plan_scale(text: str) -> Dict[str, int]:
    """How much work plan.md names: distinct file paths, and how many are tests."""
```

Paths are normalised (`\`→`/`, lowercased) and de-duplicated; a path is counted as a test when any segment matches `test`/`tests`/`spec`/`__tests__` or the basename starts with `test_` / ends `_test`/`.test`/`.spec`. This is a text heuristic on a document that lists file paths — it is a hint on a warning line, never a gate, and never blocks anything.

### 3.2 Where it is published

In `finish_stage`, right after `rec.write_plan(run.text)`:

```python
scale = plan_scale(run.text)
budget = cfg.turns_for("implement")
scale["warn"] = (
    (scale["files"] >= PLAN_FILE_WARN or scale["test_files"] >= PLAN_TEST_WARN)
    and budget < PLAN_SUGGESTED_TURNS      # already raised: nothing to say
)
state["plan_scale"] = scale
```

and, when `warn`, `say()` exactly (the wording, blank #1):

```
[pipeline] warning: this plan names 18 file(s), 6 of them tests - the default
[pipeline]          budget of 80 turns may not be enough (three slices this size
[pipeline]          died at turn 81, measured 2026-08-15)
[pipeline]          raise it before approving:
[pipeline]            max_turns: implement=160     in the requirement front matter
[pipeline]            --max-turns-stage implement=160
```

`await_approval` gains an optional `note: str = ""`:
- printed again when the gate starts waiting (so it is on screen next to `edit <path>`), and
- rendered into `APPROVAL_TEMPLATE` as `# ` comment lines, so the human who opens `approvals/plan.md` reads it there too. `read_decision` already ignores `#` lines, so the template stays inert.

`run_pipeline` passes the note built from `state["plan_scale"]` when the gate is `plan`. A slice with `approval: none` still gets the log line — that is the unattended case where it matters most.

---

## 4. 결함 2 — `test_commands:` decoupled from `setup:`

### 4.1 Syntax (blank #2)

```markdown
---
approval: plan
setup: npm ci --prefix backend
test_commands: npm run test:guards, npm run lint
test_commands: npx vitest run          # a second line accumulates
---
```

- comma is the separator; whitespace inside a command is preserved (`npm run test:guards` is one command)
- a command containing a comma is not supported — documented in README
- each command is validated with the **same** `_SHELL_METACHARS` rule as `setup:` (a chained command would never match a permission rule anyway, so accepting it would only mislead)
- `test_commands:` with an empty value is an error, like `setup:` and `approval:`

To make repeated lines accumulate without changing last-wins for every other key, `parse_front_matter` gains one explicit rule:

```python
# Only these keys accumulate across repeated lines; everything else is last-wins,
# which is what approval:/setup: have always been.
_MULTI_FIELDS = ("test_commands",)
```

accumulated values are joined with `"\n"`, and `resolve_test_commands` splits on `[\n,]`.

### 4.2 Rule generation

`setup_tool_rules` keeps its name and its test (`test_workspace.py:301`) but delegates:

```python
def command_tool_rules(command) -> Tuple[str, ...]:   # exact form + prefix form
def setup_tool_rules(command) -> Tuple[str, ...]:     # = command_tool_rules(command)
```

### 4.3 `allowed_tools_for`

```python
if cfg.test_commands:
    # Declared: only these. The measured defect is exactly the setup-derived rule
    # ('npm ci --prefix backend' -> Bash(npm ci:*)) crowding out the real test
    # command, leaving the agent unable to verify and off the plan.
    declared = tuple(r for c in cfg.test_commands for r in command_tool_rules(c))
else:
    declared = tuple(TEST_COMMAND_TOOLS) + setup_tool_rules(cfg.setup_command)
for rule in declared + tuple(cfg.allow_tools):
    ...
```

`--allow-tool` still appends in both branches — it is an explicit human override and must not be silently dropped. `plan` stays empty in both branches.

### 4.4 The prompt says the same thing

`build_prompt` gains `test_commands: Sequence[str] = ()`. When non-empty, the implement/test prompts carry, in addition to `_ALLOWED_NOTE` (which is still built from the granted tuple, so the two cannot drift):

```
- This project's verification commands are declared by the requirement:
    npm run test:guards
    npm run lint
  Run these. Do not go looking for another command, and do not conclude the
  project cannot be verified.
```

This is the second half of the measured defect: the agent left the plan because it believed verification was impossible.

### 4.5 Validation up front

`epic.validate_items` calls `resolve_test_commands(fields)` and `resolve_max_turns(fields)` alongside `resolve_gates`/`resolve_setup`, so a typo in epic item 4 is refused before items 1–3 build branches. `epic._DECOMPOSE_PROMPT` is updated: "The front matter accepts exactly four keys: `approval:`, `setup:`, `test_commands:`, `max_turns:`."

---

## 5. 결함 3 & 4 — repo hint with candidates, and `--merge --push`

### 5.1 Recent repos (`storage.py`)

```python
RECENT_REPOS_FILENAME = "repos.json"
RECENT_REPOS_LIMIT = 10

def remember_repo(data_dir: Path, repo: Path) -> None:
    """Record a repo the tool was pointed at. Best effort: never raises."""

def recent_repos(data_dir: Path, limit: int = 5) -> List[str]:
    """Most recently used first. Paths that no longer exist are dropped."""
```

`{"schema": 1, "repos": [{"path": "...", "last_used": "<iso>"}]}`, written with `write_json_atomic`, read with `read_json_tolerant`, whole body wrapped so a read-only or full data dir can never break a pipeline command.

`_dispatch` calls `remember_repo(data_dir, repo)` after resolving the repo, **only when `(repo/.aidev).is_dir()`** — otherwise a run from a junk cwd would poison the candidate list.

### 5.2 The hint

`_repo_hint(repo, repo_given, data_dir=None)` keeps its current two lines and appends candidates when there are any:

```
    no .aidev/ here - this is probably not the target repository.
    pass --repo <path>
    recently used (aidev never picks one for you - name it):
      --repo C:\Users\minsa\jokertest
      --repo C:\Users\minsa\ai-dev-orchestrator
```

**No auto-apply, ever** — the candidates are printed as literal `--repo <path>` strings the user can copy. `data_dir` is threaded (not globals, not `default_data_dir()`, so `--data-dir` in tests is honoured) through:

`_dispatch` → `list_slices(repo, repo_given, data_dir)`, `find_slice(repo, id, repo_given, data_dir)`, `merge_slice(args, repo, data_dir, repo_given)`, `discard_slice(...)`, `_finished_workspace(repo, id, repo_given, data_dir)`, `epic.find_epic(repo, pattern, repo_given, data_dir)`.

All new parameters are keyword-with-default, so `find_record` (which takes an already-rendered hint string) is untouched.

### 5.3 `--merge <id> --push`

`workspace.py` additions:

```python
def branch_remote(repo, branch) -> Optional[str]:   # git config --get branch.<b>.remote
def remote_exists(repo, name) -> bool:              # git remote -> name in list
def push_branch(repo, remote, branch) -> str:
    """Push exactly one branch, by explicit refspec. Raises GitError."""
    return git(repo, "push", remote, "refs/heads/{0}:refs/heads/{0}".format(branch))
```

The explicit `refs/heads/<base>:refs/heads/<base>` refspec is the guarantee the requirement asks for: **the slice branch is not in the argv at all**, whatever `push.default` is set to. No `--force`, no `--all`, no `--tags`, no `--set-upstream`.

`--push` is added to the pipeline parser (`action="store_true"`). In `_dispatch`, `--push` without `--merge` is a usage error (exit 2): `--push only makes sense with --merge`.

In `merge_slice`, inside the existing `with SliceLock(rec):` block, after `set_status(rec, state, STATUS_MERGED)`:

```python
if getattr(args, "push", False):
    remote = workspace.branch_remote(repo, ws.base) or "origin"
    if not workspace.remote_exists(repo, remote):
        push = {"status": "failed", "remote": remote,
                "error": "no git remote named '{0}'".format(remote)}
    else:
        try:
            workspace.push_branch(repo, remote, ws.base)
            push = {"status": "pushed", "remote": remote, "branch": ws.base, "at": now_iso()}
        except workspace.GitError as exc:
            push = {"status": "failed", "remote": remote, "error": str(exc)}
    state["merge"]["push"] = push
    rec.write_state(state)
```

- success → `say("pushed {base} to {remote} (the slice branch was not pushed)")`, exit 0.
- failure → the merge **stands** (`status` is still `merged`, the commit is real); the message says so explicitly and prints `git -C <repo> push <remote> <base>` to retry. Exit code is **`EXIT_FAILED` (1)**, not 0: the entire point of this flag is "if you forget, the only copy is local", so a silent 0 after a failed backup would defeat it.

Default (no `--push`) pushes nothing — unchanged behaviour, and the flag is opt-in per the requirement.

---

## 6. 신규 채널 — `--amend`

```
aidev pipeline --repo <r> --amend <slice-id> "<지시문>"
```

### 6.1 What an amend *is* (blank #3)

An amend is **a new cycle over the existing slice record**, not a new stage and not a new slice:

- it runs `AMEND_STAGES = ("implement", "test")` — intersected with `stage_order(state)` so a legacy record cannot ask for a stage it never had
- **`plan` does not re-run.** The instruction *is* the plan for this cycle. Re-planning a finished slice would cost a full readonly stage plus a gate on every correction, and the requirement calls this the 최빈 동작 of the 사후 감독 model.
- **during an amend, only the gates of stages in the cycle are consulted.** `plan`'s gate is skipped because `plan` is skipped. This also makes amending a `rejected` slice work: the stale `rejected` in `approvals/plan.md` is not re-read.
- per-stage `attempts` keep counting up (they are what makes `stage_run_id` unique); `session_id` is dropped, because a session that concluded "I finished" must not be resumed with a new instruction; `failures` resets to 0.
- the worktree and branch are the ones the slice already owns — `workspace.verify()` must pass, exactly as `continue_slice` requires. A pre-v0.3 slice with no workspace amends in place, with the same note `continue_slice` already prints.

Refused:
- `discarded` → nothing to amend, the branch is gone
- no `state.json`, or the workspace is broken → the same message `continue_slice` gives
- `--amend` without an instruction, or an instruction without `--amend` → exit 2
- combined with `--requirement` / `--epic` / `--merge` / `--resume-slice` / … → the existing `modes` check, with `("amend", "--amend")` added

Allowed with a note:
- `merged` → runs, and the summary says the branch now has commits the base does not: `aidev pipeline --repo <r> --merge <id>` again
- a slice belonging to an epic → runs, plus `note: this slice is part of epic <id>; later slices were branched from its earlier tip and do not contain this amend`

### 6.2 state.json (blank #4)

`STATE_SCHEMA` stays **2**. Every key added is optional, which is the rule the file already documents for the `epic` key ("a reader that does not know it is not wrong, it is only older").

```json
"amend_open": true,
"amends": [
  {
    "n": 1,
    "instruction": "인원수 표시를 고쳐라",
    "stages": ["implement", "test"],
    "opened_at": "2026-08-16T21:10:00+09:00",
    "previous_result": {"stage": "test", "run_id": "...", "summary": "..."},
    "closed_at": "2026-08-16T21:26:00+09:00"
  }
]
```

- `amends` is append-only; `n` is `len(amends)` at open time.
- `current_amend(state)` returns the last entry **only while `amend_open`** is true.
- `run_pipeline` sets `closed_at` and clears `amend_open` immediately before `set_status(rec, state, STATUS_DONE)`.
- On any failure path `amend_open` stays true — deliberately: `--resume-slice last` on a failed amend then continues **the amend cycle**, not the whole slice. `continue_slice` says so: `resuming amend #1 of slice <id>`.
- The instruction is also written durably to `.aidev/slices/<id>/amends/001.md` and mirrored into `.aidev/history/<id>/amends/001.md`; `history_snapshot` gains `"amends"` and `"plan_scale"`.

### 6.3 Commits (blank #5)

`commit_stage(cfg, rec, state, stage, label=None)`; `label` defaults to `stage`. `run_pipeline` passes `"amend{n}/{stage}"` during an amend:

```
slice(20260816-doctor): amend1/implement
slice(20260816-doctor): amend1/test
slice(20260816-doctor): amend2/implement
```

The `slice(<id>): ` prefix is unchanged, so `COMMIT_MESSAGE`, `log_subjects`, the merge commit and every existing test keep working. `state["commits"]` is keyed by the label, so `commits["implement"]` (the original) survives beside `commits["amend1/implement"]`. `stage_entry(state, stage)["commit"]` is updated to the newest sha for that stage — truthful, and `_workspace_lines` gets a small helper so the commit line shows the amend keys after the stage keys instead of dropping them.

### 6.4 The prompt

`build_prompt` gains `amend: Optional[Dict] = None`; `_IMPLEMENT_PROMPT` and `_TEST_PROMPT` gain an `{amend}` slot immediately under the "Nobody is watching" line (empty string in the normal path):

```
AMENDMENT - THIS IS WHAT YOU MUST DO NOW
----------------------------------------
{instruction}

WHAT THE PREVIOUS CYCLE REPORTED ({stage})
------------------------------------------
{previous}

The REQUIREMENT and PLAN below are history: they describe what this slice already
built and are here so you understand the code you are changing. The amendment
above is the change being asked for now. Do only that, and do not undo work the
amendment does not mention.
```

`{previous}` is the **직전 RESULT**: `rec.runs()[-1]`'s `summary` (already capped at 2000 chars when recorded) with its stage. This satisfies the requirement's three context pieces — 지시문, 원 requirement, 직전 RESULT — in one prompt, for both implement and test.

### 6.5 CLI wiring

```python
cmd.add_argument("--amend", default=None, metavar="SLICE",
                 help="run a fix cycle on a finished slice, on its own branch")
cmd.add_argument("instruction", nargs="?", default=None,
                 help="what to change (with --amend)")
```

`nargs="?"` keeps every existing invocation valid. `_dispatch` routes `--amend` before `--requirement`, and `amend_slice(args, repo, data_dir, repo_given)`:

1. `find_slice(...)`
2. read state; refuse `discarded` / unreadable; `workspace.verify` when a workspace exists
3. parse the stored `requirement.md` front matter → `gates`, `setup_command`, `test_commands`, turn budgets (so an amend honours the same declarations, and `run_setup` still skips because `state["setup"]["status"] == "done"` with the same command)
4. `open_amend(rec, state, instruction, stages)` — reset entries, append the record, write the instruction file
5. `_finish(_config(args, repo, data_dir, ws, setup_command, fields), rec, state, body)` — the same lock, the same loop, the same summary. `--dry-run` prints what would be re-run and touches nothing.

`render_summary` gains one line when `state["amends"]`: `Amends    2  (last: 인원수 표시를 고쳐라)` truncated to the column width.

---

## 7. `run_pipeline` — the only loop change

```python
amend = current_amend(state)
amend_stages = tuple(amend.get("stages") or ()) if amend else ()
for stage in stage_order(state):
    if amend is not None and stage not in amend_stages:
        continue                       # plan is history during an amend
    ...
```

Everything else — readonly baseline check, `run_setup`, quota waits, `note_stage_failure`, `commit_stage`, gate handling — is reached unchanged; the gate is naturally limited to the cycle's stages by the `continue`.

---

## 8. Verification

`python -m pytest -q` from the repo root (already granted to the test stage by `TEST_COMMAND_TOOLS`). Every test below drives the real CLI through `main(argv(...))` with the existing `claude_bin` stub, the way the suite already does — no new mocking layer.

**결함 1a** — `test_max_turns_front_matter_sets_a_stage_budget` (plan 80 / implement 140 / test 80 in argv), `test_max_turns_cli_stage_option_beats_front_matter`, `test_max_turns_global_applies_to_every_stage_and_cli_base_wins`, `test_a_bad_max_turns_line_is_refused` (unknown stage, `abc`, `0` → exit 2, zero invocations), unit `test_resolve_max_turns_syntax` (parametrized) in `test_workspace.py`.

**결함 1b** — `test_plan_size_warning_reaches_the_log_and_the_approval_file` (new `AIDEV_FAKE_BIG_PLAN` stub mode emitting a plan naming 18 files, 6 tests; assert the warning in stdout, in `approvals/plan.md`, `state["plan_scale"]["files"] >= 18`), `test_a_small_plan_does_not_warn` (default stub → no warning), `test_a_raised_budget_silences_the_warning` (`--max-turns-stage implement=160`), unit `test_plan_scale_counts_files_and_tests`.

**결함 2** — `test_test_commands_replace_the_built_in_rules` (allowedTools is exactly the declared rules), `test_setup_no_longer_crowds_out_the_test_command` (the measured case: `setup: npm ci --prefix backend` + `test_commands: npm run test:guards` → `Bash(npm ci:*)` absent, `Bash(npm run test:guards)` present, and the stub's `requires_approval` mode reports PASS), `test_repeated_test_commands_lines_accumulate`, `test_a_test_command_needing_a_shell_is_refused`, `test_the_prompt_names_the_declared_commands`; existing `test_no_setup_declared_runs_nothing` (asserts `set(rules) == set(TEST_COMMAND_TOOLS)`) is the undeclared-path regression and must keep passing untouched.

**결함 3** — `test_list_without_repo_offers_the_repos_it_has_seen` (run a slice against `repo`, then `--list` from an empty cwd with the same `--data-dir`: the path appears as a `--repo` candidate **and the listing is still empty** — no auto-apply), `test_resume_without_repo_offers_candidates_but_resumes_nothing` (exit 2), `test_a_repo_without_aidev_is_not_remembered`; storage unit `test_recent_repos_round_trip` (order, cap, missing paths dropped, unreadable file → `[]`). Existing `test_list_without_repo_says_which_repo_it_looked_in` and `test_resume_without_repo_points_at_the_repo_flag` must keep passing.

**결함 4** — a `bare_remote` fixture (`git init --bare` in `tmp_path`, `git remote add origin`): `test_merge_push_pushes_only_the_base_branch` (`git ls-remote` has `refs/heads/<BASE_BRANCH>` at the merge commit and **no** `refs/heads/slice/*`), `test_merge_without_push_pushes_nothing`, `test_merge_push_failure_reports_but_keeps_the_merge` (origin pointing at a nonexistent path → exit 1, `state["status"] == "merged"`, `state["merge"]["push"]["status"] == "failed"`, stdout says the merge stands), `test_push_without_merge_is_a_usage_error`.

**--amend** — `test_amend_reruns_implement_and_test_on_the_same_branch` (exactly 2 new invocations, no plan; both prompts carry the instruction + the original requirement + the previous RESULT; commits `amend1/implement` and `amend1/test` on the branch; worktree path unchanged; `state["amends"][0]["n"] == 1`, `amend_open` false, status `done`), `test_amend_does_not_reask_the_plan_gate`, `test_amend_starts_a_fresh_session` (`--resume` absent from the amend argv), `test_a_second_amend_numbers_itself` (`amend2/...`, `len(amends) == 2`), `test_a_failed_amend_is_resumed_as_an_amend` (fail mode → exit 1, `amend_open` true; then ok mode + `--resume-slice last` → implement+test only, finishes), `test_amend_refuses_a_discarded_slice`, `test_amend_needs_an_instruction`, `test_amend_cannot_combine_with_requirement`, `test_amend_on_a_merged_slice_says_it_must_be_merged_again`, `test_amend_dry_run_touches_nothing`.

**epic** — `test_a_listed_slice_with_a_bad_max_turns_line_is_refused_before_anything_runs`.

Manual check after the suite is green: `aidev pipeline --repo . --list` from an unrelated directory shows candidates; `--dry-run` on a `--amend` prints the cycle without touching git.

---

## 9. What could go wrong

1. **The positional `instruction` swallows a stray token.** `aidev pipeline --repo r --list extra` would bind `extra` instead of erroring. Mitigated: `_dispatch` refuses an `instruction` given without `--amend` (exit 2, naming the argument). Covered by a test.
2. **Multi-value front matter leaking into `approval:`/`setup:`.** Accumulation is restricted to the explicit `_MULTI_FIELDS` tuple; both existing keys stay last-wins, and their existing parametrized tests are the guard.
3. **`--max-turns` default becoming `None`** — anything reading `args.max_turns` directly would get `None`. Only `_config` reads it; it is resolved there. `aidev run` is a separate parser and untouched.
4. **Push reaching a network in CI.** Every push test uses a local bare repo path; nothing resolves a hostname. A real remote can still exceed `GIT_TIMEOUT_S` (300s) on a large first push — the failure is reported as a push failure with the merge intact, which is the correct outcome, and is noted in the README.
5. **`--push` moving something unintended.** The explicit refspec plus never passing `ws.branch` is the structural guarantee; the `ls-remote` assertion is the behavioural one.
6. **Amend on an epic slice desynchronising the chain.** Later slices were cut from the earlier tip and do not contain the amend. Not fixed here (out of scope) — a note is printed when `state["epic"]` is present, and the README says it.
7. **`plan_scale` miscounting.** It is a regex over prose; a plan that writes paths without extensions under-counts, and a plan quoting many filenames in passing over-counts. It only ever adds one advisory line and can never block or change a budget, so both directions are cheap. The threshold constants are module-level and one edit away.
8. **Windows.** New git calls go through `workspace.run` (already timeout-guarded, UTF-8, `errors="replace"`); new files use `write_text_atomic` / `write_json_atomic` with their existing `PermissionError` retry. `data/repos.json` writes are additionally wrapped so a locked file can never fail a command.
9. **State growth.** `amends` is append-only; each entry holds one instruction and one capped summary (~2 KB). A slice amended 50 times carries ~100 KB — acceptable, and `state.json` is rewritten whole already.
10. **Scope.** Detecting "died because it ran out of turns" from the result event is *not* implemented — the requirement asks for a configurable budget (1a) and a pre-flight warning (1b), and inventing a new failure classifier would be the speculative improvement the requirement forbids.
