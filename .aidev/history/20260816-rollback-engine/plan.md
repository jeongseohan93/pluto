# Rollback Engine — 구현 계획

되돌리기를 사람의 git 지식이 아니라 **커밋에 박힌 의미(slice/stage)** 로 수행한다.
두 개의 명령을 추가한다: 로컬 전용 slice 브랜치를 stage 커밋으로 되감는
`--rollback <id> --to <stage>`, 그리고 이미 base에 실린 merge를 되돌리는
`--revert-merge <id>`. 둘 다 실행 전 현재 지점과 복구 명령을 출력하고, 번복 이력을
`.aidev/slices/<id>/rollbacks.json`에 남긴다.

---

## 0. 이미 있는 것들 (읽고 확인한 사실)

- `state.json`의 `commits`는 **label → sha** 맵이다 (`pipeline.py:1856`, `commit_stage`).
  키는 `requirement` / `plan` / `implement` / `test` + amend 라벨 `amend1/implement`
  (`REQUIREMENT_STAGE`, `AMEND_LABEL`). 되감기 목표 커밋은 여기서 그대로 읽는다.
- stage별 상태는 `state["stages"][name]` (`stage_entry`, `pipeline.py:761`), 순서는
  `stage_order(state)` (`pipeline.py:842`).
- 브랜치/worktree/base는 `workspace.Workspace.from_dict(state["workspace"])`
  (`workspace.py:408`), 유효성은 `workspace.verify` (`workspace.py:542`).
- merge 결과는 `state["merge"] = {"status": "merged", "commit": ..., "base": ..., "branch": ...}`
  (`pipeline.py:2997`). revert 대상 커밋은 여기서 읽는다.
- merge가 본진에 쓰기 전에 거는 전제조건 4종(`--merge`가 이미 하는 것,
  `pipeline.py:2953-2973`): worktree 유효 / worktree clean / 본진이 base 체크아웃 /
  본진 tracked clean. `--revert-merge`도 본진에 커밋하므로 **같은 전제조건을 재사용**한다.
- 충돌 처리 관용: 충돌 파일 목록을 **abort 전에** 읽고, abort한 뒤 exit 4
  (`workspace.merge_branch`, `workspace.py:344`).
- 파괴적 명령의 출력 관용(`discard_slice`, `pipeline.py:3082-3087`): 무엇을 했는지 +
  "recoverable for now: git -C … " 복구 명령. 롤백 출력은 이 스타일을 따른다.
- 한 slice 한 프로세스: `SliceLock(rec)` (`pipeline.py:691`). 두 새 명령 모두 잡는다.
- 테스트는 항상 `main(argv(...))`로 CLI를 통과한다 (`tests/test_pipeline.py:99`).
  Namespace를 직접 만드는 테스트는 없으므로 새 인자가 기존 테스트를 깨지 않는다.

---

## 1. `aidev/workspace.py` — git 프리미티브 4개 추가

이 모듈은 slice를 모르는 순수 git 계층이다. 그 규약을 지켜 **경로와 ref만** 받는다.

1. `is_ancestor(repo, maybe_ancestor, ref) -> bool` (`inspection` 절, `resolve_commit` 아래)
   - `git merge-base --is-ancestor <a> <b>`, returncode 0 → True (같은 커밋도 True).
   - 용도: (a) 되감기 목표가 실제로 현재 브랜치 tip의 조상인지, (b) `commits` 맵의 어떤
     라벨이 되감은 뒤에도 브랜치에 남는지, (c) merge 커밋이 아직 base에 실려 있는지.
2. `diff_paths(repo, a, b) -> List[str]` (`inspection` 절)
   - `git diff --name-only <a> <b>`, 실패하면 `[]`. 마이그레이션 경고의 입력.
3. `reset_hard(worktree, commit) -> None` (`mutation` 절, `commit_all` 아래)
   - `git reset --hard <commit>`. **untracked 파일은 지우지 않는다** — worktree의
     `node_modules` 같은 파생물이 살아남는 것이 의도다(주석으로 명시). 호출자가
     tracked clean을 먼저 확인한다.
4. `revert_commit(repo, commit, mainline=1) -> MergeResult` (`merge_branch` 바로 아래)
   - `git revert -m <mainline> --no-edit <commit>`.
   - `--no-verify`를 **주지 않는다**: 단계 커밋과 달리 이건 사람이 시킨 본진 커밋이고,
     merge와 같은 이유로 훅을 우회하지 않는다(README 155행의 원칙).
   - 실패 시 `merge_branch`와 같은 순서: `diff --name-only --diff-filter=U`로 충돌 파일을
     먼저 읽고 → `git revert --abort` → `MergeResult(ok=False, conflicts=..., detail=...)`.
   - 반환형은 `MergeResult`를 재사용한다(모양이 동일하고 필드 의미가 같다). docstring에
     "merge와 같은 결과 모양"이라고 적는다.

---

## 2. `aidev/pipeline.py` — 본체

### 2.1 상수 (`STATUS_*` 블록, `pipeline.py:129-134`)

```python
STATUS_ROLLED_BACK = "rolled_back"   # stage 롤백 뒤: 중간 지점, resume 가능
STATUS_REVERTED = "reverted"         # base에서 철회됨
```
`STATE_SCHEMA`는 **2 그대로**다. 추가되는 키(`rollback`, `revert`, stage의 `rewound`)가
전부 optional이므로 v0.3/v0.4 reader가 계속 읽는다 — amend가 같은 판단을 한 근거와 동일.

`ROLLBACKS_FILENAME = "rollbacks.json"` 도 `STATE_FILENAME` 옆에 둔다.

### 2.2 `SliceRecord`에 번복 원장 (`pipeline.py:549-634`, `append_run` 옆)

```python
@property
def rollbacks_path(self) -> Path: return self.dir / ROLLBACKS_FILENAME
def append_rollback(self, entry) -> None   # runs.json과 같은 read-append-write_json_atomic
def rollbacks(self) -> List[Dict[str, Any]]
```
파일 모양: `{"slice_id": ..., "rollbacks": [ ... ]}` — `runs.json`과 동일한 규약이라
기존 reader 습관이 그대로 통한다. 항목:

```json
{"n": 1, "kind": "stage", "at": "...+09:00", "target": "plan",
 "branch": "slice/<id>", "from": "<이전 tip sha>", "to": "<목표 sha>",
 "dropped_commits": {"implement": "...", "test": "..."},
 "rewound_stages": ["implement", "test"],
 "migrations": ["backend/migrations/003_add_seat.sql"],
 "reason": "테스트가 헛돌아서" 또는 null,
 "undo": "git -C <worktree> reset --hard <from>"}
```
`kind: "revert-merge"`면 `target`/`rewound_stages`는 없고 `base`, `merge_commit`,
`to`(= revert 커밋)가 대신 들어간다.

`state.json`에는 **가장 최근 한 건의 요약**만 `state["rollback"] = {"n","kind","target","at","from","to"}`
로 둔다(= `merge`/`discard`가 단일 dict인 것과 같은 결). 전체 이력은 원장 파일 하나가
authoritative — 두 곳에 전부 복제하지 않는다. 미래 Ledger는 이 파일만 읽으면 된다.

### 2.3 마이그레이션 감지 (`plan_scale` 근처, 새 절 `# --- migrations`)

```python
MIGRATION_SEGMENTS = ("migrations", "migration", "migrate")
_MIGRATION_NAME_RE = re.compile(r"^(v\d+__|\d{3,}[_-])", re.IGNORECASE)

def looks_like_migration(path: str) -> bool
def migration_paths(paths: Sequence[str]) -> List[str]
def migration_note(paths: Sequence[str]) -> str
```
규칙(`_looks_like_test`와 같은 보수적 텍스트 휴리스틱):
디렉터리 세그먼트가 `MIGRATION_SEGMENTS`에 있거나, `alembic/versions/…`이거나,
`.sql` 파일명이 `V1__` / `003_` 형태이면 마이그레이션으로 본다.
경고문(막지 않는다, 한 번 찍는다):

```
warning: this rollback range contains 2 migration file(s) - the database is NOT
         rolled back by this command
           backend/migrations/003_add_seat.sql
           backend/migrations/004_seat_index.sql
```
`say_lines(migration_note(...))`로 출력하고 목록은 원장 항목의 `migrations`에 남긴다.

### 2.4 `--rollback <id> --to <stage>`

`merge_slice`/`discard_slice` 사이에 `rollback_slice(args, repo, repo_given, data_dir) -> int`
를 새 절 `# ------ rollback`으로 추가한다. `_finished_workspace(...)`를 그대로 재사용해
`(rec, state, ws)`를 얻는다(workspace 없는 v0.2 slice는 거기서 이미 거부된다).

절차:

1. **상태 전제**: `status`가 `merged` / `reverted` / `discarded`면 거부(exit 2).
   각각 다른 문장으로: merged → "먼저 `--revert-merge`", reverted → "amend 후 다시 merge
   하거나 discard", discarded → "브랜치가 없다". 그 외(running:*/failed/done/rejected/
   quota_wait/rolled_back)는 허용 — 이 명령의 대상이 "진행 중/실패 slice"다.
2. **목표 해석**: 유효 target = `[REQUIREMENT_STAGE] + stage_order(state)` 중
   `state["commits"]`에 sha가 있는 것. `--to`가 없거나 목록에 없으면 유효 목록을 찍고
   exit 2. (amend 라벨은 이번 범위에서 target으로 받지 않는다 — 에러 메시지에 그렇게 적는다.)
3. **workspace 검증**: `workspace.verify(repo, ws)`가 사유를 주면 그대로 거부(문구는
   `continue_slice`의 것과 같은 형태: 재생성하지 않는다).
4. **worktree tracked clean 검사**: `workspace.is_clean(ws.path, tracked_only=True)`가
   False면 거부 — `reset --hard`가 사람이/에이전트가 남긴 tracked 수정을 삼킨다.
   untracked는 reset이 건드리지 않으므로 통과시킨다(빌드 산출물이 정상 상태다).
5. **현재 지점과 범위 계산**: `before = workspace.resolve_commit(repo, ws.branch)`,
   `target_sha = state["commits"][target]`.
   - `is_ancestor(repo, target_sha, before)`가 False면 거부(브랜치가 이미 손으로 움직였다).
   - `before == target_sha`면 "이미 그 지점이다"라고 말하고 아무것도 하지 않고 exit 0.
     (원장에도 남기지 않는다 — 번복이 아니다.)
   - `dropped = {label: sha for label, sha in commits.items() if not is_ancestor(sha, target_sha)}`
     → 라벨 순서를 가정하지 않고 amend 라벨까지 정확히 걸러낸다.
   - `paths = workspace.diff_paths(repo, target_sha, before)` → 마이그레이션 경고 입력.
6. **실행 전 출력**(요구사항의 "현재 지점 기록 + 복구 명령"):
   ```
   [pipeline] rollback slice <id> -> '<target>' (<target7>)
   [pipeline]   branch  slice/<id>
   [pipeline]   was at  <before7>   (<n> commit(s) dropped: implement, test)
   [pipeline]   undo    git -C <worktree> reset --hard <before10>
   ```
   그 뒤 마이그레이션 경고. `--dry-run`이면 여기까지만 하고 **아무것도 바꾸지 않고** exit 0.
7. **잠그고 실행**: `with SliceLock(rec):` → `workspace.reset_hard(ws.path, target_sha)`
   (`GitError` → `PipelineError`) → 원장 append → state 되감기 → `rec.write_state`.
8. **state 되감기** (`_rewind_state(state, target, target_sha, dropped, n)`):
   - `rewound = stage_order` 중 target 뒤의 stage 전부 (target이 `requirement`면 전부).
   - 각 stage entry: 되감기 전 값을 보존한 채 현실에 맞춘다 —
     `entry["rewound"] = {"n": n, "at": now_iso(), "was": {"status","run_id","commit","verdict"}}`
     (요구사항: 지우지 않고 "되감았음"을 기록) 뒤에 `status="pending"`,
     `run_id`/`commit`/`verdict`/`session_id` pop. **`attempts`와 `failures`는 남긴다** —
     비용과 이력이고, `session_id`가 없으면 세션 정책에 영향도 없다.
   - `state["commits"]`에서 `dropped` 키 제거(원장이 그 sha를 보존한다).
   - test가 되감기 대상이면 `state.pop("test_verdict", None)`.
   - `state["rollback"]` 요약 갱신, `state["reason"] = "rolled back to '<target>' (<sha7>)"`,
     `status = STATUS_ROLLED_BACK`.
   - `amend_open`은 건드리지 않는다. 열린 amend가 있으면 `--resume-slice`가 그 사이클을
     이어가고, 그 사이클의 stage들이 곧 되감긴 stage들이라 의미가 맞는다.
9. **마무리 출력**: 무엇이 pending이 됐는지 한 줄 + 다음 명령
   `aidev pipeline --repo <r> --resume-slice <id>`.

`continue_slice`는 손대지 않아도 된다: `rolled_back`은 done/merged/discarded 어디에도
걸리지 않으므로 "resuming slice X (was rolled_back)"으로 첫 pending stage부터 이어간다.
이것이 "재진행 완주" 요구를 코드 추가 없이 만족시킨다.

### 2.5 `--revert-merge <id>`

`revert_merge_slice(args, repo, repo_given, data_dir) -> int`, `merge_slice` 바로 아래.

1. `_finished_workspace(...)`로 `(rec, state, ws)`.
2. `merge = state.get("merge")`가 없거나 `status != "merged"` 또는 `commit`이 없으면 거부:
   "slice X는 merge된 적이 없다 / merge가 충돌로 끝났다".
   이미 `state.get("revert", {}).get("status") == "reverted"`면 거부하고 그 sha를 알려준다
   (재적용은 사람이 `git revert <revert-sha>`로 한다).
3. 본진 전제조건 — `merge_slice`와 **동일한 문장**을 재사용(공통 헬퍼
   `_require_checkout_on_base(repo, ws)`로 뽑아 `merge_slice`도 그것을 부르게 한다):
   base가 아니면 `git checkout <base>` 안내, tracked dirty면 commit/stash 안내.
   worktree clean 검사는 필요 없다 — revert는 worktree를 건드리지 않는다.
4. `workspace.is_ancestor(repo, merge_commit, ws.base)`가 False면 거부:
   "이 merge 커밋은 지금 base에 없다(이력이 다시 쓰였거나 다른 base다)".
5. `before = head_commit(repo)`, `paths = diff_paths(repo, merge_commit + "^", merge_commit)`
   → 마이그레이션 경고. 실행 전 출력:
   ```
   [pipeline] revert merge of slice <id> on <base>
   [pipeline]   merge commit   <merge7>  (slice(<id>): merge)
   [pipeline]   base is at     <before7>
   [pipeline]   undo (not pushed yet)  git -C <repo> reset --hard <before10>
   [pipeline]   undo (already pushed)  git -C <repo> revert --no-edit <새 revert sha>
   ```
   (두 번째 undo 줄은 revert가 성공한 뒤 실제 sha와 함께 다시 한 번 찍는다.)
   `--dry-run`이면 여기서 exit 0.
6. `with SliceLock(rec):` → `workspace.revert_commit(repo, merge_commit, mainline=1)`.
   - 충돌: `state["revert"] = {"at","status":"conflict","conflicts":[...],"merge_commit","base"}`,
     충돌 파일 나열 + `git -C <repo> revert -m 1 <sha>` 안내, **자동 해결 금지**, exit 4.
     `revert --abort`가 이미 본진을 원상복구했음을 문장으로 말한다.
   - 성공: `state["revert"] = {"at","status":"reverted","commit":<새 sha>,"merge_commit",
     "base","branch"}`, `set_status(rec, state, STATUS_REVERTED)`, 원장 append.
7. 마지막 안내: worktree와 브랜치는 **그대로 둔다**(우리가 만들지 않은 걸 지우지 않는
   discard의 원칙과 같다). 다음 선택지 두 줄: `--amend <id> "..."` 후 다시 `--merge`,
   또는 `--discard <id>`. push된 경우 `git -C <repo> push <remote> <base>`를 사람이
   친다고 명시(이 명령은 push하지 않는다).

`reverted` slice의 하위 호환:
- `continue_slice`(`pipeline.py:2788`)의 `status in (STATUS_MERGED, STATUS_DISCARDED)`
  거부 목록에 `STATUS_REVERTED`를 **추가한다**. 넣지 않으면 모든 stage가 done인 채
  `run_pipeline`이 돌아 status를 조용히 `done`으로 되돌려 번복 기록을 세탁한다.
- `amend_slice`(`pipeline.py:2902`)의 merged 안내 옆에 reverted 분기를 추가:
  "이 slice는 base에서 철회됐다 — amend가 끝나면 다시 merge해야 반영된다".
- `list_slices` / `epic.slice_status` / `render_summary`는 status를 **문자열 그대로**
  다루므로 수정이 필요 없다(열린 스키마 확인 = 테스트로 고정한다).

### 2.6 요약 출력 (`_workspace_lines`, `pipeline.py:2224`)

`state.get("rollback")`이 있으면 한 줄:
`Rollback  #2 stage -> plan  (a1b2c3d -> 9f8e7d6)` — merge/discard 라인들과 같은 폭 규약.
`status == STATUS_ROLLED_BACK`이면 `Next` 줄을 `--resume-slice`로,
`STATUS_REVERTED`이면 `--amend` / `--discard`로 찍는다.

### 2.7 CLI (`add_parser` `pipeline.py:2293`, `_dispatch` `pipeline.py:2431`)

```python
cmd.add_argument("--rollback", default=None, metavar="SLICE",
                 help="rewind a slice's branch and state to a stage commit (needs --to)")
cmd.add_argument("--to", dest="to_stage", default=None, metavar="STAGE",
                 help="with --rollback: the stage commit to rewind to (or 'requirement')")
cmd.add_argument("--revert-merge", default=None, metavar="SLICE",
                 help="put a revert of this slice's merge commit on its base branch")
cmd.add_argument("--reason", default=None, metavar="TEXT",
                 help="why you are rolling back - stored in the slice's rollback record")
```
`_dispatch`:
- `modes` 튜플에 `("rollback", "--rollback")`, `("revert_merge", "--revert-merge")` 추가
  (여러 명령 동시 지정 거부에 자동으로 포함된다).
- `--push` 가드와 같은 자리에 세 개의 사용법 가드:
  `--to`만 있고 `--rollback`이 없으면 / `--rollback`인데 `--to`가 없으면 /
  `--reason`이 롤백 계열 없이 왔으면 → exit 2.
- 디스패치는 `merge` 앞에 두 줄 추가.
- 마지막 `raise PipelineError("one of --requirement, ...")` 문구에 `--rollback`,
  `--revert-merge`를 더한다(기존 테스트는 `"one of --requirement"` 접두만 본다).

`__version__`은 **올리지 않는다** — v0.4.1(amend/test_commands)도 0.4.0을 유지했다.

---

## 3. `tests/fake_pipeline_claude.py`

`AIDEV_FAKE_MIGRATION` 하나만 추가한다. 켜져 있으면 implement 단계에서
`backend/migrations/003_add_seat.sql`을 만든다(디렉터리 포함). `AIDEV_FAKE_FILES` 블록
바로 옆에 같은 모양으로 넣고, 모듈 docstring의 환경변수 목록에 한 줄 더한다.
이렇게 하면 마이그레이션 파일이 **실제 implement 커밋에 실려** 롤백 범위에 들어간다 —
테스트가 손으로 커밋을 심는 것보다 진짜 경로를 지난다.

---

## 4. `tests/test_pipeline.py` — 새 절 `# ---- rollback / revert-merge`

merge/discard 절(1530행대) 뒤, `--amend` 절 앞에 넣는다. `finished_slice()`,
`worktree()`, `state_of()`, `branches()`, `git()` 헬퍼를 그대로 쓴다.
헬퍼 하나만 추가: `rollbacks(repo)` — `rollbacks.json`을 읽어 리스트 반환.

1. `test_rollback_rewinds_the_branch_and_the_state`
   완주 slice → `--rollback <id> --to plan` → exit 0. 브랜치 tip == `commits["plan"]`,
   worktree HEAD도 같음. state: `status == "rolled_back"`, plan=done,
   implement/test=pending, `commits`에 implement/test 없음, plan/requirement는 남음,
   `stages.implement.attempts`는 보존, `stages.implement.rewound.was.status == "done"`,
   `test_verdict` 사라짐, `schema == 2`.
2. `test_a_rolled_back_slice_resumes_and_finishes` — Done Criteria 1번.
   위 상태에서 `--resume-slice <id>` → exit 0, 롤백 이후 stub 호출이 2건(implement,
   test)뿐, 최종 status `done`, 브랜치에 `slice(<id>): implement`/`: test` 커밋이 다시
   존재(`subjects(repo, "<base>..slice/<id>")`).
3. `test_rollback_prints_the_recovery_command_and_records_it` — Done Criteria 3번.
   stdout에 `was at <before7>`와 `reset --hard <before10>` 포함. `rollbacks.json`
   1건: `kind == "stage"`, `target == "plan"`, `from == before`, `to == plan sha`,
   `dropped_commits`에 implement/test, `reason == "테스트가 헛돌아서"`(`--reason`로 전달),
   `state["rollback"]["n"] == 1`.
4. `test_rollback_warns_about_migrations_in_range` — Done Criteria 4번.
   `AIDEV_FAKE_MIGRATION=1`로 slice 완주 → `--to plan` 롤백 → stdout에
   `migration` + `1` + 파일 경로, 원장 항목의 `migrations`에 그 경로. 그래도 exit 0.
5. `test_rollback_refuses_uncommitted_work_in_the_worktree`
   worktree의 tracked 파일(README.md)을 수정 → exit 2, 브랜치 tip 불변.
   반대로 untracked 파일(`junk.txt`)만 있으면 롤백이 성공하고 그 파일은 **살아남는다**.
6. `test_rollback_usage_errors` — `--to` 없이 `--rollback`(exit 2, "--to"),
   `--rollback` 없이 `--to`(exit 2), 없는 stage `--to nope`(exit 2, 유효 목록 출력),
   merge된 slice에 `--rollback`(exit 2, "--revert-merge" 안내).
7. `test_rollback_dry_run_touches_nothing` — 출력은 나오지만 브랜치 tip 불변,
   `rollbacks.json` 없음, state 불변.
8. `test_rollback_after_an_amend_drops_the_amend_commits`
   완주 → amend 1회 → `--to implement` 롤백 → `commits`에 `amend1/*` 없음,
   원장 `dropped_commits`에 amend 라벨 존재, implement는 여전히 done, test는 pending.
9. `test_revert_merge_puts_a_revert_commit_on_the_base` — Done Criteria 2번.
   완주 → `--merge` → `--revert-merge` → exit 0. base HEAD 제목이
   `Revert "slice(<id>): merge"`, merge가 가져왔던
   `.aidev/history/<id>/requirement.md`가 본진에서 사라짐, 브랜치와 worktree는 그대로,
   state: `status == "reverted"`, `revert.status == "reverted"`, `revert.commit` 존재,
   원장에 `kind == "revert-merge"` 1건, stdout에 두 종류 undo 명령.
10. `test_revert_merge_conflict_stops_and_reports`
    merge 후 본진에서 `.aidev/history/<id>/requirement.md`를 수정·커밋 → `--revert-merge`
    → exit 4, stdout에 `REVERT CONFLICT` + 파일명, 본진 HEAD 불변,
    `git status --porcelain`에 `UU` 없음, `state["revert"]["status"] == "conflict"`,
    slice status는 `merged` 그대로.
11. `test_revert_merge_preconditions`
    merge 안 한 slice(exit 2, "never merged"), base가 아닌 브랜치 체크아웃(exit 2,
    `checkout <base>`), 본진 dirty(exit 2), 두 번째 `--revert-merge`(exit 2, "already").
12. `test_a_reverted_slice_is_readable_and_not_silently_resumed` — 열린 스키마 확인.
    revert 후 `--list` exit 0이고 출력에 `reverted`, `--resume-slice` exit 2
    ("nothing left to resume"), `--amend`는 돌고 stdout에 "merge it again" 안내,
    `state["schema"] == 2`.

임시 파일은 전부 `tmp_path` 안에서만 만들고 저장소에 남기지 않는다.

---

## 5. `README.md`

1. 흐름도(106-108행) 아래 두 줄 추가:
   `--rollback <id> --to <stage>` / `--revert-merge <id>`.
2. 명령 예시 블록(111-119행)에 두 줄 추가.
3. `### 사후 수정 (--amend, v0.4.1)` 절(286-312행) 뒤에 새 절
   **`### 되돌리기 (--rollback / --revert-merge, v0.4.2)`**:
   - 철학 0조 인용 → "되돌리기도 의미 단위로".
   - stage 롤백: reset이 허용되는 이유(slice 브랜치는 **로컬 전용**), `--to`가 가리키는
     커밋은 **남고** 그 뒤가 되감긴다는 의미, `requirement`도 target이라는 것,
     attempts/이력은 보존하고 `rewound`로 "되감았음"을 남긴다는 것, 이어서 `--resume-slice`.
   - slice 철회: push됐을 수 있으므로 **revert만, reset 금지**. 본진 전제조건은 `--merge`와
     동일. 충돌은 자동 해결하지 않고 abort 후 exit 4. status `reverted`, 이후 경로는
     amend → 다시 merge, 또는 discard. push는 사람이 친다.
   - 공통: 실행 전 현재 지점 + 복구 명령 출력, `rollbacks.json` 원장(미래 Ledger의 번복률
     원천), `--reason`, 마이그레이션 경고는 **경고일 뿐**이고 DB는 되돌리지 않는다,
     `--dry-run`.
   - 하지 않는 것: epic 되감기, DB 자동 롤백, push된 slice 브랜치.
4. slice 디렉터리 트리(490-499행)에 `rollbacks.json  번복 이력 (있을 때만)` 추가.
5. status 목록(525-532행)에 `rolled_back` / `reverted` 추가 + "v0.4.2가 더한
   `rollback` / `revert` / `stages.*.rewound`도 전부 optional이라 schema는 2 그대로".
6. 안전핀 표(580-585행)에 한 줄: `--rollback`은 worktree의 tracked 변경이 없을 것,
   `--revert-merge`는 `--merge`와 같은 본진 조건.
7. 옵션 표(642-667행)에 `--rollback` / `--to` / `--revert-merge` / `--reason` 4행 추가,
   `--dry-run` 설명에 두 명령 언급.
8. 종료 코드 줄(669행): `4`가 merge 충돌 **및 revert 충돌**임을 명시.

---

## 6. epic 되감기 — 설계 스케치 (이번 slice에서 **구현하지 않는다**)

요구사항의 "설계만 스케치". README가 아니라 이 plan에만 남긴다.

- 명령 모양: `aidev pipeline --repo <r> --rollback-epic <epic-id> --to <n>`
  (n = slices 목록의 index). 에픽은 slice들을 **사슬**로 만든다: slice N+1의 worktree는
  `slice/<N>`에서 갈라져 나온다(`workspace.start`). 따라서 되감기는 stage 롤백의 단순
  반복이 아니라 **tip에서 거꾸로** 처리해야 한다.
- 알고리즘: `state["slices"]`를 역순으로 훑어 index > n인 항목에 대해
  (a) merge된 것은 `--revert-merge`, (b) merge 안 된 것은 `--discard`,
  (c) 이 둘의 순서는 **반드시 tip → base 방향**(뒤 slice가 앞 slice의 커밋 위에 있다).
  그 뒤 epic state의 해당 항목들을 "not started"로 되돌린다 — `reconcile_items`의
  `_started()`가 이미 discarded를 "시작 안 함"으로 취급하므로 그 규약을 재사용한다.
- 위험: 사슬 중간을 되감으면 뒤 slice의 fork 지점이 사라진다. README 429행이 이미
  "조용히 base로 이동"이라고 정의한 동작과 충돌할 수 있어, 되감기는 **거부하거나 전량
  되감기**만 허용하는 쪽이 안전하다. 이 판단에 실측이 더 필요해서 이번 범위 밖이다.
- 원장: epic 되감기 1회는 slice 원장 N개 + 에픽 자신의 `rollbacks.json` 1건으로 남긴다.

---

## 7. 검증

```powershell
cd C:\Users\minsa\ai-dev-orchestrator-slices\20260816-rollback-engine
python -m pytest -q
```
- 새 테스트 12건 + 기존 전량 통과가 완료 조건. Windows에서 그대로 돈다(git 없으면
  기존 `repo` fixture가 skip).
- 회귀로 특히 볼 것: `test_usage_errors`(문구 확장), merge/discard 전체(공통 헬퍼
  `_require_checkout_on_base` 추출), `test_list_survives_unknown_stage_keys`
  (열린 스키마), epic 테스트 전량(`slice_status`가 새 상태를 문자열로 통과시키는지).
- 잔재 확인: 테스트가 만드는 파일은 전부 `tmp_path` 아래. 작업 후
  `git status --porcelain`이 계획된 5개 파일만 보여야 한다.

## 8. 위험과 대응

| 위험 | 대응 |
| --- | --- |
| `reset --hard`가 사람의 작업을 삼킨다 | tracked dirty면 **거부**. untracked는 reset이 건드리지 않으므로 통과. 실행 전 `before` sha와 undo 명령을 먼저 출력 |
| git reset은 성공했는데 state 쓰기가 실패 | 순서를 reset → 원장 append → state 쓰기로 두고, 출력된 undo 명령(`reset --hard <before>`)이 그 상태에서도 정확하게 작동한다. state 쓰기는 이미 Windows 재시도가 붙은 `write_state_atomic` |
| `commits` 라벨 순서 가정이 amend에서 깨진다 | 순서를 쓰지 않고 `merge-base --is-ancestor`로 도달 가능성만 판단 |
| revert가 본진을 반쯤 바꾼 채 멈춘다 | `merge_branch`와 같은 규약: 충돌 파일을 먼저 읽고 `revert --abort`, exit 4. 자동 해결 절대 없음 |
| `reverted` 상태가 조용히 `done`으로 세탁된다 | `continue_slice`의 거부 목록에 추가하고 테스트로 고정 |
| merge 커밋이 base에 없다(이력 재작성) | `is_ancestor(merge_commit, base)`로 사전 거부 |
| 마이그레이션 휴리스틱의 오탐/미탐 | 경고 한 줄뿐이고 어떤 동작도 막지 않는다. 규칙을 README와 코드 주석에 명시해 사람이 판단 |
| 새 인자가 epic 경로를 깬다 | epic은 같은 `args` 객체를 그대로 넘기고, 새 인자는 전부 `default=None`. `modes` 검사에만 참여 |
