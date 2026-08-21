# 작업 지시서(work order) 구조화 + 기계 검증 — 구현 계획

## 0. 한 문장

plan 산출물에 **고정 열 표로 된 작업 지시서 절**을 강제하고(세션 0에서 결정론적으로 파싱·검증), 파일을 바꾸는 모든 실행의 **stage commit 직전에 최종 diff를 지시서와 기계 대조**한다. 신규 파일·미선언 삭제는 즉시 FAIL, 미선언 수정은 `unplanned_modified`로 분류하고 에이전트의 사유 표와 완전 일치를 요구한다.

---

## 1. 작업 지시서 (이 slice 자신의 것)

| 동사 | 대상 경로 | symbol | 책임 |
| --- | --- | --- | --- |
| CREATE | aidev/workorder.py |  | 지시서 파싱·경로 검증·심볼 해석·digest·사후 대조 순수 로직 |
| CREATE | tests/test_workorder.py |  | 파서/검증/경로/digest/분류 단위 테스트 |
| MODIFY | aidev/pipeline.py |  | 봉인·게이트·scope check 호출 지점, 프롬프트, state 스키마, --no-worktree 거부 |
| MODIFY | aidev/workspace.py |  | 임시 index 스냅샷(diff_status / snapshot_entries), status --porcelain=v1 -z |
| MODIFY | aidev/graph/db.py |  | 파일 한정 심볼 조회(SQL은 이 파일에만 산다) |
| MODIFY | README.md |  | 지시서·사후 대조 절 추가, 보증 범위와 별도 안전 게이트 등록 |
| MODIFY | tests/fake_pipeline_claude.py |  | plan 스텁이 유효한 지시서를 내고, 사유 표/무지시서 모드를 갖는다 |
| MODIFY | tests/test_pipeline.py |  | Done Criteria 통합 테스트, --no-worktree 거부 반영 |
| MODIFY | tests/test_workspace.py |  | 스냅샷·diff_status의 git 수준 테스트 |
| MODIFY | tests/test_verify.py |  | 엔진 verify 2단 검사 테스트 |

symbol 열이 전부 비어 있는 것은 의도적이다 — 이 slice가 기존 파일에 **새 함수를 추가**하므로, §9의 "신규 symbol 선언 금지"(plan 검증 시점에 없는 symbol은 해석 0개로 FAIL) 규칙에 따라 파일 단위 MODIFY로 선언한다.

---

## 2. 설계 결정 (요구사항 조항 → 구현)

### 2.1 절 인식과 표 문법 (§1, §2, §6)

- 지시서 절 제목: **`## 작업 지시서`**. 인식 규칙 = `#`~`######` 헤딩 한 줄, `#`/공백/후행 `:` 제거 후 텍스트가 `작업 지시서` 또는 `WORK ORDER`(대소문자 무시)와 정확히 같을 것.
- 사유 절 제목: **`## 범위 밖 수정 사유`** (요구사항이 고정).
- **fenced code block 안의 헤딩·표는 절로 인식하지 않는다.** 파싱 전에 ` ``` ` / `~~~` 펜스(3자 이상, info string 허용)를 추적해 내부 줄을 통째로 공백 처리한 사본을 만든다.
- 인식 가능한 절이 **2개 이상이면 FAIL** (지시서 절·사유 절 각각).
- 표는 고정 열이다. 헤더는 정확히 `| 동사 | 대상 경로 | symbol | 책임 |`(symbol만 대소문자 무시), 다음 줄은 구분자 행(각 셀이 `:?-{3,}:?`), 그 뒤가 데이터 행. 절 안에서 `|`로 시작하는 줄인데 셀이 4개가 아니면 FAIL. 표가 없거나 데이터 행이 0개면 FAIL.
- 셀 분리는 자체 구현: `\|`는 리터럴 파이프, 그 외 `|`가 구분자. 앞뒤 빈 셀 하나씩 제거.
- **LLM 파싱 금지** — 위 전부가 정규식/문자열 처리다.
- 사유 표는 `| 경로 | 사유 |` 2열, 같은 규칙.

### 2.2 경로 검증 (§1, §6)

`normalize_path(raw) -> (path, error)`:

거부 대상 — 빈 값 / 역슬래시(`\`) 포함 / 절대경로(`/` 시작, `X:` 드라이브, `//` UNC) / 세그먼트 `.` 또는 `..` / 빈 세그먼트 / 후행 `/` / 콜론(`:`) 포함(ADS) / 제어문자(`< 0x20`, `0x7f`) / 세그먼트가 Windows 예약 장치명(`CON PRN AUX NUL COM1-9 LPT1-9`, 확장자 앞 stem 기준, 대소문자 무시) / 세그먼트 후행 점 또는 공백.

symlink/junction 탈출: `os.path.realpath(cwd / path)`가 `os.path.realpath(cwd)` 내부인지 normcase 비교로 확인(존재하지 않는 CREATE 대상도 부모가 junction이면 잡힌다). 밖이면 FAIL.

### 2.3 중복·충돌 기준 (§8)

경로별로 묶어서:
- 같은 경로에 **서로 다른 동사** → FAIL
- 같은 `(경로, symbol)` 조합 중복(빈 symbol 두 줄 포함) → FAIL
- 같은 경로·같은 동사·서로 다른 non-empty symbol → **허용**
- 같은 경로에 symbol 없는 행과 symbol 행이 함께 → FAIL
- CREATE 행의 symbol이 비어 있지 않으면 FAIL (존재하지 않는 파일의 symbol은 해석 불가, §9)

### 2.4 실존·해석 검증 (§1, §2)

기준 디렉터리는 `cfg.cwd`(worktree).
- CREATE: `(cwd/path)`가 **존재하면** FAIL(충돌).
- MODIFY / REFERENCE: 파일로 존재하지 않으면 FAIL.
- symbol이 있으면 Function DB에서 **해당 경로 안** 해석이 정확히 1개여야 한다. 0개 → FAIL(메시지에 "새 함수라면 symbol을 비우고 파일 단위 MODIFY로 선언하라"), 2개 이상 → FAIL(좌표 나열).
- symbol이 없으면 엔진은 **아무 함수도 추정하지 않는다**.

`GraphDB.find_in_file(path, symbol)` 신설:
```sql
SELECT * FROM functions
 WHERE path = ? AND (qualname = ? OR name = ? OR qualname LIKE ?)   -- '%.'+symbol
 ORDER BY lineno
```
같은 파일 안 동명 bare name 2개는 그대로 2행이 나와 ambiguous FAIL이 된다.

### 2.5 그래프 요구 (§6)

symbol 행이 하나라도 있으면:
- `cfg.graph_hook`이 꺼져 있으면(`--no-graph`) plan FAIL.
- `graph.db_path` 없으면 `update_repo`, `is_dirty`면 `refresh_if_dirty`, meta `base_commit`이 현재 HEAD와 다르면 `update_repo`. 그 뒤에도 dirty거나 열리지 않거나 예외면 **plan FAIL**(fail-closed, 조용한 skip 없음).
symbol 행이 하나도 없으면 그래프를 열지 않으며 `--no-graph`가 허용된다.

### 2.6 digest 이원화 (§9)

- `document_digest` = `sha256(plan.md 전체 바이트)` — **재승인 판단용**
- `scope_digest` = canonical work-order digest = 정렬된 `verb\tpath\tsymbol` 줄들의 sha256 — 범위 비교·계측용(책임 문구는 제외)

`ensure_plan_sealed`와 scope check의 재승인 트리거는 **document_digest 불일치 기준**.

### 2.7 봉인(seal)과 재승인 (§6, §7, §8)

- **plan 커밋 직전(세션 0)**: `finish_stage`가 plan.md를 쓴 직후 파싱+검증. 실패 → 사유 목록과 함께 plan stage failed, **커밋하지 않음**, `fail_slice`. 승인 게이트 앞에서 걸러진다.
- **승인 직후**: `await_approval`이 approved를 돌려준 그 자리에서 plan.md를 **재파싱·재검증**하고 `document_digest`/`scope_digest`를 저장(봉인). 게이트 중 사람이 고친 plan.md가 이 경로로 정상 반영된다. 재검증 실패 → plan stage failed + fail_slice.
- `approval: none`이면 plan 검증 성공 시점에 곧바로 자동 봉인.
- **implement / resume / repair / amend 직전**: `ensure_plan_sealed`가 현재 plan.md의 document_digest를 비교.
  - 불일치 & plan 게이트 on → 상태 `plan_reapproval_pending`, 기존 `approvals/plan.md`를 `approvals/plan-reapproval-NNN.md`로 보관하고 삭제(다음 게이트가 새 템플릿을 쓴다), 재승인까지 중단.
  - 불일치 & `approval: none` → 사람 게이트를 만들지 않고 재검증·**자동 재봉인**만 수행(§8).
  - 봉인 기록이 아예 없거나 `document_digest`가 빈 값 → **미봉인**으로 취급(빈 값이 우회로가 되지 않는다).
- **순서**: `run_pipeline` 진입부에서 amend 필터보다 **먼저** plan 게이트를 처리하고, 그 안에서 `plan_reapproval_pending`(불일치)을 **미봉인 검사보다 먼저** 처리한다. 재봉인 전에는 resume·amend 모두 차단. 재승인 후 같은 amend 사이클이 그대로 이어져 **데드락이 없다**.

### 2.8 최종 diff 산출 (§8) — 임시 index 스냅샷

`base..HEAD`에 porcelain을 덮어쓰지 않는다. 임시 Git index로 **작업트리 실물**을 base와 비교한다:

```
GIT_INDEX_FILE=<tmp>  git read-tree <HEAD 또는 base>
GIT_INDEX_FILE=<tmp>  git add -A
GIT_INDEX_FILE=<tmp>  git diff --cached --name-status -z --no-renames <base>
GIT_INDEX_FILE=<tmp>  git ls-files -s -z          # mode + blob (setup 지문용)
```
- `git add -A`는 `.gitignore`를 존중하므로 `.aidev/graph/`(자기 자신을 `*`로 무시)와 `__pycache__/`는 애초에 보이지 않는다 → 제외 목록에 넣지 않는다.
- 상태 문자: `A/M/D/T`만 인정. `R/C`(--no-renames로 나오지 않아야 함)나 그 외 미지 문자는 **예외**.
- git 실패·기준 commit 없음(unborn HEAD 포함)·미지 status는 전부 **예외를 올려** scope check를 FAIL시킨다(fail-closed).
- `-z` / `--name-status -z` / `status --porcelain=v1 -z`만 쓴다 — 공백·한글·인용 경로 안전.

### 2.9 제외 목록 (§6, §8) — 디렉터리 단위 금지

`mirror_history`가 실제로 덮어쓸 **정확한 파일만** 제외:
- `.aidev/history/<slice>/requirement.md`
- `.aidev/history/<slice>/plan.md` (plan 본문이 비어있지 않을 때)
- `.aidev/history/<slice>/slice.json`
- `.aidev/history/<slice>/failure.md|diagnosis.md|progress.md` — **본진 source 파일이 실존할 때만**
- `.aidev/history/<slice>/amends/NNN.md` — state에 기록된 정확한 amend 번호만

그 외 `.aidev/**` 신규 파일(예: `.aidev/history/<slice>/evil.py`, `.aidev/slices/**` 위조 승인)은 **신규 파일 FAIL**로 잡힌다.

**setup 산출물(§9)**: `run_setup`이 성공하면 setup 직후 스냅샷에서 새로 생긴 경로마다 `{path, mode, blob}`을 `state["setup"]["produced"]`에 저장한다. scope check는 현재 `mode+blob`이 그 지문과 **정확히 같을 때만** 제외한다. 내용이 바뀌거나 삭제되면 정상 scope 대상으로 복귀하고, 이미 CREATE/MODIFY로 선언된 경로에는 setup 제외를 적용하지 않는다.

### 2.10 분류 규칙 (§4)

effective order로부터: `write_declared = CREATE ∪ MODIFY 경로`, `deletable = MODIFY 경로`.

| diff status | 조건 | 결과 |
| --- | --- | --- |
| A | write_declared에 없음 | **FAIL** (지시서 밖 신규 파일) |
| D | MODIFY에 없음 | **FAIL** (선언 없는 삭제) |
| M/T | write_declared에 없음 | `unplanned_modified` |
| A/M/D/T | 선언됨 | 통과 |

REFERENCE는 읽기 입력 목록일 뿐 쓰기 권한이 아니다 — REFERENCE만 선언된 경로의 수정은 unplanned, 추가/삭제는 FAIL. **트레이 밖 읽기는 실패시키지 않는다**(계측만; 이번 slice는 읽기 계측을 추가하지 않는다 — 2-2 몫).

### 2.11 사유 표 대조 (§4, §7)

- 사유 표는 해당 실행 **최종 응답의 `## 범위 밖 수정 사유` 절에서만** 고정 형식으로 파싱한다. unplanned가 없으면 절을 생략해도 된다.
- `declared_now ⊆ current_unplanned` (여분·미존재 경로 → FAIL), 중복 행 → FAIL, 잘못된 경로 형식 → FAIL.
- `required = current_unplanned − 이전 scope check에서 이미 사유가 선언된 경로`. `required ⊆ declared_now`가 아니면 FAIL(누락).
  - 단일 실행에서는 `declared_now == current_unplanned` 완전 일치와 동치다(Done Criteria 그대로).
  - 이전 사유를 이어받는 것은 **amend 데드락 방지**용이다: 기준 커밋 대비 최종 diff는 예전 실행이 남긴 unplanned도 계속 포함하므로, 그것까지 매 실행 재선언하게 만들면 amend가 영원히 통과하지 못한다.
- **사유 선언은 지시서를 확장하지 않는다** — 경로는 끝까지 `unplanned_modified`로 남고 effective order에 들어가지 않는다.

### 2.12 unplanned 재계산 (§7)

`state["scope_checks"]`는 **감사 이력으로 누적**(append-only). `state["unplanned_modified"]`는 매 검사마다 **기준 커밋 대비 현재 최종 diff + effective_scope로 재계산**해 통째로 교체한다. 원복된 경로는 diff에서 사라져 현재 목록에서 빠지고, 나중에 정식 MODIFY로 선언되면 declared로 옮겨간다. 감사 이력에는 둘 다 남는다.

### 2.13 엔진 verify 2단 검사 (§8, §9)

엔진 실행에는 **사유 주체가 없다** → 무관용. `verify.run_commands` 호출 **직전**과 **직후** 각각 독립적으로 `diff_status(cwd, "HEAD")`를 본다.
- 직전 비어있지 않음 → `"verify 시작 전 작업트리가 dirty하다"` FAIL (명령 실행하지 않음)
- 직후 비어있지 않음 → `"verify가 저장소를 바꿨다"` FAIL
선언된 MODIFY 파일을 verify가 고쳐도 FAIL이다. (상태 문자 비교가 아니라 실행 전후 각각의 검사다.)

### 2.14 하위 호환과 --no-worktree (§5, §7)

- `launch_slice`가 새 slice의 state에 `work_order_required: True`를 심는다. 이 키가 없는 state = **legacy**(v0.2/v0.3 시절 slice)로, 봉인·사후 대조를 전부 skip한다.
- 새 plan과 replan은 지시서가 없으면 FAIL(같은 plan 검증 경로).
- `launch_slice`에서 `--no-worktree`(plan is None)면 **즉시 `PipelineError`**. `continue_slice`/`amend_slice`에서 `work_order_required`인데 `workspace`가 None이어도 `PipelineError`. legacy slice의 resume만 workspace 없이 계속된다.
- 비활성화 스위치(`--no-work-order` 류)는 **만들지 않는다** — 차단 장치에 우회로를 두지 않는다.

---

## 3. 파일별 변경

### 3.1 `aidev/workorder.py` (신규, 순수 로직 — pipeline을 import하지 않는다)

```python
VERB_CREATE, VERB_MODIFY, VERB_REFERENCE = "CREATE", "MODIFY", "REFERENCE"
ORDER_HEADINGS  = ("작업 지시서", "WORK ORDER")
REASON_HEADING  = "범위 밖 수정 사유"
ORDER_COLUMNS   = ("동사", "대상 경로", "symbol", "책임")
REASON_COLUMNS  = ("경로", "사유")

@dataclass class Item:      verb, path, symbol, note, row
@dataclass class Order:     items: List[Item]; errors: List[str]
@dataclass class Reasons:   rows: List[Tuple[str, str]]; errors: List[str]
@dataclass class ScopeVerdict:
    ok: bool; failures: List[str]; unplanned: List[str]
    added: List[str]; deleted: List[str]; declared: List[str]

def mask_fenced(text) -> str                      # 펜스 내부를 공백 줄로
def find_sections(text, titles) -> List[int]      # 인식된 헤딩 줄 번호들
def split_row(line) -> Optional[List[str]]        # \| 이스케이프 처리
def parse_table(lines, columns) -> (rows, errors)
def parse_work_order(text) -> Order               # 절 0개/2개↑, 표 없음/열 오류 모두 errors
def parse_reasons(text) -> Reasons
def normalize_path(raw) -> (path, error)
def escapes_root(root, rel) -> bool               # realpath 비교
def check_duplicates(items) -> List[str]          # §8 중복·충돌 기준
def items_digest(items) -> str                    # scope_digest
def document_digest(data: bytes) -> str
def render_table(items) -> str                    # 프롬프트/승인 화면용 재렌더
def classify(status, order, excluded) -> ScopeVerdict
def match_reasons(unplanned, declared, carried) -> List[str]
```

`parse_work_order`는 **표 파싱만** 한다(파일시스템·DB 없음) — 단위 테스트가 저장소 없이 돈다. 실존/심볼/그래프 검증은 pipeline 쪽 `validate_work_order`가 이 결과를 받아 수행한다.

### 3.2 `aidev/workspace.py`

- `run()`/`_spawn()`에 `env: Optional[Dict[str,str]]` 인자 추가(기본 None → 현재 동작 그대로, `os.environ`에 덮어쓰기 병합).
- `@dataclass TreeSnapshot: status: Dict[str,str]; entries: Dict[str, Tuple[str,str]]`
- `def snapshot(repo, base) -> TreeSnapshot` — 임시 디렉터리에 index 파일을 만들어 read-tree → add -A → diff --cached --name-status -z --no-renames base → ls-files -s -z. `finally`로 임시 파일 삭제. 실패는 `GitError`, 미지 status는 `ValueError`.
- `def diff_status(repo, base) -> Dict[str,str]` — `snapshot(...).status`. **요구사항이 지목한 이름**이며 fail-closed의 진입점.
- `def status_entries(repo) -> List[Tuple[str,str]]` — `status --porcelain=v1 -z -uall` 파싱(경로 인용/이스케이프 문제 없음). `_pending_files`(커밋 파일 수 상한)와 임시파일 검사의 untracked 스캔을 이쪽으로 옮긴다. readonly 단계의 before/after 문자열 비교(`repo_changes`)는 같은 모양끼리 비교하는 것이므로 그대로 둔다.

### 3.3 `aidev/graph/db.py`

- `GraphDB.find_in_file(path: str, name: str) -> List[sqlite3.Row]` — 위 SQL. `find()`와 달리 **경로를 좁히고 단계적 폴백을 하지 않는다**(모든 후보를 한 번에 돌려줘야 ambiguous가 보인다).

### 3.4 `aidev/pipeline.py`

상수/상태:
```python
STATUS_PLAN_REAPPROVAL = "plan_reapproval_pending"
WORK_ORDER_KEY = "work_order"
```

새 함수(전부 명세 주석 포함):

| 함수 | 역할 |
| --- | --- |
| `work_order_required(state) -> bool` | legacy 판정 (`state.get("work_order_required")`) |
| `open_graph_for_order(cfg, items)` | symbol 행이 있을 때만 그래프 보장·개방, 실패 시 사유 문자열 |
| `validate_work_order(cfg, items, base_dir) -> List[str]` | 경로·중복·실존·심볼·그래프 전 검증, 사유 목록 |
| `read_work_order(cfg, text, base_dir) -> (items, errors)` | 파싱+검증 한 묶음 |
| `seal_plan(cfg, rec, state) -> Optional[str]` | plan.md 재파싱·재검증 후 두 digest 저장 |
| `plan_seal_status(rec, state) -> str` | `ok` / `stale` / `unsealed` / `legacy` |
| `ensure_plan_sealed(cfg, rec, state) -> Optional[str]` | mutating 실행 직전 digest 검사 |
| `handle_plan_gate(cfg, rec, state) -> Optional[int]` | `stale`→재승인 게이트(or 자동 재봉인), `unsealed`→실패. **amend 필터보다 먼저** |
| `reopen_plan_gate(rec, state)` | 기존 승인 파일 보관·삭제, 재승인 기록 append |
| `effective_order(state) -> List[Item]` | plan 지시서 + `amends[].work_order` 누적 합산(원본 불변) |
| `engine_excluded(cfg, rec, state, order) -> Dict[str, Optional[fingerprint]]` | mirror 정확 파일 + setup 지문 |
| `scope_check(cfg, rec, state, stage, label, text) -> Optional[str]` | 최종 diff 대조 본체, 감사 기록·unplanned 재계산 |
| `engine_verify_guard(cfg, rec, state, when) -> Optional[str]` | verify 전/후 HEAD 대비 무관용 검사 |
| `record_setup_products(cfg, state, before)` | setup 산출물 지문 저장 |
| `work_order_note(state) -> str` | 승인 화면·프롬프트용 표 재렌더 |
| `_scope_lines(state) -> List[str]` | `render_summary`의 Scope 줄 |

변경 지점:

1. `new_state` — 변경 없음. `launch_slice`가 `state["work_order_required"] = True`를 설정(선택 키, STATE_SCHEMA 2 유지).
2. `launch_slice` — `plan is None`이면 `PipelineError`(§7). 기존 "warning: --no-worktree" 경로는 legacy resume 전용으로 남는다.
3. `_plan_workspace` — non-git 저장소 거부 문구에서 `--no-worktree` 권유를 제거(더 이상 새 slice에 열려 있지 않다).
4. `continue_slice` / `amend_slice` — `work_order_required(state)`인데 `ws is None`이면 `PipelineError`.
5. `amend_slice` — 지시문에 지시서 절이 있으면 파싱·검증(실패 시 사이클을 열기 전에 거부), `open_amend`에 넘겨 `amends[-1]["work_order"]`로 저장. 절이 없으면 아무것도 추정하지 않고 범위를 넓히지 않는다.
6. `open_amend(rec, state, instruction, stages, work_order=None)` — 인자 추가.
7. `finish_stage` — plan 분기 끝에서 `read_work_order` 실행, 실패 시 사유 목록을 반환(→ stage failed, 커밋 안 됨). 성공 시 items를 state에 임시 보관.
8. `run_pipeline`:
   - 진입부(`resume_quota_wait` 뒤, `amend = current_amend(state)` **앞**)에서 `handle_plan_gate`. 반환값이 exit code면 즉시 반환.
   - plan 게이트 approved 직후 `seal_plan` 호출(실패 → fail_slice).
   - `approval: none`이고 plan이 방금 통과했으면 같은 자리에서 자동 봉인.
   - mutating stage(`implement`, agent `test`) 실행 **직전** `ensure_plan_sealed`.
   - `finish_stage` 통과 후 **`commit_stage` 앞**에 `scope_check`. 실패 → stage `failed`, `note_stage_failure`, `fail_slice`(커밋 없음).
   - 게이트 note에 `work_order_note(state)` 추가(사람 승인 화면 표시).
9. `run_verify`:
   - `verify.run_commands` 전후로 `engine_verify_guard`.
   - repair implement 후 `commit_stage` 앞에 `scope_check(..., label="repair{N}/implement", text=run.text)`.
   - repair implement 직전 `ensure_plan_sealed`.
10. `run_setup` — 실행 전 스냅샷, 성공 후 `record_setup_products`.
11. `render_summary` — `_scope_lines` 추가: `Scope     unplanned N   scope checks M` + 최근 실패 사유 1줄(없으면 침묵 → 기존 출력 그대로).
12. 프롬프트:
    - `_PLAN_PROMPT`에 `_WORK_ORDER_RULES` 블록 추가(표 양식, 세 동사, symbol 규칙, §9 신규 symbol 금지, 경로 규칙).
    - `_IMPLEMENT_PROMPT`/`_TEST_PROMPT`에 `_WORK_ORDER_NOTE` 슬롯(구조화된 표 + 규약 문구 + 사유 표 양식). 지시서가 없는 legacy면 빈 문자열 → 프롬프트 바이트 그대로.
    - `build_prompt(..., work_order: str = "")` 인자 추가.

프롬프트에 들어갈 규약 문구(고정):

> 지시서에 없는 파일 수정 금지, 부득이한 수정은 사유 표로 선언.
> 지시서의 CREATE에 없는 새 파일을 만들면 그 실행은 즉시 실패한다. 선언되지 않은 기존 파일을 고쳤다면 최종 응답 맨 끝에 `## 범위 밖 수정 사유` 절을 두고 `| 경로 | 사유 |` 표로 항목당 한 줄씩 선언하라. 없으면 절을 생략한다.

### 3.5 `README.md`

- `### 검증의 결정론화 (v0.5)` 뒤에 `### 작업 지시서와 사후 대조 (v0.8)` 신설: 표 양식, 세 동사, symbol 규칙, plan FAIL 사유, 사후 대조 규칙, 사유 표, digest 이원화, legacy 예외.
- **보증 범위 명시**: 사후 대조의 보증은 "Git이 관찰하는 worktree 내부 변경"이다. 문서 전체에서 "모든 신규 파일 즉시 FAIL" 표현을 이 범위로 한정해 서술.
- `### 안전핀`에 별도 항목 등록: "worktree 밖 쓰기 차단 — 범위 밖, 미구현. 사후 대조는 worktree 내부만 본다."
- `--no-worktree` 설명을 "legacy slice의 resume 전용"으로 정정.

### 3.6 `tests/fake_pipeline_claude.py`

- `work_order_table()` — 이 실행이 실제로 만들 파일과 정확히 일치하는 CREATE 행을 생성한다: 기본 `aidev/thing.py`, `AIDEV_FAKE_FILES`면 `generated-N.txt` N개, `AIDEV_FAKE_SPECLESS`면 `aidev_generated.py`, `AIDEV_FAKE_TEMPFILE`이면 그 이름들, `AIDEV_FAKE_MIGRATION`이면 `backend/migrations/003_add_seat.sql`, `AIDEV_FAKE_BIG_PLAN`이면 module 12+6개.
- plan 텍스트에 표를 덧붙인다(`1. touch aidev/thing.py` 줄은 유지 — 기존 단언 보존).
- 새 스위치: `AIDEV_FAKE_NO_WORK_ORDER`(표 생략), `AIDEV_FAKE_ORDER`(표 본문 직접 주입 — 미존재 MODIFY/경로 탈출/동사 충돌/모호 symbol 시나리오용), `AIDEV_FAKE_REASONS`(implement/test 최종 응답에 `## 범위 밖 수정 사유` 절 부착), `AIDEV_FAKE_EDIT`(선언·미선언 기존 파일 수정), `AIDEV_FAKE_DELETE`(파일 삭제), `AIDEV_FAKE_TEST_WRITE`(agent test 단계가 파일을 쓴다).
- `AIDEV_FAKE_TEXT`가 plan 단계를 덮을 때도 표는 붙인다(`AIDEV_FAKE_NO_WORK_ORDER`로만 뗀다) — 기존 epic 테스트가 decompose만 노리므로 영향 없음.

---

## 4. state.json (전부 선택 키, STATE_SCHEMA 2 유지)

```json
"work_order_required": true,
"work_order": {
  "items": [{"verb": "MODIFY", "path": "aidev/pipeline.py", "symbol": "", "note": "..."}],
  "scope_digest": "sha256:...",
  "document_digest": "sha256:...",
  "sealed": true,
  "sealed_at": "2026-08-21T22:00:00+09:00",
  "graph": {"used": false, "base_commit": ""}
},
"plan_reapprovals": [{"n": 1, "at": "...", "kept": "approvals/plan-reapproval-001.md"}],
"setup": {"...": "...", "produced": [{"path": "backend/x", "mode": "100644", "blob": "…"}]},
"scope_checks": [
  {"at": "...", "stage": "implement", "label": "implement", "ok": false,
   "added": ["evil.py"], "deleted": [], "unplanned": ["README.md"],
   "declared": ["README.md"], "reason": "…"}
],
"unplanned_modified": [{"path": "README.md", "reason": "…", "stage": "implement", "at": "..."}],
"amends": [{"n": 1, "work_order": {"items": [...]}}]
```

---

## 5. 실행 흐름 (변경 후)

```
launch          --no-worktree? -> PipelineError
run_pipeline    resume_quota_wait -> restore_extensions
                -> handle_plan_gate            ← amend 필터보다 먼저
                     stale  -> 재승인 게이트(or 자동 재봉인)
                     unsealed(non-legacy) -> fail
                -> amend 필터 -> per stage:
   plan          run_stage -> finish_stage(plan 저장 + 지시서 파싱/검증)
                 -> commit_stage -> 게이트 -> approved -> seal_plan
   implement     ensure_plan_sealed -> run_setup(+지문) -> run_stage
                 -> finish_stage -> scope_check -> commit_stage
   test(engine)  engine_verify_guard(before) -> run_commands
                 -> engine_verify_guard(after) -> spec/temp check
                 -> 실패 시 repair implement -> scope_check -> commit_stage
   test(agent)   ensure_plan_sealed -> run_stage -> finish_stage
                 -> scope_check -> commit_stage
```

---

## 6. 테스트 계획

### 6.1 `tests/test_workorder.py` (단위, git 불필요)

- 표 파싱: 정상 3행 / 열 4개 아님 / 헤더 불일치 / 구분자 없음 / 표 없음 / 데이터 0행 / `\|` 이스케이프 / 한글·공백 경로
- 절 인식: 0개, 2개(FAIL), fenced block 안 제목은 절이 아님(FAIL 아님), 사유 절 2개(FAIL)
- 동사: 소문자 허용, 오타 FAIL
- 경로: `../x`, `/abs`, `C:\x`, `a\b`, `a//b`, `a/`, `a:b`(ADS), 제어문자, `CON.py`, `aux/x.py`, `a./x`, `a /x`
- 중복·충돌 §8 전 조합(같은 경로 다른 동사 FAIL / (경로,symbol) 중복 FAIL / 다른 symbol 허용 / bare+symbol 혼용 FAIL / CREATE에 symbol FAIL)
- digest: 행 순서를 바꿔도 `scope_digest` 동일, 책임 문구만 바뀌면 동일, symbol이 바뀌면 다름; `document_digest`는 바이트 하나에도 반응
- `classify`: A/D/M 각 케이스, REFERENCE-only 경로
- `match_reasons`: 완전 일치 / 누락 / 여분 / 중복 / 미존재 경로 / carried-forward

### 6.2 `tests/test_workspace.py` (git 수준)

- `snapshot`/`diff_status`: 수정·추가·삭제·삭제 후 복원·추가 후 삭제 fixture (§8 필수 3종)
- 원복(수정 후 되돌리기)이 diff에서 사라진다
- 공백·한글·인용이 필요한 경로가 온전히 나온다
- 기준 commit 없음 / 잘못된 base → 예외
- gitignore된 파일은 안 보인다
- 임시 index 사용 후 **실제 index와 HEAD가 그대로**임을 확인(부작용 없음)
- `status_entries`: `-z` 파싱, 공백 경로, rename

### 6.3 `tests/test_pipeline.py` (통합 — Done Criteria 대응)

| Done Criteria | 테스트 |
| --- | --- |
| 지시서 포함 plan 통과 | `test_a_plan_with_a_work_order_passes_and_is_sealed` |
| 미존재 MODIFY 대상 → plan FAIL | `test_a_modify_target_that_does_not_exist_fails_the_plan` |
| 지시서 없는 새 plan → FAIL | `test_a_plan_without_a_work_order_fails` |
| 경로 탈출·동사 충돌 → FAIL | `test_path_escape_and_verb_conflict_fail_the_plan` (parametrize) |
| symbol 해석 0개 → plan FAIL | `test_an_unknown_symbol_fails_the_plan` |
| 같은 파일 동명 bare name 2개 → ambiguous | `test_two_functions_with_the_same_bare_name_are_ambiguous` (fixture 파일 작성) |
| symbol 있는데 `--no-graph` | `test_a_symbol_row_refuses_no_graph` |
| 지시서 밖 신규 파일 → FAIL | `test_a_file_outside_the_order_fails_before_the_commit` (커밋 안 됨 확인) |
| MODIFY에 없는 삭제 → FAIL | `test_an_undeclared_deletion_fails` |
| unplanned + 사유 표 일치 → 통과 | `test_an_unplanned_edit_passes_when_the_reason_table_matches` |
| 불일치·누락·여분 → FAIL | `test_a_mismatched_reason_table_fails` (parametrize 3종) |
| 사유 절 2개 → FAIL | `test_two_reason_sections_fail` |
| 사유 선언이 지시서를 넓히지 않음 | `test_a_declared_reason_stays_unplanned_forever` |
| unplanned 원복 | `test_reverting_an_unplanned_file_drops_it_but_keeps_the_audit` |
| legacy resume 하위 호환 | `test_a_legacy_slice_resumes_without_a_work_order` (work_order_required 없는 state 손으로 작성) |
| 신규 slice `--no-worktree` 거부 | `test_a_new_slice_refuses_no_worktree` (기존 `test_non_git_repo_is_refused_with_a_way_out` 갱신) |
| agent test가 범위 밖 수정 → FAIL | `test_the_agent_test_stage_is_scope_checked` |
| scope FAIL 후 resume | `test_a_scope_failure_reruns_the_same_stage_on_resume` |
| `.aidev/history/<slice>/evil.py` | `test_a_new_file_under_aidev_history_is_still_a_new_file` |
| setup 산출물 지문 | `test_setup_output_is_excluded_only_while_it_matches_its_fingerprint` |
| plan 수정 → amend 중단 → 재승인 → 재개 | `test_editing_the_plan_pauses_an_amend_until_it_is_approved_again` |
| `approval: none` digest 불일치 | `test_approval_none_reseals_itself_without_a_gate` |
| digest 빈 값 우회 금지 | `test_an_empty_digest_is_not_a_way_past_the_seal` |
| amend 지시서 누적 | `test_an_amend_work_order_extends_the_effective_scope_only` |
| replan | `test_a_replan_must_produce_a_work_order_again` |

### 6.4 `tests/test_verify.py`

| Done Criteria | 테스트 |
| --- | --- |
| test_commands가 파일 생성 → FAIL | `test_a_verify_command_that_writes_a_file_fails` (tracked/untracked 2종) |
| 선언된 MODIFY 파일을 verify가 수정 → FAIL | `test_verify_touching_a_declared_file_still_fails` |
| verify 수정 실패 → resume → 명령 재실행 없이 dirty FAIL | `test_a_dirty_worktree_fails_verify_before_it_runs` (호출 카운터 파일로 재실행 없음 확인) |

### 6.5 기존 테스트 영향

- `test_non_git_repo_is_refused_with_a_way_out` — 후반부(`--no-worktree`로 성공)를 §7 거부로 교체, 전반부 메시지 단언 조정.
- 나머지 pipeline/verify/specs/epic 테스트는 스텁이 지시서를 함께 내보내므로 **의도가 보존**된다(임시파일 가드 테스트는 여전히 임시 이름 때문에 실패하고, 커밋 상한 테스트는 여전히 상한 때문에 실패한다).
- `python -m pytest -q` 전량 통과가 완료 조건.

---

## 7. 위험과 완화

| 위험 | 완화 |
| --- | --- |
| 스텁 plan이 실제 생성 파일과 어긋나 기존 테스트가 scope FAIL로 방향이 바뀐다 | `work_order_table()`을 파일을 만드는 **바로 그 env 스위치**에서 생성 — 한 곳에서 갈라지므로 어긋날 수 없다 |
| `git add -A`가 실제 index를 건드린다 | `GIT_INDEX_FILE`을 임시 파일로 지정, `finally` 삭제. 테스트로 index/HEAD 무변경 확인 |
| 큰 저장소에서 스냅샷 비용 | scope check 1회당 add -A 1회. 커밋 직전에만 돈다(스테이지당 1~2회) |
| CRLF 정규화로 M이 뜨는 오탐 | unplanned로 분류될 뿐 FAIL이 아니다. 신규/삭제만 즉시 FAIL이므로 오탐이 slice를 죽이지 않는다 |
| plan이 표 양식을 못 맞춰 FAIL 루프 | FAIL 메시지에 기대 헤더와 예시 행을 그대로 출력하고, 같은 문구를 plan 프롬프트가 이미 갖고 있다. `--replan`이 반려 사유로 그 메시지를 받는다 |
| 그래프 빌드 실패로 plan이 죽는다 | symbol 행이 있을 때만 그래프를 요구한다. 파일 단위 지시서는 그래프 없이 통과 |
| 재승인 게이트가 amend를 막는다 | `handle_plan_gate`를 amend 필터보다 먼저 두고, 재승인 후 같은 사이클로 복귀. 전용 테스트로 고정 |
| unplanned 누적이 amend를 영구 차단 | carried-forward 규칙(§2.11) + 전용 테스트 |
| `.aidev` 제외 목록이 실제 mirror와 어긋난다 | 제외 목록을 `mirror_history`가 쓰는 경로 생성 로직과 같은 헬퍼에서 유도하고, `evil.py` 통합 테스트로 고정 |
| worktree 밖 쓰기는 잡히지 않는다 | **범위 밖으로 명시**. README 안전핀에 별도 항목으로 등록만 하고 이번 slice에서 구현하지 않는다 |

---

## 8. 하지 않는 것 (확인)

컨텍스트 트레이(2-2), 빈 파일 준비(2-4), plan 산문 금지 — 손대지 않는다. REFERENCE를 읽기 allowlist로 쓰지 않고, 지정 외 읽기를 실패시키지 않는다. 지시서 검증·사후 대조를 끄는 스위치를 만들지 않는다. desktop/ 은 건드리지 않는다(state.json이 데이터를 이미 싣고 있으므로 UI는 별건이다).
