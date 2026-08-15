# v0.4 — Epic → Slice Planner + 순차 큐 구현 계획

## 0. 요구사항이 명시적으로 요구한 결정: N+1의 base

세 안을 실제 코드 기준으로 비교한다. 기준은 `aidev/workspace.py`의 `plan_workspace()` / `merge_branch()`와 README가 못박은 원칙 *"사람의 merge만이 결과를 진짜 이력으로 만든다"* 이다.

| | (a) 모두 같은 base | (b) N의 브랜치 위에 N+1 | (c) N을 auto-merge 후 N+1 |
| --- | --- | --- | --- |
| 의존 slice가 선행 결과를 본다 | **못 본다.** slice 2의 worktree에 slice 1의 코드가 없다. 요구사항이 "B는 A의 결과 위에"를 전제하므로 plan/implement가 존재하지 않는 코드를 상대로 돈다 | 본다. worktree가 N의 tip에서 생성됨 | 본다 |
| merge 충돌 | N개 브랜치가 같은 파일을 건드리면 사람이 merge할 때마다 충돌 | 사슬이라 base 대비 선형. tip 하나만 merge해도 전부 들어온다 | 없음 |
| 사람 merge 원칙 | 지킴 | 지킴 | **깨짐.** 무인 루프가 base를 움직인다 |
| 실패 slice 격리 | 좋음 | tip부터 거꾸로 discard하면 유지됨. 중간 것만 버리려면 사람 손 | auto-merge된 건 되돌리기 어렵다 |
| 비용 | worktree N개 | worktree N개 (동일) | worktree N개 + merge N회 |

**결론: (b) 채택.** 단 (b)를 그대로 쓰면 slice N+1의 `Workspace.base`가 `slice/<N>`이 되어 `merge_slice()`가 *"repo가 base('slice/<N>')를 체크아웃하고 있지 않다"* 며 사람에게 남의 브랜치를 체크아웃하라고 요구한다(pipeline.py:2214-2221). 그래서 **"어디서 갈라지나"와 "어디로 merge되나"를 분리한다**:

- `Workspace.base` = **에픽의 base 브랜치** (merge 대상, 모든 slice 동일)
- `Workspace.start` = **분기 지점** (slice 1 = 에픽 시작 시점에 고정한 `base_commit`, slice N+1 = `slice/<N-slice-id>`)

merge는 그대로 base 브랜치로 간다. 사슬이므로 앞 slice들은 조상이고, 사람이 순서대로 merge하든 tip 하나만 merge하든 결과가 같다. (c)는 채택하지 않으므로 원칙 조화 방안도 불필요하다.

---

## 1. 파일별 변경

### 1.1 `aidev/telemetry.py`
- `PHASES`에 `"decompose"` 추가 (끝에 append). 위치 의존 코드 없음(`reporter.render_phase_stats`는 DB 행을 그대로 찍는다). 이걸로 `aidev stats`가 분해 비용을 독립 항목으로 집계한다. `test_stages_are_real_telemetry_phases`의 `set(STAGES) <= set(PHASES)`는 계속 성립.

### 1.2 `aidev/workspace.py` — 분기 지점과 merge 대상 분리
- `WorkspacePlan`에 `start: Optional[str] = None` 추가.
- `Workspace`에 `start: Optional[str] = None` 추가 (`base_commit` 뒤, `created_at` 앞). `to_dict()`에 `"start"` 추가, `from_dict()`는 없으면 `None` (v0.3 state.json 그대로 읽힘 — 관용적 reader 규약 유지).
- `plan_workspace(repo, slice_id, branch, base=None, start=None, root=None)`:
  - `base`는 지금처럼 merge 대상 브랜치 이름 (기본 = `current_branch(repo)`).
  - `start`가 주어지면 `resolve_commit(repo, start)`로 검증하고 그 커밋을 `base_commit`으로 쓴다. 해석 실패 시 기존과 같은 `GitError`.
  - `start`가 없으면 지금 동작 그대로(`base` 또는 `HEAD`).
  - `WorkspacePlan.start = start`.
- `create()`가 `Workspace(..., start=plan.start)`를 채운다. `worktree_add`는 이미 `base_commit`을 시작점으로 쓰므로 그 외 변경 없음.
- `verify()` / `destroy()` / `merge_branch()` 무변경.

`test_workspace.py`는 전부 keyword 생성이라 필드 추가에 영향 없음.

### 1.3 `aidev/pipeline.py` — 재사용 가능하게 열기 (동작 변경 없음)

기존 단일 `--requirement` 경로의 **동작은 한 줄도 바꾸지 않는다.** 아래는 전부 "기본값이 곧 현재 동작"인 확장이다.

1. **`StageSpec`** (신규 dataclass, `execute_stage` 위):
   ```python
   @dataclass
   class StageSpec:
       """How one stage is run. The epic's 'decompose' is a stage that is not a
       slice stage, so what used to be looked up from STAGE_POLICY is passed in."""
       stage: str
       policy: Dict[str, Optional[str]]
       phase: str
       allowed: Tuple[str, ...] = ()
       prompt: Optional[str] = None   # fixed prompt; None = build_prompt per attempt

       @classmethod
       def for_slice(cls, stage: str, cfg: "PipelineConfig") -> "StageSpec":
           return cls(stage=stage,
                      policy=STAGE_POLICY.get(stage, STAGE_POLICY["implement"]),
                      phase=stage,
                      allowed=allowed_tools_for(stage, cfg))
   ```
   `STAGE_POLICY`에는 decompose를 **넣지 않는다** (`test_stages_are_real_telemetry_phases`의 `set(STAGE_POLICY) == set(STAGES)`가 깨진다).
2. **`execute_stage(cfg, rec, stage, prompt, attempt, resume_session=None, spec=None)`**: 첫 줄에서 `spec = spec or StageSpec.for_slice(stage, cfg)`. `policy`/`allowed_tools`/`phase`를 spec에서 읽도록 3줄 교체 (`run_cfg.phase`, `Telemetry(phase=...)`도 `spec.phase`).
3. **`run_stage(cfg, rec, state, stage, requirement, spec=None)`**: 동일하게 `spec` 기본값 처리. 루프 안 프롬프트 생성부를 `prompt = spec.prompt if spec.prompt else build_prompt(...)`로. 쿼터 대기·세션 정책·attempt 카운트는 **그대로** — 이걸로 decompose가 쿼터/세션 정책을 공짜로 상속한다.
4. **레코드 일반화**: `SliceRecord`에 `label` 프로퍼티(`= slice_id`) 추가. `SliceLock`은 `rec.dir`/`rec.label`만 쓰도록 바꾸고 docstring을 "one process per record"로. `await_approval(cfg, rec, state, stage, artifact="plan.md")`로 확장하고 `APPROVAL_TEMPLATE`의 `{slice_id}` → `{label}`, "You may edit plan.md" → `"You may edit {artifact}"`. (`test_plan_gate_blocks_until_approved`는 `"rejected: <reason>"` 부분문자열만 본다.)
5. **레코드 탐색 일반화**: `slice_touched_at` → `record_touched_at(rec)` (state 경로만 쓰므로 그대로 동작), `find_slice`의 exact→prefix→substring→`last` 로직을 `find_record(records, pattern, kind, root, hint)`로 빼고 `find_slice`는 그 얇은 래퍼로. 에러 문구는 현행 유지("no slices in ...", "matches N slices").
6. **`_plan_workspace(args, repo, name, branch=None, base=None, start=None)`**: `branch`는 기본 `SLICE_BRANCH_PREFIX + name`, `base`는 기본 `getattr(args, "base", None)`, `start`를 그대로 `workspace.plan_workspace`에 전달. 기존 호출부는 `_plan_workspace(args, repo, slice_id)`로 동일.
7. **`start_slice` 분해**:
   ```python
   @dataclass
   class SliceOutcome:
       code: int
       rec: SliceRecord
       state: Dict[str, Any]

   def launch_slice(args, repo, data_dir, text, name, *, base=None, start=None,
                    epic=None, quiet_unfinished=False) -> SliceOutcome
   ```
   `start_slice`가 하던 일 중 **파일 해석과 `--dry-run` 출력만 남기고** 나머지(front matter → gates/setup → slice_id → rec → workspace plan → blocked 거부 → requirement 기록 → `new_state` → `_open_workspace` → `_finish`)를 `launch_slice`로 이동. `epic`이 주어지면 `state["epic"] = {"epic_id": ..., "index": n}`, 그리고 "unfinished slice(s) here" 안내는 억제(큐 안에서는 틀린 조언).
   `start_slice`는 `return launch_slice(...).code`.
8. **`resume_slice` 분해**: `find_slice` + `--dry-run` 출력만 남기고, 그 뒤(status 판정 → front matter 재파싱 → `workspace.verify` → `_finish`)를 `continue_slice(args, repo, data_dir, rec) -> int`로 이동.
9. **`new_state(..., epic=None)`**: `epic`이 있으면 `state["epic"]`에 기록. `STATE_SCHEMA`는 **2 그대로 둔다** (`test_one_command_creates_the_workspace_and_finishes`가 `state["schema"] == 2`를 단언한다). 추가 키는 전부 optional이므로 관용적 reader 규약과 일관.
10. **`history_snapshot`**에 `"epic": state.get("epic")` 한 줄 추가 (브랜치 스냅샷에 어느 에픽의 몇 번째인지 남는다).
11. **`_workspace_lines`**: `ws.start`가 있고 `ws.base`와 다르면 `Branch  slice/x  (base: B @ sha, from slice/w)` 형태로 한 줄에 표시.
12. **프롬프트**: `run_stage`의 `build_prompt(..., base=...)` 인자를 `cfg.workspace.start or cfg.workspace.base`로. `_WORKSPACE_NOTE`의 "created from {base}"가 사슬에서 선행 slice 브랜치를 가리키게 된다.
13. **`add_parser`**: 
    - `--epic PATH` (에픽 markdown), `--resume-epic EPIC` 추가.
    - `--list` help를 "slices and epics of --repo"로.
14. **`_dispatch`**:
    - `modes` 튜플에 `("epic", "--epic")`, `("resume_epic", "--resume-epic")` 추가 → 조합 사용은 기존과 같은 usage 에러.
    - `--epic` + `--no-worktree` → `PipelineError("--epic needs worktrees: slice N+1 branches from slice N")` (exit 2).
    - `--list`는 `epic.list_epics(repo)` 후 `list_slices(...)`.
    - `--epic` / `--resume-epic` → `epic.start_epic(...)` / `epic.resume_epic(...)`.
    - 순환 참조 회피: `_dispatch` **함수 안**에서 `from . import epic` (epic이 pipeline 위에 세워진 층이라 반대 방향 import는 불가). 주석으로 이유를 남긴다.

### 1.4 `aidev/epic.py` (신규, ~450줄)

```python
"""Epic -> slice list -> a sequential queue of v0.3 slices.

An epic is decomposed once, the list is approved by a human once, and then each
item runs as an ordinary slice. Nothing here re-implements the slice loop: the
queue calls pipeline.launch_slice()/continue_slice() and the whole v0.3 flow
(worktree, requirement commit, gates, quota waits, stage commits) happens
unchanged inside it.

    <repo>/.aidev/epics/<epic-id>/
        epic.md          the original, copied in
        slices.md        what decompose produced (a human edits it before approving)
        slices/01-x.md   one executable requirement per item, cut from slices.md
        state.json       authoritative epic progress, single writer: this process
        approvals/decompose.md
        runs.json        the decompose runs
"""
```

**상수**
```python
EPICS_DIRNAME = "epics"
EPIC_BRANCH_PREFIX = "epic/"
DECOMPOSE = "decompose"
EPIC_STATE_SCHEMA = 1
SLICES_FILENAME = "slices.md"
DECOMPOSE_POLICY = {"safety_profile": "readonly", "permission_mode": None}
```

**`EpicRecord`** — `SliceRecord`와 같은 모양(pipeline의 `await_approval`/`SliceLock`/`run_stage`/`execute_stage`/`record_run`/`resume_quota_wait`가 그대로 먹도록):
`dir`, `requirement_path`(=`epic.md`), `slices_path`, `slices_dir`, `state_path`, `runs_path`, `approvals_dir`, `approval_path(stage)`, `label`(=`epic_id`), `slug`, `ensure()`, `read_state()`, `write_state()`, `append_run()`, `runs()`, `write_slices(text)`(fence 제거 후 `write_text_atomic`), `read_slices()`.

**epic state.json** (slice 규약 상속: 단일 writer, atomic, 관용적 read)
```json
{"schema": 1, "epic_id": "...", "status": "running:decompose|waiting_approval:decompose|running:slice|quota_wait|done|failed|rejected",
 "repo": "...", "base": "windows-handoff-20260808", "base_commit": "abc123...",
 "gates": ["decompose"], "stage_order": ["decompose"],
 "stages": {"decompose": {"status": "pending"}},
 "quota": {"waiting": false, "resume_at": null, "retries": 0},
 "workspace": {...},           // 분해용 worktree, 승인 직후 제거되며 키도 지워진다
 "current": 2,
 "slices": [{"index": 1, "title": "...", "name": "...", "requirement": ".aidev/epics/<id>/slices/01-x.md",
             "slice_id": "...", "branch": "slice/...", "start": "...", "status": "done"}],
 "created_at": "...", "updated_at": "..."}
```
`slices[].status`는 **투영**이다 — 매번 해당 slice의 `state.json`을 읽어 갱신하며, authoritative source는 계속 slice 쪽이다(`history_snapshot`과 같은 철학).

**decompose 프롬프트** (`_DECOMPOSE_PROMPT`, 요지):
- readonly 선언(파일 생성·수정·명령 금지, 우회 금지) — plan 프롬프트와 동일 문구.
- 실제 코드를 읽고 쓸 것.
- **턴 예산**: "각 slice의 *각 단계*가 `{max_turns}`턴 안에 끝나야 한다. 실측으로 대형 slice 2건이 81턴에 죽었다. 판단이 서지 않으면 더 작게 잘라라."
- **순서 = 의존 순서**: "N+1은 N의 결과가 이미 들어있는 worktree에서 시작한다. 뒤가 앞에 의존할 수는 있어도 그 반대는 안 된다. 각 항목은 자기 텍스트만 보고 실행되므로 선행 slice가 무엇을 끝냈는지 산문으로 적어라."
- **형식**(그대로 제시, 최종 메시지 전체가 verbatim으로 slices.md가 된다고 명시):
  ```
  === SLICE 1: <제목> ===
  ---
  approval: plan
  ---
  # <제목>

  ## 요구 동작
  ## Scope
  ## 테스트
  ## 하지 않는 것

  === SLICE 2: <제목> ===
  ```
- front matter 키는 `approval:`(단계명 또는 `none`)과 `setup:`(셸 연산자 없는 단일 명령)뿐이며 모르면 줄을 빼라.
- 첫 마커 앞 텍스트는 메모로 보존되고 실행되지 않는다.
- 에픽이 쓰인 언어로 본문을 쓸 것.
- (주의: 이 프롬프트에 `PLAN STAGE`/`TEST STAGE` 문자열이 들어가지 않게 쓴다 — 테스트 스텁의 단계 판별이 그 문자열을 본다.)

**파서**
```python
_MARKER_RE = re.compile(r"^===[ \t]*SLICE[ \t]*(\d+)?[ \t]*:?[ \t]*(.*?)[ \t]*===[ \t]*$",
                        re.MULTILINE | re.IGNORECASE)

@dataclass
class SliceItem:
    index: int      # 파일 안의 위치가 곧 실행 순서. 마커의 숫자는 참고용이라 다시 매긴다
    title: str
    text: str       # front matter 포함, 그 자체로 requirement

def parse_slices(text) -> Tuple[str, List[SliceItem]]   # (notes, items)
```
- 코드펜스로 문서 전체를 감싼 경우 앞뒤 펜스 한 줄씩 제거(실측되는 LLM 습관).
- 각 항목 검증: 본문 비어있지 않을 것, `pipeline.parse_front_matter` → `resolve_gates`/`resolve_setup`이 예외를 내지 않을 것. 실패 시 `PipelineError("slices.md, slice 2 ('제목'): <원문 에러>")`로 **승인 직후·큐 시작 전에** 전부 검증한다.
- 제목: 마커의 제목 → 없으면 본문 첫 `# heading` → 없으면 `slice N`.
- 항목 0개면 `PipelineError`로 "slices.md에서 `=== SLICE n: ... ===` 마커를 찾지 못했다. 고친 뒤 `--resume-epic <id>`"를 안내.

**slice 이름/파일**
```python
head = slugify(erec.slug)[:20].strip("-")
name = "{0}-{1:02d}-{2}".format(head, item.index, slugify(item.title)).rstrip("-")
path = erec.slices_dir / "{0:02d}-{1}.md".format(item.index, slugify(item.title) or "slice")
```
`make_slice_id`가 40자로 자르지만 인덱스가 앞쪽 23자 안에 있어 항상 살아남고, 충돌은 기존 `-2` 접미사 규칙이 처리한다. Windows 경로 길이도 v0.3과 같은 범위에 머문다.

**`run_epic(args, cfg, erec, state, epic_body) -> int`**
```
pipeline.resume_quota_wait(cfg, erec, state)            # 그대로 재사용
1) decompose
   if stages.decompose != done:
       reason = pipeline.workspace_dirty_reason(cfg.cwd, DECOMPOSE) -> fail
       before = pipeline.repo_changes(cfg.cwd, include_slice_files=True)
       run = pipeline.run_stage(cfg, erec, state, DECOMPOSE, epic_body, spec=_decompose_spec(cfg, epic_body))
       실패/쿼터소진 -> fail_epic
       run.text 비었으면 fail
       observed.safety_violations 있으면 fail (기록도 남김)
       after != before -> fail("decompose stage changed the repository despite the readonly profile")
       erec.write_slices(run.text); stages.decompose = done
2) 게이트 (무조건, 에픽 front matter의 approval:은 여기에 영향 없음)
   decision = pipeline.await_approval(cfg, erec, state, DECOMPOSE, artifact=SLICES_FILENAME)
   rejected -> status=rejected, worktree 파기, exit 3
3) 승인됐으므로 분해용 worktree는 더 볼 일이 없다 -> destroy + state["workspace"] 제거
   (실패해도 say()로 경고만; 큐를 죽이지 않는다)
4) 목록 확정: parse_slices(erec.read_slices()) -> reconcile_items(state, items) -> materialize
5) for entry in state["slices"]:
       if entry.status == done: continue
       state["current"] = entry.index; set_status(erec, state, "running:slice")
       code = _run_entry(...)          # launch or continue
       entry.status = 해당 slice state.json의 status (투영)
       if code != EXIT_DONE: fail_epic(...) 후 그 code 반환
6) status = done; exit 0
```

**`reconcile_items(state, items)`** — 요구사항의 "실패 시 사람이 slices.md 수정 후 재개"를 성립시키는 규칙:
- 이미 시작된 항목(`slice_id` 보유)은 위치 기준으로 **그대로 유지**한다. 그 requirement는 이미 브랜치에 커밋돼 있어서 지금 바꾸면 거짓말이 된다.
- 시작되지 않은 첫 위치부터 끝까지는 **현재 slices.md로 통째 교체**하고, 바뀌었으면 `say("slices.md changed: pending items 2..3 replaced by 2..4")`로 알린다.
- 새 목록이 이미 시작된 개수보다 짧으면 `PipelineError`로 거부(무엇이 이미 돌았는지 알려주고 사람이 결정하게 한다).
- 시작됐지만 해당 slice가 `discarded`인 항목은 "시작되지 않음"으로 되돌려 현재 slices.md로 다시 만든다(사람이 slice를 버리고 다시 쓰는 탈출구).

**`_run_entry`**
```python
if entry.get("slice_id"):
    rec = pipeline.SliceRecord(pipeline.slices_root(repo), entry["slice_id"])
    code = pipeline.continue_slice(args, repo, data_dir, rec)
else:
    start = _start_ref(state, entry)     # 1번: state["base_commit"], 그 외: 앞 항목의 branch
    outcome = pipeline.launch_slice(args, repo, data_dir, text=Path(entry["requirement"]).read_text(),
                                    name=entry["name"], base=state["base"], start=start,
                                    epic={"epic_id": erec.epic_id, "index": entry["index"]},
                                    quiet_unfinished=True)
    entry.update(slice_id=..., branch=..., start=start); code = outcome.code
```
- `_start_ref`: 앞 항목의 `branch`가 `workspace.branch_exists`로 확인되지 않으면 **base로 조용히 되돌리지 않고** 실패시킨다("slice 2 depends on slice/<1>, which no longer exists; 조용히 base로 분기하면 의존이 사라진다").
- `pipeline.PipelineError`는 여기서 잡아 epic state에 이유를 기록하고 `exc.code`를 반환(프로세스 전체를 예외로 끝내지 않는다).
- **쿼터 대기와 slice 내부 게이트는 아무 처리도 하지 않는다.** 둘 다 `run_pipeline` 안에서 블로킹되고, 큐는 그 slice의 종료를 기다릴 뿐이다 — 요구사항 3의 두 항목이 여기서 저절로 성립한다.

**`start_epic(args, repo, data_dir)`**
- `--no-worktree` 거부, 에픽 파일 해석(`pipeline.resolve_requirement` 재사용), 빈 파일 거부.
- `epic_id = pipeline.make_slice_id(source.stem, epics_root(repo))`.
- `plan = pipeline._plan_workspace(args, repo, epic_id, branch=EPIC_BRANCH_PREFIX + epic_id)`.
- `--dry-run`: epic id / base / branch / worktree / epic dir / 게이트(decompose, 끌 수 없음) / stages 출력 후 종료, **아무것도 만들지 않는다**.
- `_refuse_blocked_workspace` → `erec.ensure()` → `epic.md` 기록 → `new_epic_state(...)`(`base`, `base_commit`를 여기서 고정) → `workspace.create` → `EpicLock` 안에서 `run_epic` → `render_epic_summary` 출력.
- 에픽 worktree에는 **커밋을 만들지 않는다**(`commit_stage` 호출 없음). base_commit에서 뜬 읽기 전용 체크아웃이고, 승인 시점에 통째로 사라진다.

**`resume_epic(args, repo, data_dir)`**
- `find_epic`(= `pipeline.find_record`, `last`/prefix/부분일치, 모호하면 거부) → state 읽기 → `done`이면 요약만 출력하고 0 → `workspace`가 남아 있으면 `workspace.verify`로 검증(없어졌으면 v0.3와 같은 문구로 거부하되, decompose가 이미 done이면 worktree 없이 그냥 진행) → `EpicLock` → `run_epic`.

**`list_epics(repo) -> None`** — 에픽이 하나도 없으면 **아무것도 출력하지 않는다**(`test_list_survives_unknown_stage_keys`가 stdout의 *모든* 줄에 대해 `" " in line[30:36]`을 검사하므로 빈 줄/자유형식 줄을 추가하면 안 된다). 있으면 `pipeline._list_row`를 같은 폭 `(34, 26, 34)`로 재사용:
```
EPIC                              STATUS                    SLICES                        UPDATED
20260815-v0-4-planner             running:slice 2/3         1=done 2=running 3=pending    2026-08-15T21:04
```
각 slice 상태는 해당 slice의 `state.json`을 직접 읽어 표시하고(살아있는 값 우선), 못 읽으면 에픽 state의 투영값을 쓴다.

**`render_epic_summary(state)`** — 인덱스 / slice id / 상태 / 턴 / 비용(각 slice의 `runs.json` 합계, `reporter.format_cost`·`format_tokens` 재사용) 표 + `Status` / `Reason` / 재개 명령 / 순서대로 칠 `--merge` 명령 목록.

**실패 출력** (`fail_epic`)
```
[pipeline] EPIC FAILED - slice 2 (20260815-v0-4-planner-02-queue) failed: stage 'test' reported failing tests (TEST_RESULT: FAIL)
[pipeline]   fix it, then:  aidev pipeline --repo <repo> --resume-epic 20260815-v0-4-planner
[pipeline]   the remaining list can be rewritten first: <repo>/.aidev/epics/<id>/slices.md
```

### 1.5 `tests/fake_pipeline_claude.py`
- `detect_stage()`가 `"DECOMPOSE STAGE"`를 **먼저** 검사하도록.
- `decompose_report()`: `AIDEV_FAKE_SLICES`(기본 2)개의 `=== SLICE n: fake slice n ===` 섹션 생성. front matter는 `AIDEV_FAKE_SLICE_APPROVAL`(기본 `none`)을 넣어, 큐 완주 테스트는 무인으로, 게이트 테스트는 `plan`으로 돌린다.
- `AIDEV_FAKE_FAIL_AFTER`: 설정되면 `previous >= N`인 호출부터 `fail` 모드처럼 동작 → "중간 slice 실패"를 결정적으로 재현.
- 나머지 모드(`quota`/`touch`/`forge`/`requires_approval`)는 그대로라 decompose에도 그대로 적용된다.

### 1.6 `tests/test_epic.py` (신규)
`tests/`에 `__init__.py`가 없고 pytest 기본 import 모드가 prepend라 `tests/`가 `sys.path`에 들어간다. 따라서 **test_pipeline.py를 손대지 않고** 픽스처를 재사용한다(기존 테스트 전량 통과를 구조적으로 보장):
```python
from test_pipeline import (BASE_BRANCH, argv, branches, claude_bin, git, git_repo,  # noqa: F401
                           invocations, log, repo, slice_dir, state_of, subjects, worktree)
```
테스트 목록:
1. `test_epic_decomposes_approves_and_runs_two_slices` — 게이트 승인 후 완주. 호출 7회(decompose 1 + slice 3 + slice 3), 두 slice 모두 `done`, 에픽 `done`, `slices/01-*.md`/`02-*.md` 생성, decompose가 **에픽 worktree**에서 돌았고 승인 후 그 worktree/브랜치가 사라졌음.
2. `test_slice_two_starts_on_top_of_slice_one` — `AIDEV_FAKE_FILES=1`. slice 1의 tip이 slice 2 브랜치의 조상이고, slice 2 worktree에 slice 1이 만든 `generated-0.txt`가 있으며, **두 slice의 `workspace.base`가 모두 `BASE_BRANCH`**임(merge 대상은 사슬이 아니라 base).
3. `test_the_list_gate_cannot_be_turned_off` — 에픽 front matter가 `approval: none`이어도 `waiting_approval:decompose`를 거쳐야 함.
4. `test_a_human_edit_to_slices_md_is_what_runs` — 게이트 중 slices.md를 1개짜리로 다시 씀 → 1개만 실행되고 그 requirement에 사람이 쓴 문구가 들어감.
5. `test_a_failed_slice_stops_the_queue_and_resume_epic_continues` — `AIDEV_FAKE_FAIL_AFTER=4` → exit 1, 에픽 `failed`, reason에 slice 2, 3번 항목 `pending`, 출력에 `--resume-epic`. 해제 후 `--resume-epic` → 0, slice 1은 재실행되지 않음.
6. `test_an_in_slice_gate_works_inside_the_queue` — `AIDEV_FAKE_SLICE_APPROVAL=plan`. `_sleep` 훅이 에픽 게이트와 각 slice의 plan 게이트를 차례로 승인, 도중 slice state가 `waiting_approval:plan`이었음을 관측.
7. `test_a_quota_wait_does_not_break_the_queue` — `AIDEV_FAKE_MODE=quota`, `QUOTA_FAILS=1` → decompose가 대기 후 재개, 에픽 완주, `state["quota"]["retries"] == 1`.
8. `test_decompose_that_writes_a_file_fails_the_epic` — `AIDEV_FAKE_MODE=forge` → 에픽 `failed`, reason에 `readonly`, slice 0개, 위조 파일은 에픽 worktree 안에만 있고 본진 승인 파일은 없음.
9. `test_unparsable_slices_md_fails_with_a_way_out` — `AIDEV_FAKE_TEXT="no markers here"` → 승인 후 실패, 메시지에 slices.md 경로와 `--resume-epic`. 유효한 slices.md를 써 넣고 재개하면 완주.
10. `test_list_shows_epic_progress` — capsys에 epic id와 `1=done` 류 표시.
11. `test_epic_dry_run_touches_nothing` — `.aidev/epics`·worktree·`epic/` 브랜치 모두 없음.
12. `test_epic_usage_errors` — `--epic --requirement` 조합(exit 2), `--epic --no-worktree`(exit 2), 없는 파일(exit 2), `--resume-epic nothing`(exit 2).
13. `test_parse_slices_unit` — 번호/대소문자/코드펜스 관용, 순서 재매김, 마커 없음·잘못된 front matter가 `PipelineError`.
14. `test_epic_slice_records_its_epic` — slice state의 `epic`과 브랜치의 `.aidev/history/<id>/slice.json`의 `epic` 키.

### 1.7 문서/버전
- `README.md`: 
  - 헤더/로드맵을 v0.4 완료로 갱신.
  - **"Epic → Slice Planner (v0.4)"** 절 신설: 명령(`--epic`, `--resume-epic`), 흐름 다이어그램(에픽 → decompose(readonly, 전용 worktree) → 목록 게이트(끌 수 없음) → 순차 큐 → 사람 merge), slices.md 형식 예시, 턴 예산 기준, **base 결정 표(위 0장 그대로)**, 실패/재개 규약, `.aidev/epics/<id>/` 저장 구조, epic state 키, 옵션 표에 `--epic`/`--resume-epic` 행 추가, `--list`가 에픽도 보여준다는 문장.
  - "안전핀"에 3줄 추가: 목록 게이트는 `approval: none`으로도 못 끈다 / decompose도 readonly + 사후 git 검증 / 큐는 조용히 base로 되돌아가지 않는다.
- `aidev/__init__.py`: docstring에 v0.4 한 문단, `__version__ = "0.4.0"`.
- `pyproject.toml`: `version = "0.4.0"`, description에 에픽 큐 한 구절.

---

## 2. 검증

```powershell
cd C:\Users\minsa\ai-dev-orchestrator-slices\20260815-v0-4-planner
python -m pytest -q                          # 전량 (기존 + test_epic.py)
python -m pytest -q tests\test_pipeline.py   # 기존 단일 경로 무손상 확인
python -m pytest -q tests\test_epic.py
python -m pytest -q tests\test_workspace.py  # plan_workspace/Workspace 필드 추가 회귀
```
- 모든 신규 테스트가 `tests/fake_pipeline_claude.py`로만 돌아 API 비용 0, git이 없으면 기존과 같이 skip.
- Windows 완주: 짧은 `--worktree-root`(tmp_path/wt)를 그대로 쓰고, 새로 만드는 경로는 `<wt>/<epic-id>` 하나뿐이다. 에픽 worktree 제거 실패는 경고로만 처리하므로 파일 잠금이 테스트를 깨지 않는다.
- 실전(Done Criteria의 "조커의 실제 에픽"): 이 slice 안에서는 fake 기반까지만 자동 검증 가능하다. implement 단계는 `aidev pipeline --repo <joker> --epic <에픽.md> --dry-run`까지 실행해 경로/base/브랜치 계획이 실제 repo에서 성립함을 확인하고, 실제 API 완주는 사람이 merge 전에 돌리는 항목으로 RESULT에 남긴다.

---

## 3. 무엇이 잘못될 수 있나

| 위험 | 대응 |
| --- | --- |
| 모델의 slices.md가 형식을 벗어남 | 프롬프트에 형식을 리터럴로 제시 + 파서가 번호/대소문자/코드펜스에 관용적 + 0개면 사람이 고칠 경로와 `--resume-epic`을 찍고 실패. **게이트가 바로 이 실패를 사람 손에 넘기는 장치다** |
| `execute_stage`/`run_stage` 시그니처 변경이 기존 40여 테스트를 깸 | 추가 인자는 전부 기본값이 곧 현행 동작(`StageSpec.for_slice`). `STAGE_POLICY`/`STAGES`/`STATE_SCHEMA`는 **건드리지 않는다**(각각을 단언하는 기존 테스트가 있다) |
| decompose를 본진에서 돌리면 README의 "claude가 본진에서 도는 일은 없다"가 깨지고, 사람이 동시 편집하면 readonly 사후 검증이 오탐 | 에픽 전용 worktree에서 돌린다. 깨끗한 기준선이 생겨 plan과 **동일한 강도**의 사후 검증이 가능하고, 분해가 읽는 코드도 사람의 WIP가 아닌 base 커밋이 된다 |
| 그 worktree가 잔해로 남음 | 승인 직후 파기(거부 시에도 파기). 중간에 프로세스가 죽으면 state.json에 기록된 채 남고 `--resume-epic`이 `workspace.verify`로 재사용 → 승인 시 정리. 제거 실패는 경고만, 경로와 명령을 출력 |
| 사슬 중간 slice를 discard하면 뒤 slice의 base가 사라짐 | 큐가 앞 항목 브랜치의 존재를 확인하고, 없으면 **조용히 base로 되돌아가지 않고** 실패시킨다(의존이 사라진 채 도는 것이 더 나쁘다). discard된 항목은 slices.md에서 다시 만들 수 있다 |
| slices.md를 사람이 고쳤을 때 이미 돈 slice와 어긋남 | 시작된 항목은 위치 기준 보존, 미시작 꼬리만 교체, 개수가 줄면 거부하고 무엇이 이미 돌았는지 보고 |
| 에픽 worktree + slice N개 worktree = 디스크/시간 | 에픽 worktree는 승인과 함께 사라진다. slice worktree는 v0.3와 동일한 비용이며 merge 후 `--discard`로 정리(README에 명시) |
| slice id가 길어져 Windows 경로 한계 | 이름을 `<epic-slug 20자>-NN-<title>`로 만들고 `make_slice_id`의 40자 컷이 적용돼 slice id는 49자 이하로 유지. 인덱스는 항상 앞쪽에 있어 잘리지 않는다 |
| 순환 import (pipeline ↔ epic) | epic만 pipeline을 top-level import. pipeline은 `_dispatch` 안에서 지역 import하고 이유를 주석으로 남긴다 |
| `--list` 출력에 줄을 추가하면 기존 테스트가 깨짐 | 에픽이 없으면 한 줄도 찍지 않고, 찍을 때는 기존 `_list_row`/같은 폭을 그대로 쓴다(빈 줄 금지) |
| 스텁의 단계 판별이 decompose 프롬프트를 plan으로 오인 | 스텁이 decompose를 먼저 검사하고, 프롬프트에 `plan stage`/`test stage` 문자열을 넣지 않는다 |
