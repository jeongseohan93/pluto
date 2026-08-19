# PLAN — 소형 수리 3건 (계기판 오보 · 추적 범위 · watch 잔가시)

## 0. 진단 (코드를 읽고 확인한 것)

### 1) `passed 0` 오보의 실제 원인 — 출력에 개수 줄이 아예 없다

`verify.parse_output`(`aidev/verify.py:275`)의 `_COUNT_RE = r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b"`(`verify.py:267`)는 `470 passed in 161.90s` 를 정상적으로 읽는다. 정규식은 멀쩡하다. 그런데도 0이 나온다는 것은 **그 줄이 출력에 없었다**는 뜻이다.

근거:

- `pyproject.toml:26-28` 에 `[tool.pytest.ini_options] addopts = "-q"` 가 있다.
- 슬라이스가 선언한 명령은 `python -m pytest -q`(`tasks/small-fixes-1.md:4`).
- 둘이 합쳐져 실효 `-qq` 가 된다. pytest 는 verbosity < -1 에서 최종 요약(`N passed in ...`)도, `collected N items` 도 출력하지 않는다. 성공 시 출력이 사실상 비게 되고, 파서는 셀 것이 없어 0을 얻는다.
- 실측 기록도 이와 일치한다: `.aidev/history/20260819-spec-bootstrap/progress.md:58` — "`PASS: python -m pytest -q (161.9s)` … `passed 0` is the summarizer's parse count, not a test count".

따라서 수리는 두 갈래다.

- **(a) 표기 계층**: 파싱이 실패했을 때 0을 만들어내지 말 것 — `passed/failed/skipped` 를 "모름"이 표현되는 값으로 바꾸고 `n/a` 로 찍는다. 이것이 요구사항이 못박은 "0 오보 금지"이며, 이 저장소 밖의 어떤 러너에도 적용된다.
- **(b) 이 저장소의 실제 개수**: `addopts` 에서 `-q` 를 뺀다. 그러면 선언된 `python -m pytest -q` 가 평범한 quiet 모드로 돌아 `470 passed in ...` 을 출력하고, 계기판에 진짜 470이 뜬다.

(b)는 가설이 맞는지 implement 단계에서 **실측으로 확인 가능**하다 — 그 단계가 실행할 수 있는 `aidev verify` 출력의 `passed ...` 줄을 읽으면 된다(§4). 가설이 틀렸다면 (b)만 되돌리고 (a)로 요구사항을 만족시킨다.

### 2) repo 밖 경로 — 필터가 아예 없다

`pipeline.changed_files`(`pipeline.py:4103-4119`)는 각 run 의 `telemetry.json` 에서 `observed.file_accesses` 중 `edit`/`write` 를 **경로 문자열 그대로** 모은다. 세션이 실제로 편집한 것이면 무엇이든 들어오므로 `~/.claude/projects/.../memory/MEMORY.md` 도 들어온다. 범위 검사는 한 줄도 없다. 중복 제거는 `path not in paths` 라 철자가 다른 두 표기(예: 심링크 해석 전/후)는 각각 남는다 — "2회 등장"의 설명이 된다.

같은 누수가 `progress.touched_paths`(`progress.py:45-58`)에도 있다. 실제로 `.aidev/history/20260819-spec-bootstrap/progress.md:12` 의 "Done" 목록에 `MEMORY.md` 가 찍혀 있다. 같은 추적, 같은 결함이므로 같은 필터를 건다.

경계 판정에 쓸 도구는 이미 있다: `storage._normalised`(`storage.py:518`, resolve + `os.path.normcase`)와 `storage.in_repo_scope`(`storage.py:532`). `storage` 는 패키지 내부 import 가 없어(`storage.py:19-28`) `progress` 가 가져다 써도 순환이 없다.

### 3) watch 잔가시 — 재현된다. 원인은 "대상 run 을 시작 시 한 번만 고른다"는 것

`cmd_watch`(`cli.py:327-395`)는 `resolve_watch_target` 으로 run 디렉터리를 **한 번** 고르고, 그 뒤로는 그 디렉터리만 폴링한다. 따라온 run 이 종료 상태가 되면 `payload.get("status") != "running"` 에서 곧장 break → 최종 리포트를 찍고 종료한다(`cli.py:366-367`, `390-395`).

그래서 두 가지가 같은 뿌리에서 나온다.

- resume 직후 `aidev watch` 를 띄우면, 새 run 의 `live.json` 이 아직 없는 찰나에는 후보가 **직전의 끝난 run** 뿐이다(`resolve_watch_target` 은 stale-running 만 건너뛰고, 끝난 run 은 정상 후보다 — `cli.py:424-432`). watch 는 옛 run 의 최종 리포트를 찍고 즉시 끝난다. 사용자에게는 "새 run 을 한 박자 늦게 문다"로 보인다.
- attempt 재시도/다음 스테이지로 넘어갈 때도 마찬가지다. 앞 run 이 끝나는 순간 watch 가 빠지므로, 한 슬라이스(plan→implement→diagnose→repair)를 끝까지 따라가지 못한다.

수리 방향은 요구사항의 문장 그대로다: **run 이 끝났을 때 곧바로 나가지 말고, 뒤이어 시작된 최신 run 이 있으면 그쪽으로 갈아탄다.** 명시적 run id 를 준 경우에는 지금 동작을 그대로 둔다(그 id 를 보라고 지목한 것이므로).

---

## 1. 바뀌는 파일

| 파일 | 무엇이 |
|---|---|
| `aidev/verify.py` | 개수를 `Optional[int]` 로 — 못 읽으면 `None`. `count_text` / `count_note` 표기 헬퍼 |
| `aidev/pipeline.py` | `Verify` 줄과 pass 메시지를 `n/a` 대응으로. `Changed files` 를 워크트리/repo 안으로 한정 |
| `aidev/progress.py` | `touched_paths` / `render` 에 `roots` 필터 (progress.md 의 같은 누수) |
| `aidev/storage.py` | `path_inside()` 공개 헬퍼 (`_normalised` 재사용) |
| `aidev/cli.py` | `cmd_watch` 에 후속 run 인계(handoff) |
| `pyproject.toml` | `addopts` 에서 `-q` 제거 (실효 `-qq` 해소) |
| `tests/test_verify.py` | 개수 표기 회귀 테스트 (fake 명령) |
| `tests/test_pipeline.py` | repo 밖 경로 제외 테스트 |
| `tests/test_watch.py` | 인계 테스트 |

`aidev/briefing.py`, `aidev/graph/**`, `desktop/**` 는 건드리지 않는다.

---

## 2. 항목별 상세

### 2-1. verify 개수 표기 (`aidev/verify.py`, `aidev/pipeline.py`, `pyproject.toml`)

**`VerifyResult`(`verify.py:100-144`)**

- `passed: Optional[int] = None`, `failed: Optional[int] = None`, `skipped: Optional[int] = None`.
  `None` = "러너의 출력이 말해주지 않았다". 0 = "정말 0개".
- `failure_count()`(`verify.py:118`)는 `self.failed or len(self.failures)` 이므로 `None or x → x` 로 **그대로 동작한다**. 손대지 않는다.
- `to_dict()`(`verify.py:133`)는 그대로 값을 싣는다 → state.json 에 `null` 이 들어간다. 읽는 쪽에서 `n/a` 로 표기.
  (구 state.json 은 `0` 을 갖고 있으므로 과거 슬라이스는 계속 0으로 보인다. 과거 기록이라 정정하지 않는다.)

**합산(`verify.py:196-198`)**

```python
result.passed = _add(result.passed, counts.get("passed"))
result.failed = _add(result.failed, _sum_present(counts, "failed", "error"))
result.skipped = _add(result.skipped, counts.get("skipped"))
```

`_add(total, seen)`: `seen is None → total`, 아니면 `seen + (total or 0)`.
`_sum_present(counts, *keys)`: 키가 하나도 없으면 `None`, 있으면 있는 것만 더한 값. `failed`/`error` 가 둘 다 없는 출력에서 `failed=0` 을 만들어내지 않기 위한 것.

`parse_output` 자체는 바꾸지 않는다 (반환 계약 그대로).

**표기 헬퍼 (`verify.py`, 렌더링 절에 추가)**

```python
def count_text(value: Optional[int]) -> str:
    """개수, 또는 출력이 말해주지 않았을 때의 'n/a'. 없는 수를 0으로 지어내지 않는다."""

def count_note(passed: Optional[int]) -> str:
    """'470 passed' 또는 'test count n/a' — 한 줄에 끼워 넣는 형태."""
```

**소비처 3곳**

1. `summarize_for_agent`(`verify.py:378-383`): `"passed {0}".format(count_text(result.passed))`. `failed`/`skipped` 는 지금처럼 참일 때만 덧붙인다(`None` 은 falsy 라 그대로 빠진다). → 기존 단언 `"passed 143" in summary`(`tests/test_verify.py:86`) 유지.
2. `render_failure_md`(`verify.py:496-502`): 세 값 모두 `count_text`. 간격 문자열은 그대로 두어 `"passed 143   failed 2   skipped 1"`(`tests/test_verify.py:110`) 유지.
3. `pipeline._verify_lines`(`pipeline.py:4013-4018`): `"Verify    {0}  exit {1}  ({2})".format(cmd, exit, verify.count_note(last.get("passed")))`.
   `pipeline.run_verify`(`pipeline.py:3393`): `say("verify passed ({0}, no session spent)".format(verify.count_note(result.passed)))`.

**`pyproject.toml`**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
# addopts 에 -q 를 두면 선언된 'python -m pytest -q' 와 합쳐져 -qq 가 되고,
# pytest 는 그 verbosity 에서 최종 요약줄을 아예 찍지 않는다. 계기판이 개수를
# 못 읽어 0으로 보고한 원인 (실측 2026-08-19). quiet 는 명령 쪽이 정한다.
```

`addopts` 줄만 지운다. `testpaths` 는 그대로.

### 2-2. repo 밖 경로 제외 (`aidev/storage.py`, `aidev/pipeline.py`, `aidev/progress.py`)

**`storage.py`** — `in_repo_scope`(`storage.py:532`) 옆에 공개 헬퍼 하나:

```python
def path_inside(path: Any, root: Any) -> bool:
    """이 경로가 root 자신이거나 그 아래인가. 상대 경로는 언제나 안쪽으로 친다."""
```

- 상대 경로(`aidev/verify.py`)는 세션의 cwd(=워크트리) 기준으로 기록된 것이므로 무조건 True.
- 절대 경로는 `_normalised`(resolve + normcase)로 양쪽을 정규화해 `target == root or target.startswith(root + os.sep)`. macOS 의 `/var` ↔ `/private/var`, 대소문자 차이가 여기서 흡수된다 — `in_repo_scope` 와 같은 규칙.
- root 가 빈 값이면 판정 불가 → True(모르는 것을 지우지 않는다).

**`pipeline.py`**

- `slice_roots(state)` 추가: `state["workspace"]["path"]`(워크트리)와 `state["repo"]` 를 리스트로. 둘 다 "이 슬라이스가 쓸 수 있는 곳"이다(비격리 슬라이스는 repo 에서 직접 작업하고, `.aidev/slices/<id>/` 도 repo 안에 있다). 없는 값은 빼고 반환.
- `split_by_root(paths, roots) -> Tuple[List[str], List[str]]` 추가: 안쪽 목록과 바깥 목록. `roots` 가 비면 전부 안쪽(동작 무변).
- `changed_files`(`pipeline.py:4103`)는 **그대로 둔다** (모든 경로를 모으는 역할). 분류는 `render_summary` 가 한다:

```python
changed, outside = split_by_root(changed_files(runs), slice_roots(state))
```

`Changed files` 블록(`pipeline.py:3941-3948`)은 안쪽만 세고 나열하고, 바깥이 있을 때만 들여쓴 한 줄을 덧붙인다:

```
Changed files (12)
  aidev/verify.py
  ...
  (+1 outside the workspace, not listed)
```

요구사항이 허용한 "별도 표시로 분리"의 최소 형태다. 목록에서는 빠지되(Done Criteria), 세션이 홈 디렉터리에 썼다는 사실 자체는 지워지지 않는다. 바깥이 0이면 이 줄은 없다 — 기존 출력과 완전히 동일.

**`progress.py`** — 같은 필터를 progress.md 의 "Done" 에도:

- `touched_paths(telemetry, roots: Sequence[Any] = ())`: `roots` 가 비면 지금과 같고, 있으면 `storage.path_inside` 로 거른다.
- `render(..., roots: Sequence[Any] = ())`: `touched_paths(telemetry, roots)` 로 넘긴다.
- `pipeline.write_progress`(`pipeline.py:2896-2909`)가 `roots=(cfg.cwd, cfg.repo)` 를 넘긴다.
- `remaining_paths` 는 손대지 않는다. plan 경로는 repo 상대 표기라 바깥 절대경로와는 애초에 매칭되지 않는다.
- `progress.py` 에 `from . import storage` 를 추가한다 (순환 없음: `storage` 는 패키지 내부를 import 하지 않는다).

### 2-3. watch 인계 (`aidev/cli.py`)

- 모듈 상수 `FOLLOW_LATEST = (None, "", "last", "latest", "-")` — `resolve_watch_target:418` 의 리터럴 튜플을 이것으로 바꿔 두 곳(대상 선택 / 인계 여부)이 어긋날 수 없게 한다.
- 상수 `HANDOFF_POLLS = 6` — 끝난 run 에게 후임을 넘겨줄 기회를 몇 번 볼 것인가. 폴링 간격이 0.3~0.5s 로 클램프(`cli.py:344`)되므로 약 2~3초. **실제 시간이 아니라 폴링 횟수**로 세는 이유는 테스트가 `cli._sleep` 를 무력화하기 때문이다(`tests/test_watch.py:24-27`).
- 새 헬퍼 두 개:

```python
def next_run(runs_root, run_dir, repo=None, worktree_root=None, now=None) -> Optional[Path]:
    """지금 보던 run 이 끝난 뒤 새로 시작된, 더 최신인 run. 없으면 None."""
    # find_running_run 으로 '살아있고 stale 이 아닌 최신 run' 을 얻고,
    # 같은 디렉터리이거나 이름이 더 앞서면(=더 오래된 run) None.
    # run id 는 타임스탬프로 시작하므로 이름 비교가 곧 시간 비교 (storage.list_run_dirs:769).

def await_next_run(runs_root, run_dir, repo, worktree_root, interval) -> Optional[Path]:
    """끝난 run 에서 후임으로 넘어갈 기회를 HANDOFF_POLLS 번까지 본다."""
```

- `cmd_watch` 는 기존 폴링 루프를 바깥 "따라갈 run" 루프로 한 겹 감싼다. 안쪽 루프는 지금 그대로(종료 상태 → break, stale → `stalled=True` 후 break, `--once` → return). break 후:

  - `args.run_id not in FOLLOW_LATEST` (명시적 id) → 지금처럼 최종 리포트를 찍고 끝. **기존 테스트는 전부 이 경로다** (`watch run-1`, `run-dead`, `run-old`, `run-empty`).
  - `--once` → 지금처럼 즉시 return.
  - 그 외(=`last` 모드) → `await_next_run(...)`. 새 run 이 있으면 `live.clear()` 후 `run_dir` 를 바꾸고 `payload=None`, `stalled=False` 로 되돌려 계속 따라간다. 없으면 지금처럼 최종 리포트/STALE 안내를 찍고 끝.

- `KeyboardInterrupt` 처리는 바깥 루프까지 감싸 그대로 130 을 반환한다.
- 워처는 여전히 읽기 전용이다(`tests/test_watch.py:399` 가 이를 못박고 있다). 새 코드도 run 디렉터리에 아무것도 쓰지 않는다.
- 최종 리포트를 찍기까지 최대 2~3초가 늦어진다. `last` 모드에서만이고, 그 대가로 슬라이스 전체를 끊기지 않고 따라간다.

---

## 3. 테스트

**`tests/test_verify.py`** (파싱/표기 절에 추가)

- `test_a_silent_pass_reports_no_count_rather_than_zero`
  `parse_output("")` → `counts == {}`; `run_commands` 로 아무것도 찍지 않고 exit 0 하는 스크립트를 돌려 `result.passed is None`, `summarize_for_agent(...)` 에 `"passed n/a"` 가 있고 `"passed 0"` 은 없다.
- `test_the_summary_shows_the_real_test_count` (fake 검증 — Done Criteria 1)
  `verify_script(tmp_path, "print('470 passed in 161.90s')\n")` 를 `test_commands` 로 선언해 파이프라인을 돌리고, 요약의 `Verify` 줄에 `470 passed` 가 있음을 단언.
- `test_a_pass_with_no_countable_output_says_n_a`
  아무것도 찍지 않고 exit 0 하는 스크립트 → `Verify` 줄에 `test count n/a`, 출력 어디에도 `0 passed` 없음.
- 기존 `test_the_summary_shows_the_engine_run`(`tests/test_verify.py:345`)은 그대로 통과해야 한다(`148 passed` 를 찍는 스크립트).

**`tests/test_pipeline.py`**

- `test_changed_files_leaves_out_paths_outside_the_workspace` (Done Criteria 2)
  `runs.json` 형태의 항목 두 개로 가짜 `telemetry.json` 을 만들어(`observed.file_accesses`), 하나는 워크트리 안의 절대경로, 하나는 `tmp_path/"home"/".claude"/...`/`MEMORY.md`. `state` 에 `workspace.path` 와 `repo` 를 넣고 `render_summary` 를 호출 → `MEMORY.md` 가 목록에 없고, `(+1 outside the workspace` 줄이 있고, 안쪽 파일은 남는다.
- `test_progress_leaves_out_paths_outside_the_workspace`
  `progress.render(..., roots=[repo])` 에 바깥 절대경로를 섞어 `- edited .../MEMORY.md` 가 없음을 단언. 기존 `test_progress_renders_what_was_done_and_what_is_left`(`:2488`)는 `roots` 없이 호출하므로 상대경로가 그대로 남아야 한다 — 무변경 보증.

**`tests/test_watch.py`** (watch loop 절에 추가)

- `test_watch_follows_the_run_that_starts_after_this_one`
  `runs/20260819-100000-a` 를 `status="completed"` 로 만든다. `cli._sleep` 을 가짜로 바꿔 첫 호출에서 `runs/20260819-100100-b` 를 `running` 으로 만들고, 그다음 호출에서 `b` 를 `completed` 로 바꾼다. `main([... "watch"])`(id 없음) → 0, 출력에 `b` 의 run id 가 있다.
- `test_an_explicit_run_id_is_never_handed_over`
  같은 배치에서 `watch 20260819-100000-a` 로 부르면 `a` 의 종료로 끝나고 `b` 로 넘어가지 않는다.
- `next_run` 단위 테스트: 같은 run 은 None, 더 오래된 running 은 None, stale-running 은 None(`find_running_run` 이 이미 거른다), 더 최신 running 이면 그 경로.

---

## 4. 검증

implement 단계가 실제로 실행할 수 있는 것은 `aidev verify` 와 `python -m aidev.specs` 뿐이다. 그 안에서 다음 순서로 확인한다.

1. `python -m aidev.specs` — 명세 규약. 새 함수(`count_text`, `count_note`, `_add`, `_sum_present`, `path_inside`, `slice_roots`, `split_by_root`, `next_run`, `await_next_run`)는 한 줄 설명 + `@param` + 분기가 있으면 `@flow` 를 갖춰야 하고, **본문을 고친 기존 함수는 docstring 도 같이 고쳐야 한다**(`KIND_UNCHANGED` 위반). 대상: `run_commands`, `summarize_for_agent`, `render_failure_md`, `_verify_lines`, `run_verify`, `render_summary`, `write_progress`, `touched_paths`, `progress.render`, `cmd_watch`, `resolve_watch_target`. 테스트 파일은 규약 면제(`specs.is_exempt`).
2. `aidev verify` — 선언 명령 `python -m pytest -q` 가 그대로 돈다. 전량 통과가 Done Criteria 3.
3. **같은 출력의 `passed ...` 줄이 §0 가설의 판정이다.**
   - `passed 470` (또는 실제 개수) → `addopts` 가설 확인. 그대로 둔다.
   - `passed n/a` → 요약줄이 여전히 안 나온다는 뜻이니 `-qq` 가설이 틀렸다. `pyproject.toml` 변경을 되돌리고, 표기 계층(a)만 남긴 채 "요약줄이 나오지 않는 원인은 addopts 가 아니었다"를 보고한다. 어느 쪽이든 "0 오보 금지"는 충족된다.
   - `passed 0` → 회귀. 표기 계층이 안 걸린 것이므로 고친다.

---

## 5. 위험과 대비

- **`passed` 의 타입이 `int` → `Optional[int]` 로 바뀐다.** 읽는 곳은 `verify.py`(378, 499) 와 `pipeline.py`(3393, 4016) 뿐이다(`grep passed`). `desktop/` 은 목데이터라 state.json 을 읽지 않고, `storage.py` 스키마에도 이 필드는 없다. `failure_count()` 는 `None or x` 덕에 무변경으로 동작한다 — **바꾸지 말 것**.
- **`addopts` 제거로 pytest 출력이 늘어난다.** 로그 파일이 커지지만 세션에 들어가는 것은 요약뿐이다(다이어트의 요지). 맨손 `pytest` 는 기본 verbosity 가 된다 — 선언 명령이 `-q` 를 갖고 있으므로 파이프라인 쪽은 그대로 quiet.
- **`_COUNT_RE` 가 출력 전체를 훑는다**는 성질은 그대로다. 실패한 테스트가 캡처한 텍스트에 `143 passed` 같은 문자열이 있으면 개수가 부풀 수 있다 — 기존 성질이고 이번 범위 밖이다. 고치지 않되, 이 계획이 그것을 알고 남겼음을 남긴다.
- **경로 필터가 과하게 자를 위험.** 상대 경로는 무조건 통과시키고, root 를 워크트리와 repo 둘 다로 잡고, 판정 불가면 남긴다. 세 겹 모두 "모르면 지우지 않는다" 쪽으로 기울여 두었다. `tests/test_pipeline.py:2488` 의 상대경로 케이스가 이 규칙의 보루다.
- **watch 인계가 기존 동작을 바꿀 위험.** 명시적 id 와 `--once` 를 인계에서 제외하면 `tests/test_watch.py` 의 루프 테스트 6개가 전부 기존 경로에 남는다. 새 동작은 id 없는 `last` 모드에서만 나타난다.
- **watch 가 무한히 갈아탈 위험.** 후임은 "살아 있고(stale 아님), 이름이 더 최신인" run 으로만 인정한다. 새 run 이 계속 생긴다는 것은 파이프라인이 실제로 돌고 있다는 뜻이므로 따라가는 것이 옳다.
- **가설 (b)가 틀릴 위험**은 §4-3 에 되돌림 경로를 박아 두었다. 표기 계층 (a)는 가설과 무관하게 요구사항을 만족한다.

## 6. 하지 않는 것

신기능 없음. `aidev/briefing.py`, `aidev/graph/**`, `desktop/**` 무변경. 요약 표는 넓히지 않는다(열 추가 없이 들여쓴 한 줄만). `parse_output` 의 반환 계약, `resolve_watch_target` 의 선택 순서, `changed_files` 의 시그니처는 그대로 둔다.
