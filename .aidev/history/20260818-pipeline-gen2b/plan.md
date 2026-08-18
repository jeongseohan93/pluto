# 계획 — 파이프라인 2세대 B (규격 마무리 + 실측 수리)

## 0. 먼저: 이 트리에 이미 들어 있는 것

gen2 A 머지(`672ac7b`)가 §1의 두 항목과 §2를 **이미 구현해 놓았다**. 실제 코드를 읽고
확인한 결과다. 요구사항 8항목 중 3항목은 신규 작업이 아니라 **검증 대상**이다.

| 요구 | 상태 | 근거 (현재 트리) |
| --- | --- | --- |
| §1a 반려 확장 (`rejected-detail.md`, 부분/전면 등급, 한 줄 하위호환) | **구현됨** | `aidev/pipeline.py:705` `REJECT_DETAIL_NAMES`, `:709` `GRADE_PARTIAL/FULL`, `:740` `read_rejection`, `:3027` 게이트에서 기록. 테스트 `tests/test_pipeline.py:2482,2525,2543` |
| §1b 승인 코멘트 보존 | **없음** | `aidev/pipeline.py:733` `read_decision`이 `approved:` 뒤 문구를 **버린다**(`return Decision(APPROVED)`). `:3022`의 `entry["approval_reason"]` 배선은 있는데 값이 절대 안 들어온다 |
| §1c 차분 재계획 (`--replan`) | **구현됨** | `:4383` `reopen_plan`, `:4445` `replan_slice`, `:1475` `_REPLAN_NOTE`, `:1489` `_REPLAN_PARTIAL`. 테스트 `tests/test_pipeline.py:2482-2578` |
| §2 모델 믹스 | **구현됨** | `:427` `resolve_models`, `:452` `parse_model_tokens`, `:77` `MODEL_STAGES`(diagnose 포함), `:1809` `model_for`, `:3643` `--model`/`--model-stage`, `:3873` 우선순위. 테스트 `tests/test_pipeline.py:2593-2660` |
| §3a 미지 키 경고 | **없음** | `:213` `parse_front_matter`가 모든 키를 조용히 담고, 읽는 쪽(`:247,283,311,355,442,516`)은 아는 키만 꺼낸다. 경고 지점 없음 |
| §3b watch STALE/스코프 | **없음** | `aidev/storage.py:378` `find_running_run`은 `live.json`의 `status=="running"`만 본다(무갱신 판정 없음). `aidev/cli.py:118` watch 파서에 `--repo` 없음 |
| §3c 승인 조건 승계 | **없음** | §1b와 동일 뿌리. `:1650` `build_prompt`에 승인 조건 채널 자체가 없다 |
| §3d discard 원자성 | **부분** | `:4996` 실패 시 브랜치는 지키지만, 선검사도 등록 복원도 복구 명령 안내도 없고, **고아 폴더 상태에서 `--discard`를 다시 쳐도 영원히 실패한다**(`git worktree remove`가 등록 없는 경로를 거부) |
| §3e requires-python | **없음** | `pyproject.toml:10` `>=3.9`. 실제 사인은 `aidev/verify.py:150` `path.write_text(..., newline="\n")` — `Path.write_text`의 `newline` 인자는 **3.10부터**다. 3.9에서 `TypeError`로 즉사한다 |

따라서 실제 구현은 **5건**(§1b/§3c 통합, §3a, §3b, §3d, §3e)이고, 나머지 3건은
"동작 확인 + 필요 시 회귀 테스트"다. §1a/§1c/§2는 **손대지 않는다** — 이미 규격대로
동작하고 테스트도 붙어 있으므로, 다시 쓰는 것은 요구사항의 "하지 않는 것"과 같은 종류의
낭비다.

### 이 slice가 스스로 지켜야 하는 규약 (반드시 읽을 것)

- `spec_check`가 켜져 있다(front matter에 `spec_check:` 없음 = on). `aidev/specs.py:263`
  `check_text`는 **이 slice가 건드린 모든 함수**에 대해 ① spec 주석/docstring 존재
  ② 파라미터마다 `@param <이름>` 한 줄 ③ **본문이 바뀌었으면 spec 문구도 바뀔 것**을
  요구한다. 아래에서 손대는 함수 중 `read_decision`, `run_stage`, `run_diagnosis`,
  `await_approval`, `_config`, `discard_slice`, `find_running_run`, `resolve_watch_target`,
  `cmd_watch`, `validate_items`, `workspace.verify`는 **지금 @param이 없거나 한 줄
  docstring**이다. 고치는 김에 규약대로 채워라. 안 하면 test 단계에서 slice가 실패한다.
- `test_commands:` 미선언이므로 검증 엔진은 꺼져 있고 test 단계는 에이전트다.
  허용된 명령에 `Bash(pytest)` / `Bash(python -m pytest:*)`가 있으므로 **`pytest -q`**
  한 줄로 전량을 돌려라 (`pyproject.toml:23` `testpaths=["tests"]`).
- 임시 파일/스크립트를 남기지 말 것. 실험용 파일을 만들었으면 지우고 끝내라.

---

## 1. 승인 코멘트 승계 — §1b + §3c (같은 뿌리, 한 번에)

**측정된 사태**: `approved: scope=A만` 이라고 써서 승인했는데 문구가 어디에도 안 남고,
attempt 2 세션은 조건 없는 plan만 보고 범위를 넘겨 구현했다(선구현 사태).

### 1.1 `aidev/pipeline.py:714` `Decision` / `:720` `read_decision`

```python
    if word in ("approved", "approve"):
        return Decision(APPROVED, rest.strip())      # 지금은 Decision(APPROVED)
```

- `Decision.reason` 옆 주석: "반려의 사유, 또는 승인에 붙은 조건" 한 줄.
- `read_decision`의 docstring을 규약대로: 한 줄 + `@param path` + `@flow`.
- 하위 호환: `approved`(문구 없음)는 `reason=""` 그대로. 기존 테스트
  `tests/test_pipeline.py:2851-2869`의 3개 케이스는 전부 `""`를 기대하므로 **그대로 통과**한다.

### 1.2 승인 조건을 state에서 읽는 함수 (신규, `read_rejection` 바로 아래)

```python
# 승인에 붙은 조건은 누가 한 번 말한 문장이 아니라 state다. 측정 2026-08-17/18:
# "scope=A" 조건으로 승인된 plan이 attempt 2에서 조건 없이 다시 구현됐다.
def approval_conditions(state: Dict[str, Any]) -> List[Dict[str, str]]:
    """이 slice가 받은 'approved: <조건>'들을, 단계 순서대로.

    @param state  state.json
    @flow  stage_order -> 승인 + 문구가 있는 단계만 -> 목록
    """
```

- 읽기 전용으로 구현한다(`stage_entry`는 `setdefault`로 state를 바꾸므로 쓰지 말 것).
  `state["stages"]`를 직접 조회하고, `entry.get("approval") == APPROVED` 이면서
  `entry.get("approval_reason")`이 비어 있지 않은 것만 `{"stage":…, "comment":…}`로 모은다.
- 저장 위치는 **기존 `stages.<stage>.approval_reason`을 그대로 쓴다.** 새 키를 만들지
  않는 이유: `:3022`가 이미 그 자리에 쓰고 있고, `reopen_plan`(`:4438`)이 재계획 때
  그 키를 지워 준다 — 반려된 plan의 승인 조건이 다음 사이클로 새는 것을 막는 배선이
  공짜로 맞는다. `open_amend`(`:1140`)는 지우지 않는데, 이것도 맞다: amend는 승인된
  plan 위에서 도는 사이클이므로 조건이 계속 유효하다.

### 1.3 프롬프트 주입 — `:1415` `_IMPLEMENT_PROMPT`, `:1527` `_TEST_PROMPT`, `:1495` `_DIAGNOSE_PROMPT`

세 템플릿 모두 **plan 블록 바로 뒤**에 `{approval}` 슬롯을 넣는다. 계획에 붙은 단서이므로
계획 옆이 제자리다.

```
APPROVED PLAN
-------------
{plan}
{approval}
INSTRUCTIONS
```

새 상수 + 렌더러(`_repair_note` 옆):

```python
_APPROVAL_NOTE = """
APPROVAL CONDITIONS - attached by the human who approved this
-------------------------------------------------------------
{conditions}

These are part of what was approved. They hold for every attempt, including this
one. Where a condition and the plan disagree, the condition wins.
"""

def _approval_note(conditions: Sequence[Dict[str, str]]) -> str:
    """승인에 붙은 조건 블록. 조건이 없으면 빈 문자열.

    @param conditions  ``approval_conditions``가 돌려준 목록
    """
```

- **불변식**: 조건이 없으면 렌더 결과가 `""`여서 프롬프트가 지금과 **바이트 동일**해야
  한다. (`"{plan}\n{approval}\nINSTRUCTIONS"` + `""` → 현재와 같은 `\n\n`.)
- `conditions`는 `- plan: scope=A만` 형태 한 줄씩.

### 1.4 호출부

- `:1650` `build_prompt`: 파라미터 `approvals: Sequence[Dict[str, Any]] = ()` 추가,
  `extra`/`format`에 `approval=_approval_note(approvals)` 전달. docstring에 `@param approvals`
  한 줄 추가(규약).
  - implement/test 두 템플릿 모두 `{approval}`을 가지므로 `format` 인자에 항상 넣는다.
  - `stage == "plan"`은 그대로 무시한다 — plan은 첫 단계라 앞선 승인이 없다.
- `:2212` `run_stage`의 `build_prompt(...)` 호출에 `approvals=approval_conditions(state)`
  추가. `state`는 이미 파라미터다. → **재시도(quota/세션 리셋), resume, repair, amend가
  전부 이 한 줄로 덮인다** (전부 `run_stage`를 다시 거치고, 프롬프트는 루프 회차마다
  새로 만들어지므로).
- `:2816` `run_diagnosis`의 `_DIAGNOSE_PROMPT.format(...)`에
  `approval=_approval_note(approval_conditions(state))` 추가.
- `:2973` `await_approval`: 승인이고 문구가 있으면 한 줄 보고
  `say("approval condition: {0}".format(decision.reason))` — 사람이 "받아 적혔다"를
  즉시 본다. 기록 자체는 `:3022`가 이미 한다(무변경).

### 1.5 `:686` `APPROVAL_TEMPLATE`

결정 예시에 한 줄 추가:

```
#   approved: <조건>        조건은 이후 모든 단계·재시도 프롬프트로 따라간다
```

`read_decision`이 `#` 줄을 무시하므로 템플릿은 여전히 무해하다. 중괄호를 새로 넣지 말 것
(이 문자열은 `.format`된다).

### 1.6 손대지 않는 것

`desktop/src/main/aidev-store.ts:89` `parseDecision`은 이 파서의 TS 이식본이고 승인의
`reason`을 `''`로 돌려준다. **UI는 요구사항의 "하지 않는 것"**이므로 건드리지 않는다.
desktop이 쓰는 승인문은 항상 `approved`(문구 없음)라 실제 어긋남도 없다. plan.md에 이
사실을 적어두는 것으로 갈음한다.

---

## 2. front matter 미지 키 경고 — §3a

**측정**: `model:` 키가 (당시 미지 키였을 때) 조용히 무시되면서 `max_turns` 적용이 깨졌다.
조용한 무시가 원인이므로, 무시는 유지하되 **한 줄 경고**를 낸다(에러 아님 — 미래의
front matter 키나 사람이 쓴 메모 때문에 slice가 죽으면 안 된다).

`aidev/pipeline.py`, `resolve_spec_check`(`:507`) 아래:

```python
# 읽는 쪽이 실제로 꺼내는 키 전부. 새 키를 추가할 때 여기에도 넣는 것이 규약이다.
KNOWN_FRONT_MATTER_KEYS: Tuple[str, ...] = (
    "approval", "setup", "test_commands", "max_turns", "model", "spec_check",
)

def unknown_front_matter_keys(fields: Dict[str, str]) -> List[str]: ...
def warn_unknown_front_matter(fields: Dict[str, str], where: str = "") -> str: ...
```

- `warn_unknown_front_matter`는 **문자열을 돌려준다**(출력하지 않는다). 없으면 `""`.
  단위 테스트가 capsys 없이 가능해지고, 출력 형식은 부르는 쪽이 정한다.
- 문구(한 줄):
  `warning: front matter key(s) ignored: modle, titel - known: approval, setup, test_commands, max_turns, model, spec_check`
  `where`가 있으면 뒤에 ` ({where})`.
- 출력 지점은 **`:3839` `_config` 한 곳**이다. 이유: 모든 발사 경로
  (`launch_slice:4074`, `resume_slice:4251`, `amend_slice:4349`, `replan_slice:4497`,
  `start_slice --dry-run:3952`)가 front matter를 가진 채 여기를 **정확히 한 번** 지난다.
  docstring이 이미 "front matter가 해석되는 단 한 곳"이라고 주장하고 있으므로 제자리다.
  `say_lines(warn_unknown_front_matter(fields))` 한 줄. `fields`가 없는 호출
  (`epic.py:782` decompose)은 빈 dict라 아무것도 안 찍는다.
- `aidev/epic.py:297` `validate_items`: 항목마다
  `where = "{path}, slice N ('제목')"`로 같은 경고를 찍고, 검증 목록에
  **`resolve_models` / `resolve_spec_check`를 추가**한다. README(`:613`)가 이미 여섯 키를
  검증한다고 적어 놓았는데 실제로는 넷만 부르고 있다 — 미지 키 경고와 같은 결함
  (선언한 줄이 조용히 아무 일도 안 한다)이므로 여기서 같이 닫는다.
  `validate_items`는 docstring에 `@param items` / `@param path` / `@flow`를 채워야 한다.

---

## 3. `watch last`: STALE 제외 + `--repo` 스코프 — §3b

**측정**: 이틀 전에 죽은 run에 붙었다. 원인 두 개 — ① `find_running_run`은 `live.json`의
`status`만 보는데, 죽은 러너는 그 파일을 `running`인 채로 남긴다 ② watch에는 `--repo`가
아예 없어서 다른 저장소의 run도 후보다. 게다가 `cmd_watch`(`:310`)의 루프는
`status != "running"`일 때만 빠지므로 **죽은 run에 붙으면 영원히 돈다.**

### 3.1 `aidev/storage.py`

```python
# 무갱신 판정 기준. reporter.STALE_AFTER_S(10초)는 "표시"용이고 이쪽은 "선택"용이라
# 훨씬 관대해야 한다: 이벤트 없이 30분 조용한 세션은 tool 호출 하나가 오래 걸리는
# 경우로 실제 가능하고(VERIFY_TIMEOUT_S가 같은 1800초다), 그보다 길면 러너가 없는 것이다.
RUN_STALE_AFTER_S = 1800.0

def live_age(live, run_dir=None, now=None) -> Optional[float]: ...
def is_stale(live, run_dir=None, now=None, stale_after=RUN_STALE_AFTER_S) -> bool: ...
def run_repo(run_dir) -> Optional[str]: ...
def in_repo_scope(run_repo, repo, worktree_root=None) -> bool: ...
def runs_for_repo(runs_root, repo=None, worktree_root=None) -> List[Path]: ...
def find_running_run(runs_root, repo=None, worktree_root=None, now=None) -> Optional[Path]: ...
```

- `live_age`: `live["updated_at"]`(float, `write_live:300`이 항상 넣는다)로 재고, 숫자가
  아니면 `live.json`의 mtime으로 폴백, 그것도 실패하면 `None`(= 잴 수 없으므로 stale 아님).
  **재지 못했다고 run을 숨기지 않는다.**
- `run_repo`: `run.json`의 `config.repo`를 먼저 본다 — `RunStore.write_run_json`이
  **실행 시작 시점에** 쓰므로 돌고 있는 run에도 있다. 없으면 `telemetry.json`의 `repo`.
  둘 다 없으면 `None`.
- `in_repo_scope`: `repo` 자신 / `repo` 하위 / `worktree_root` 하위면 참.
  pipeline 단계의 run은 `repo=cfg.cwd`(= worktree)로 기록되므로(`:2052`) worktree 루트를
  같이 봐야 실제 slice run이 잡힌다. 경로 비교는 `os.path.normcase(str(Path(p).resolve()))`
  (workspace.py `_same_path`와 같은 방식). **repo를 준 상태에서 `run_repo`가 `None`이면
  제외한다** — 소속을 증명 못 하는 run을 스코프에 넣는 것이 이 결함의 재발이다.
- `find_running_run`: 최신순으로 훑되 `status=="running"` **이고** `is_stale`이 아닌 것.
- 기존 호출/시그니처 호환: `find_running_run(runs)` 위치인자 그대로, `list_run_dirs`,
  `resolve_run_dir`는 무변경(테스트가 그대로 import 한다).

### 3.2 `aidev/cli.py`

- `:118` watch 파서에 추가:
  ```python
  watch_cmd.add_argument("--repo", type=Path, default=None,
      help="이 저장소(및 그 slice worktree)의 run만 후보로 삼는다 — 'last'에만 적용")
  ```
- `:346` `resolve_watch_target(runs_root, run_id, repo=None, now=None)`:
  - 명시 id → `resolve_run_dir` 그대로(**명시는 언제나 이긴다** — `test_watch.py:127`이
    고정하고 있는 계약).
  - `last`/빈 값 → ① 스코프 안의 살아 있는 running ② 없으면 스코프 안에서 **stale-running이
    아닌** 최신 ③ 그래도 없으면 스코프 안의 최신(run이 있는데 "없다"고 하지 않기 위해).
  - worktree 루트는 `workspace.default_worktree_root(repo)`(`aidev/workspace.py:461`)로 얻는다.
    `cli.py`에 `from . import workspace` 추가(순환 없음: workspace는 표준 라이브러리만 쓴다).
    커스텀 `--worktree-root`로 만든 worktree는 이 경로 규칙 밖이므로 run id를 직접 대야
    한다 — README에 그렇게 적는다.
- `:298` `cmd_watch` 루프: 프레임을 그린 뒤
  `payload["status"] == "running"` 이고 `storage.is_stale(payload, run_dir)`이면
  **폴링을 멈춘다.** 한 줄 보고(`이 run은 N초째 갱신이 없다 - 러너가 없다`) 후 루프 탈출 →
  기존 꼬리(telemetry.json 있으면 최종 리포트)를 그대로 탄다. 죽은 run에 무한 폴링하던
  경로가 이걸로 닫힌다. 반환값은 `0` 유지(관찰 실패가 아니라 관찰 대상이 끝난 것이다).
- `--repo`가 없으면 지금 동작 그대로다.

---

## 4. discard 원자성 — §3d

**측정된 반쯤 죽은 상태**: `git worktree remove --force`가 admin 등록은 지우고 디렉터리
삭제에서 실패 → 고아 폴더가 남는다. 그러면 ① `--resume-slice`는 `workspace.verify`
(`:645` "no longer registered")로 거부되고 ② `--discard`를 다시 쳐도 등록 없는 경로라
`git worktree remove`가 또 실패한다. **출구가 없다.**

### 4.1 `aidev/workspace.py` (신규 3함수, `worktree_remove:331` 근처)

```python
def removal_blockers(repo: Path, path: Path) -> List[str]:
    """'git worktree remove'가 통할 수 없는 이유. 빈 목록이면 시도할 값어치가 있다."""
    # locked worktree -> "git -C <repo> worktree unlock <path>"
    # 등록돼 있는데 디스크에 없음 -> "git -C <repo> worktree prune"

def repair_worktree(repo: Path, path: Path) -> bool:
    """등록을 되살려 본다. 되살아났는지는 find_worktree로 '측정'한다(가정하지 않는다)."""
    run(repo, ["worktree", "repair", str(path)], check=False)
    return find_worktree(repo, path) is not None

def remove_tree(path: Path) -> Optional[str]:
    """이 도구가 만든 디렉터리를 지운다. 못 지운 이유를 돌려주고, 예외는 내지 않는다."""
```

- `git worktree repair`는 **최선 노력**이다(admin 디렉터리가 통째로 없으면 못 되살린다).
  그래서 성공 여부를 반환값으로 측정하고, 실패해도 그 자체로는 에러가 아니게 만든다.
  `run(..., check=False)`라 구버전 git(서브커맨드 없음)에서도 안 죽는다.
- `remove_tree`는 "우리가 만들지 않은 잔해는 치우지 않는다"는 모듈 규약(파일 헤더 `:15`)의
  예외가 **아니다**: 지우는 대상은 그 slice의 state.json에 기록된 자기 workspace이고,
  사람이 `--discard`를 친 상황에서만 불린다.

### 4.2 `aidev/pipeline.py:4989` `discard_slice`

```
with SliceLock(rec):
    entry  = workspace.find_worktree(repo, ws.path)
    exists = Path(ws.path).exists()

    if entry is not None:                      # (1) 선검사 — 아무것도 건드리기 전에
        blockers = workspace.removal_blockers(repo, ws.path)
        if blockers: raise PipelineError(...복구 명령 포함...)
        try:    workspace.worktree_remove(repo, ws.path)
        except workspace.GitError as exc:      # (2) 실패 후 실제 상태를 다시 잰다
            ...아래 표...
    elif exists:                               # (3) 이전 실패가 남긴 고아 폴더
        say("worktree is no longer registered but the directory is still there")
        problem = workspace.remove_tree(ws.path)
        if problem is not None: raise PipelineError(...복구 명령...)
    else:
        say("worktree was already gone: ...")
    ...브랜치 삭제 / state 기록은 지금 그대로...
```

(2)의 분기 — **실패 후 상태를 가정하지 않고 다시 측정한다**:

| 재측정 결과 | 처리 |
| --- | --- |
| 여전히 등록됨 + 폴더 있음 | 아무것도 안 바뀌었다. 지금 메시지(`:5002`) 유지 + "브랜치는 그대로 두었다" |
| 등록 사라짐 + 폴더 남음 | `repair_worktree` 시도 → **되살아나면** "원상 복구했다, 다시 시도해라" / **아니면** 고아 상태를 `state["discard_failed"] = {"at","path","error"}`에 기록하고, 에러에 복구 3종(`git -C <repo> worktree prune`, `rm -rf <path>`, `aidev pipeline --repo <repo> --discard <slice>` ← 이제 (3)으로 마무리된다)을 찍는다 |
| 폴더 없음 | 목표 상태에 도달했다. git의 불평만 `say`로 남기고 브랜치 삭제로 진행한다 |

- 성공 경로에서는 `state.pop("discard_failed", None)`.
- 브랜치는 **어느 분기에서도 먼저 지우지 않는다** — 현재 코드가 지키는 불변식이고
  그 이유(`:4999` 주석)도 그대로 유효하다.
- 실패 시 `state`는 `STATUS_DISCARDED`가 되지 않는다(지금과 같다). exit는 `EXIT_USAGE`(2).

### 4.3 복구 명령 안내 (같은 결함의 반대편 출구)

`:4229`와 `:4299`에 **글자 그대로 같은** 6줄 블록이 있다(resume/amend가 깨진 workspace를
거부할 때). 여기에 `git -C <repo> worktree repair <path>`를 더해야 하는데, 두 벌을 따로
고치면 또 어긋난다. 헬퍼 하나로 합친다:

```python
def _broken_workspace_error(repo, rec, ws, broken) -> PipelineError:
    """복구할 수 없는 workspace를, 복구 명령과 함께 거절한다."""
```

두 호출부는 `raise _broken_workspace_error(...)` 한 줄이 된다.

---

## 5. requires-python >= 3.10 — §3e

- `pyproject.toml:10` → `requires-python = ">=3.10"`.
- 실제 사인을 README에 근거로 남긴다: `aidev/verify.py:150`의
  `Path.write_text(..., newline="\n")`는 3.10 신설 인자다. 3.9에서 `TypeError`.
  **코드를 3.9로 낮추지 않는다** — 요구사항이 "명시"를 시켰고, 개행 제어는 지울 수 없는
  요구(윈도우에서 CRLF가 섞이면 로그 대조가 깨진다)이기 때문이다.
- `README.md:35` 설치 절:
  ```
  Python **3.10 이상**. 의존성 없음(표준 라이브러리만). `claude` CLI가 PATH에 있어야 한다.
  (3.9에서는 `Path.write_text(newline=...)`가 없어 `aidev verify`가 즉사한다 — 실측 2026-08-18, macOS)
  ```

---

## 6. README 갱신 (Done Criteria)

| 위치 | 내용 |
| --- | --- |
| `:35` 설치 | Python 3.10 이상 + 실측 근거 |
| `:541` 승인 게이트 | `approved: <조건>` 형식과 "조건은 이후 모든 단계/재시도/repair 프롬프트로 따라간다", 조건 없는 `approved`는 그대로 |
| `:390` 반려/재계획 절 뒤 | **승인 조건 승계** 문단 — 측정(scope=A가 attempt 2에서 무시된 선구현 사태)과 저장 위치(`stages.<stage>.approval_reason`), 재계획 시 소거된다는 규칙 |
| `:611` front matter 여섯 키 | 미지 키는 **무시하되 경고 한 줄**, 에러가 아닌 이유 |
| `:961` watch 절 | `aidev watch --repo <path>`(= `last`에만 적용), `last`가 **30분 무갱신 running run을 후보에서 뺀다**는 것, 표시용 10초 STALE(`:1030`)과 다른 숫자인 이유, 죽은 run에 붙으면 무한 폴링 대신 보고하고 끝난다는 것, 커스텀 `--worktree-root`는 run id를 직접 대야 한다는 것 |
| `:207` discard 문단 | 선검사 → 시도 → 실패 시 재측정/등록 복원 → 고아 폴더는 다음 `--discard`가 마무리한다. 브랜치 우선 보존 불변식은 유지 |
| `:920` 플래그 표 | (필요 시) watch `--repo` 한 줄 |

---

## 7. 테스트

기존 330개 test 함수(파라미터 전개 403+)는 **전부 유지**한다. 아래는 추가분.

### `tests/test_pipeline.py`

1. `test_decision_parsing` 파라미터에 3줄 추가:
   `("approved: scope=A only\n", APPROVED, "scope=A only")`,
   `("approve: 조건 있음", APPROVED, "조건 있음")`,
   `("approved\n", APPROVED, "")` (하위 호환 — 이미 있음, 유지 확인).
2. `test_an_approval_condition_reaches_implement_and_the_retry` (E2E, Done Criteria 문구
   그대로): `requirement(repo)`(front matter 없음 = plan 게이트 on) →
   `monkeypatch.setenv("AIDEV_FAKE_FAIL_AFTER", "1")`로 implement를 1회차에 죽이고,
   게이트는 `approve_the_gate` 변형으로 `"approved: scope=A only\n"`을 쓴다 → exit 1 →
   `FAIL_AFTER` 해제 후 `--resume-slice` → exit 0.
   검증: `state["stages"]["plan"]["approval_reason"] == "scope=A only"`,
   **implement 프롬프트 2개 모두**에 `"scope=A only"`와 `"APPROVAL CONDITIONS"` 존재.
   (`session_reset_after`가 1이라 두 번째는 새 세션 = 기억이 없는 세션이라는 점이 바로
   이 결함의 현장이다.)
3. `test_build_prompt_carries_the_condition_into_a_repair` (단위): `build_prompt("implement",
   …, repair={"n":1,"failure":"…","diagnosis":""}, approvals=[{"stage":"plan","comment":"scope=A"}])`
   → 두 블록이 모두 있고, `approvals=()`면 프롬프트가 조건 없는 버전과 **완전히 동일**할 것.
4. `test_approval_conditions_reads_only_approved_stages` (단위): 반려/미결/문구 없는 승인은
   빠지고, `state`가 **변형되지 않는다**(`copy.deepcopy` 비교).
5. `test_an_unknown_front_matter_key_warns_and_is_ignored`:
   `front="approval: none\nmodle: claude-opus-5\nmax_turns: implement=90"` →
   slice는 정상 완주(exit 0), stdout에 `front matter key(s) ignored: modle`,
   그리고 **`max_turns`는 정상 적용**(implement argv의 `--max-turns`가 `90`) —
   실측 결함(미지 키가 다른 키를 깨뜨림)을 그대로 고정한다.
6. `test_unknown_front_matter_keys_unit` (파라미터): 알려진 6키만 있으면 `""`,
   대시/언더스코어 정규화(`max-turns`)도 알려진 키로 취급.
7. `test_discard_survives_a_half_removed_worktree` (§3d 핵심):
   - `finished_slice`로 slice를 만든 뒤 `workspace.worktree_remove`를
     monkeypatch —  worktree의 `.git` 파일(`gitdir: <repo>/.git/worktrees/<name>`)을 읽어
     그 admin 디렉터리를 `shutil.rmtree`로 지우고 `GitError`를 던진다. **실측 상태의 정확한
     재현**(등록만 사라지고 폴더는 남음).
   - 1차 `--discard` → exit 2. 검증: 브랜치 살아 있음, `state["status"] != "discarded"`,
     `state["discard_failed"]["path"]`, stderr에 고아 경로 + `worktree prune` + 재실행 명령.
   - monkeypatch 해제 후 2차 `--discard` → **exit 0**, 폴더 없음, 브랜치 없음,
     `state["status"] == "discarded"`, `discard_failed` 없음. (= "다시 칠 수 있다"가 계약)
8. `test_discard_keeps_the_branch_when_the_worktree_will_not_go`: `worktree_remove`가
   **아무것도 바꾸지 않고** `GitError`를 던지는 경우 → 브랜치 유지, 등록 유지, exit 2.
   (기존 동작의 회귀 고정.)

### `tests/test_watch.py`

9. `test_last_skips_a_run_nobody_has_updated`: 오래된 running(`updated_at=time.time()-2일`)과
   더 오래된 completed → `find_running_run`은 `None`, `resolve_watch_target(runs,"last")`는
   **completed 쪽**을 고른다.
10. `test_last_still_prefers_a_live_running_run`: 신선한 running이 있으면 그것.
    (기존 `test_find_running_run_prefers_the_newest_running`은 `updated_at`이 현재 시각이라
    그대로 통과 — 회귀 방어.)
11. `test_watch_scopes_last_to_a_repo`: `run.json`을 두 개 만들어 하나는
    `config.repo = <repo>/…`, 하나는 남의 저장소 → `--repo`를 준 `last`가 자기 것만 고른다.
    worktree 케이스도 한 줄: `config.repo = <repo>-slices/<slice>` 가 스코프에 **든다**.
12. `test_watch_does_not_follow_a_dead_run_forever`: stale-running live.json 하나 →
    `main([... "watch", run_id])`가 `_sleep` 스텁 없이도 **끝난다**(무한 루프 방지),
    출력에 STALE 문구.
13. `test_run_repo_falls_back_to_telemetry`(단위): `run.json` 없고 `telemetry.json`만 있을 때.

### `tests/test_workspace.py`

14. `test_repair_restores_a_worktree_whose_link_was_broken` / `test_removal_blockers_names_a_lock`:
    실제 git으로 worktree를 만들고 `git worktree lock` → `removal_blockers`가 unlock 명령을
    담은 이유를 낸다. `repair_worktree`는 되살아났을 때 `True`, 되살릴 수 없을 때 `False`
    (반환값이 "가정"이 아니라 "측정"임을 고정).
15. `test_remove_tree_reports_instead_of_raising`: 없는 경로 → 문자열 반환, 예외 없음.

### `tests/test_epic.py`

16. `test_an_item_with_a_broken_model_line_is_refused_before_the_queue_starts`:
    `model:` 오타가 있는 항목이 **큐 시작 전에** exit 2로 걸린다(§3a에서 추가한 검증).

---

## 8. 검증

```bash
pytest -q                      # 전량 (기존 403+ 전부 + 신규 ~16개)
pytest -q tests/test_watch.py tests/test_workspace.py tests/test_epic.py
python -m aidev.specs --base <requirement 커밋>     # 함수 명세 규약 자가 점검
```

- `pytest -q`가 **전량 통과**하고 실패 0. 기존 테스트를 고쳐서 통과시키는 것은 금지 —
  단, `test_decision_parsing`의 파라미터 **추가**는 계약 확장이므로 허용된다.
- `python -m aidev.specs`는 implement 단계에 이미 허용돼 있다(`SPEC_CHECK_TOOLS`).
  커밋 전에 스스로 돌려서 명세 위반 0을 확인해라. test 단계가 같은 검사를 다시 하고,
  걸리면 slice가 실패한다.
- 수동 확인 1건(파일 하나로 끝난다): 아무 요구사항에 `approval: plan` + 미지 키 한 줄을
  넣고 `--dry-run` → 경고 한 줄이 찍히고 나머지 출력은 그대로인지.
- 임시 파일 잔재 확인: `git status --porcelain -uall`이 계획한 파일만 보여줄 것.

---

## 9. 위험과 대응

| 위험 | 왜 위험한가 | 대응 |
| --- | --- | --- |
| **이미 구현된 것을 다시 쓴다** | §1a/§1c/§2는 코드도 테스트도 있다. 다시 쓰면 그 자체가 이 slice가 청산하려는 "선구현 사태"의 반복이고, 리뷰 불가능한 diff가 된다 | 0절의 표대로 **읽고 확인만** 한다. 만지지 않는다. 실행하면서 반증(예: `--model-stage`가 실제 argv에 안 들어감)이 나오면 그때만 최소 수정하고 RESULT에 근거를 적는다 |
| 프롬프트 템플릿 수정이 기존 테스트를 깬다 | `{approval}` 슬롯 추가로 공백/줄바꿈이 달라질 수 있다 | "조건 없으면 지금과 바이트 동일" 불변식을 테스트 3번으로 고정. 기존 프롬프트 단언은 부분 문자열이라 안전하다 |
| `approval_conditions`가 state를 변형 | `stage_entry`가 `setdefault`를 쓴다. 프롬프트 만들다가 state가 자라면 단일 writer 규율이 흐려진다 | 읽기 전용 구현 + 테스트 4번에서 deepcopy 비교 |
| STALE 임계값이 살아 있는 run을 죽었다고 본다 | 오래 걸리는 Bash tool 호출 중에는 이벤트가 없어 `live.json`이 안 바뀐다 | 1800초(= `VERIFY_TIMEOUT_S`와 같은 크기). 표시용 10초와 **다른 상수**임을 코드 주석과 README에 명시. 못 재면 stale로 보지 않는다 |
| `--repo` 스코프가 정작 slice run을 다 걸러낸다 | 단계 run은 worktree 경로로 기록된다(`repo=cfg.cwd`) | `default_worktree_root`까지 스코프에 포함하고 테스트 11번으로 고정. 커스텀 worktree-root는 한계로 문서화 |
| `git worktree repair`가 없는 git / 못 되살리는 상태 | 구버전 git, admin 디렉터리 소실 | `check=False`로 부르고 **결과를 `find_worktree`로 측정**한다. 실패해도 (3) 고아 폴더 경로가 출구를 보장한다 |
| discard가 지우면 안 될 폴더를 지운다 | `remove_tree`는 되돌릴 수 없다 | 호출 조건을 세 개 모두 만족할 때로 제한: ① state.json이 기록한 그 slice의 workspace 경로 ② `find_worktree`가 **아무 worktree로도 등록하지 않음** ③ 사람이 `--discard`를 직접 침. 브랜치는 여전히 먼저 지우지 않는다 |
| 함수 명세 규약 위반으로 이 slice가 실패 | 손대는 함수 다수가 지금 `@param`이 없다 | 1절 머리의 목록대로 전부 채운다. 커밋 전 `python -m aidev.specs --base <requirement 커밋>` |
| 미지 키 경고가 시끄럽다 | 사람이 front matter에 메모를 쓴다 | 에러가 아니라 한 줄 경고. 발사 경로마다 정확히 한 번(`_config`) |

---

## 10. 하지 않는 것

- Write 차단 훅(`aidev/hooks/write_guard.py`)·명세 검사(`aidev/specs.py`) **재작업** —
  gen2 A가 끝냈다. 이 slice는 그 검사를 **통과하는 쪽**이다.
- `desktop/` 일체(UI). `parseDecision`의 승인 문구 미지원은 알면서 남긴다.
- v0.5 계열(인덱스·브리핑).
- §1a·§1c·§2 재구현. 새 규격 확장(예: 반려 문서 스키마 추가, 모델 별칭)도 하지 않는다.
- `reporter.STALE_AFTER_S`(표시용 10초) 변경 — 다른 질문에 답하는 다른 숫자다.
- 3.9 호환 코드로의 후퇴. `requires-python`을 올려서 **거부**하는 것이 요구사항이다.
