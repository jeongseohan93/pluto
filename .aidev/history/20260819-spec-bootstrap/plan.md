# PLAN — 명세 부트스트랩 (핫 패스 초벌)

## 0. 조사 결과 요약 (근거)

**그래프 DB는 이 워크트리에 없다.** `.aidev/graph/` 에는 `.gitignore`(`*`)와 `dirty` 마커만 있고 `graph.db`는 없다 — DB는 파생 캐시라 커밋되지 않기 때문이다(`aidev/graph/build.py:8-13`). PLAN 단계는 read-only라 빌드도 쿼리도 할 수 없고, MCP 그래프 툴은 권한 거부되었다. 그래서 대상 목록은 **DB가 쓰는 것과 똑같은 규칙**으로 정적으로 도출했고, implement가 실행할 **확인용 SQL**을 §5에 그대로 적어 둔다.

DB의 판정 규칙 (읽어서 확인함):
- `has_spec = 1 if function.summary else 0` — `aidev/graph/db.py:221`
- `summary` = 명세 블록의 첫 비어있지 않은 비-태그 줄 — `aidev/graph/model.py:99-131`
- 명세 블록 = `ast.get_docstring(node) or specs.comment_block_above(lines, lineno)` — `aidev/graph/pyparse.py:77`
- 커버리지 = `spec_non_test / functions_non_test` — `aidev/graph/db.py:477-478`

숫자 정합성 확인: 요구사항의 1189 함수 / 55% / 미명세 305개는 `functions_non_test ≈ 678`, `spec_non_test ≈ 373` 을 뜻한다 (373/678 = 55.0%, 678−373 = 305). 일관된다.

**호출 빈도 (aidev/ 내 호출 지점 수, `calls.callee_name` 과 같은 기준):**

| 함수 | 호출 | 함수 | 호출 |
|---|---|---|---|
| `pipeline.say` | ~130 | `pipeline.fail_slice` | 11 |
| `pipeline.now_iso` | ~26 | `workspace.head_commit` | 9 |
| `pipeline.write_state` | ~24 | `pipeline.slices_root` | 7 |
| `pipeline.stage_entry` | ~20 | `workspace.git_available` | 5 |
| `*.to_dict` (5개 클래스) | ~20 | `storage.read_live` / `list_run_dirs` | 4 |
| `pipeline.set_status` | ~17 | `workspace.is_clean` | 4 |
| `pipeline.read_state` | ~13 | `pipeline.tail_text` | 3 |

이 목록의 상위 전부가 **현재 명세가 없다**. 즉 "callers 상위"와 "핵심 모듈 public"이 사실상 같은 집합을 가리킨다.

**graph/ 패키지는 이미 dunder를 제외하면 100% 명세되어 있다** (가장 최근에 만들어진 모듈). 그래서 이 슬라이스에서 graph/ 는 0개다 — 요구사항의 8개 모듈 중 유일하게 손댈 게 없는 모듈이고, 이건 문제가 아니라 이미 끝나 있다는 뜻이다.

---

## 1. 명세 형식 결정 — docstring (주석 블록 아님)

`# 주석 블록`도 DB가 명세로 인정하지만(`comment_block_above`), **docstring을 쓴다.** 근거:

1. 대상 89개 중 약 15개가 `@property` 다. 주석 블록은 `node.lineno`(= `def` 줄) **위**만 보므로 데코레이터가 있으면 `@property` 와 `def` 사이에 주석을 끼워 넣어야 한다 — 읽을 수 없는 코드가 된다.
2. `aidev/pipeline.py:930-943` 에 이미 증거가 있다: `failure_path` / `diagnosis_path` / `progress_path` 는 docstring 형식으로 명세된 property이고, 바로 위의 `dir`~`amends_dir` 만 비어 있다. 같은 클래스 안에서 형식을 섞을 이유가 없다.
3. 저장소 전체의 기존 명세가 전부 docstring이다.

"주석 외 변경 0"은 **docstring 제거 후 AST 동등성**으로 기계 검증한다(§4). 이건 "diff에 `#` 줄만 있다" 보다 강한 보증이다 — 실행되는 문장이 하나도 이동/변경되지 않았음을 증명한다.

---

## 2. 대상 목록 — 89개 (파일별, 좌표 포함)

제외 원칙: dunder(`__init__`/`__enter__`/`__exit__` — 클래스 docstring이 계약이라는 `aidev/specs.py:217-218` 의 하우스 룰), 중첩 함수, 테스트 파일, 자잘한 private 헬퍼, desktop/(JS).

### `aidev/pipeline.py` — 40개
| 줄 | 함수 | `@param` 필요 |
|---|---|---|
| 704 | `looks_like_migration` | path |
| 715 | `migration_paths` | paths |
| 867 | `slices_root` | repo |
| 892 | `SliceRecord.dir` | — |
| 896 | `SliceRecord.requirement_path` | — |
| 900 | `SliceRecord.plan_path` | — |
| 904 | `SliceRecord.state_path` | — |
| 908 | `SliceRecord.runs_path` | — |
| 912 | `SliceRecord.rollbacks_path` | — |
| 916 | `SliceRecord.approvals_dir` | — |
| 920 | `SliceRecord.amends_dir` | — |
| 923 | `SliceRecord.amend_path` | number |
| 981 | `SliceRecord.approval_path` | stage |
| 984 | `SliceRecord.ensure` | — |
| 988 | `SliceRecord.read_state` | — |
| 991 | `SliceRecord.write_state` | state |
| 994 | `SliceRecord.read_plan` | — |
| 1000 | `SliceRecord.write_plan` | text |
| 1015 | `SliceRecord.append_run` | entry |
| 1023 | `SliceRecord.runs` | — |
| 1039 | `SliceRecord.rollbacks` | — |
| 1045 | `now_iso` | — |
| 1140 | `new_state` | slice_id, repo, gates, stages, epic |
| 1169 | `stage_entry` | state, stage |
| 1337 | `ensure_clean_repo` | repo |
| 1386 | `tail_text` | path, limit |
| 1423 | `looks_like_quota` | text |
| 2030 | `StageRun.ok` | — |
| 2046 | `OutputCapture.feed` | event |
| 2066 | `OutputCapture.text` | — |
| 2424 | `record_run` | rec, run |
| 2655 | `history_dir` | cfg, slice_id |
| 3123 | `set_status` | rec, state, status |
| 3128 | `fail_slice` | rec, state, reason |
| 3453 | `say` | message |
| 3470 | `banner` | stage, slice_id, attempt, resumed, fresh |
| 3916 | `cmd_pipeline` | args |
| 4093 | `resolve_requirement` | path, repo, flag |
| 4364 | `resume_slice` | args, repo, data_dir, repo_given |
| 5451 | `list_slices` | repo, repo_given, data_dir |

### `aidev/storage.py` — 24개
131 `recent_repos_path`(data_dir) · 186 `slugify`(value) · 191 `make_run_id`(task, now) · 209 `RunStore.events_path` · 213 `stderr_path` · 217 `prompt_path` · 221 `run_json_path` · 225 `telemetry_path` · 229 `live_path` · 233 `open` · 238 `close` · 263 `write_stderr`(line) · 269 `write_prompt`(text) · 272 `write_run_json`(meta) · 275 `write_telemetry`(data) · 306 `read_telemetry` · 309 `iter_raw_events` · 370 `read_live`(run_dir) · 374 `read_telemetry`(run_dir) · 563 `Database.close` · 572 `Database.save_run`(telemetry) · 664 `Database.recent_runs`(limit) · 670 `Database.phase_totals`(limit) · 687 `list_run_dirs`(runs_root)

### `aidev/workspace.py` — 11개
54 `git_available` · 129 `is_git_repo`(path) · 205 `branch_exists`(repo, name) · 211 `head_commit`(repo) · 261 `is_clean`(repo, tracked_only) · 286 `WorktreeEntry.stale` · 335 `find_worktree`(repo, path) · 350 `worktree_add`(repo, path, branch, start_commit) · 548 `default_worktree_root`(repo) · 578 `Workspace.to_dict` · 704 `create`(repo, plan)

### `aidev/verify.py` — 5개
70 `FailedTest.to_dict` · 85 `CommandResult.ok` · 88 `CommandResult.to_dict` · 120 `VerifyResult.first_failing` · 126 `VerifyResult.to_dict`

### `aidev/cli.py` — 5개
159 `cmd_run`(args) · 292 `cmd_report`(args) · 419 `cmd_list`(args) · 434 `cmd_stats`(args) · 475 `main`(argv)

### `aidev/runner.py` — 3개
231 `_write_prompt`(process, prompt) · 241 `_drain_stderr`(process, store) · 256 `_terminate`(process)

*(private이지만 "자잘한 헬퍼"가 아니다 — 세 개 모두 서브프로세스 수명주기를 다루고, runner.py 에서 명세 없는 함수는 이 셋뿐이라 모듈이 100%가 된다.)*

### `aidev/specs.py` — 1개
64 `Violation.to_dict`

### `aidev/graph/**` — 0개
dunder를 빼면 이미 전부 명세되어 있다.

**합계 89개** (요구사항 예상 80~120 범위 안). 미명세 305개 중 29%를 닫는다.

**예상 커버리지: 55% → ~68%** (373+89 = 462 / 678). 정확한 값은 §5의 build 후 `graph status` 출력이 정답이다.

**의도적으로 남기는 216개**: `epic.py`(47 def), `telemetry.py`(27), `reporter.py`(25), `events.py`(24), `progress.py`(6), `hooks/`, 각 모듈의 private 헬퍼·dunder·중첩 함수, 그리고 `desktop/` 의 JS/TS. 요구사항이 지정한 8개 핵심 모듈 밖이다.

---

## 3. 각 명세의 내용 규칙

`aidev/graph/model.py:21` 의 닫힌 코어를 그대로 따른다. 레이아웃은 이웃 함수를 복사한다:

```python
def stage_entry(state: Dict[str, Any], stage: str) -> Dict[str, Any]:
    """One stage's slot in state.json, made on first ask so a caller never checks.

    @param state  the slice's state.json, mutated in place
    @param stage  which stage's entry is wanted
    @flow  stages dict (created when absent) -> entry ; not a dict -> replace with pending
    """
```

```python
@property
def plan_path(self) -> Path:
    """Where the plan stage's answer lands - the one file implement is handed."""
    return self.dir / "plan.md"
```

지켜야 할 것:
- **함수명 재서술 금지.** `"""Return the plan path."""` 같은 줄은 위반이다. 한 줄은 *왜 이게 있는지 / 언제 불리는지 / 무엇을 보장하는지* 를 말해야 한다.
- `@param` 은 **모든 파라미터**에 (self/cls 제외 — `aidev/specs.py:197-211` 의 규칙). 하나라도 빠지면 verify가 `KIND_PARAM` 으로 스테이지를 실패시킨다.
- `@flow` 는 **분기·루프가 있을 때만**. `now_iso` 나 property 접근자에는 붙이지 않는다.
- `주요 내부 변수: name(설명)` 은 로컬 변수가 진짜로 이름값을 할 때만 (`_scan`, `resolve_calls` 처럼). `@flow` 바로 다음 줄에 빈 줄 없이 붙인다 — 기존 스타일이 그렇고, `parse_spec` 은 이를 `@flow` 값의 연속으로 흡수한다.
- 인자 없는 함수의 `Takes no arguments.` 접미사는 **`graph/` 안에서만 쓰는 스타일**이다. pipeline/storage/workspace 에는 붙이지 않는다 (`SliceRecord.failure_path` 등 기존 사례가 안 붙인다).
- 줄 길이는 주변 코드에 맞춰 ~100자.
- 본문을 읽고 쓴다. 예: `set_status` 는 "상태를 바꾼다"가 아니라 "상태를 바꾸고 **같은 호출에서 디스크에 공표한다** — 메모리에만 있는 상태는 `--list` 와 resume 에 보이지 않는다".

---

## 4. 코드 변경 0 — 기계 검증

`BASE` = plan 커밋(없으면 requirement 커밋). implement 도중엔 편집이 아직 커밋 전이므로 `git diff <BASE>` (작업 트리 비교)를 쓴다.

```bash
BASE=$(git log -1 --format=%H --grep='slice(20260819-spec-bootstrap): plan')
BASE=${BASE:-$(git log -1 --format=%H --grep='slice(20260819-spec-bootstrap): requirement')}
echo "base=$BASE"
```

**검증 A — 건드린 경로가 `aidev/*.py` 뿐인가** (`.aidev/` 는 파이프라인 자신의 기록이라 제외):

```bash
git diff --name-only "$BASE" -- . ':(exclude).aidev' | grep -vE '^aidev/.*\.py$' \
  && echo 'FAIL: unexpected path' || echo 'paths OK'
```

**검증 B — docstring을 떼면 AST가 완전히 동일한가** (`ast.dump` 는 기본적으로 줄 번호를 포함하지 않으므로 삽입에 의한 라인 이동은 무시된다):

```bash
python - "$BASE" <<'PY'
import ast, subprocess, sys
base = sys.argv[1]

def strip(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return tree

files = subprocess.run(["git", "diff", "--name-only", base, "--", "aidev"],
                       capture_output=True, text=True).stdout.split()
bad = []
for path in files:
    old = subprocess.run(["git", "show", "{0}:{1}".format(base, path)],
                         capture_output=True, text=True).stdout
    new = open(path, encoding="utf-8").read()
    if ast.dump(strip(ast.parse(old))) != ast.dump(strip(ast.parse(new))):
        bad.append(path)
print("checked {0} file(s)".format(len(files)))
print("SPEC-ONLY OK" if not bad else "CODE CHANGED IN: " + ", ".join(bad))
sys.exit(1 if bad else 0)
PY
```

**검증 C — verify가 돌릴 명세 검사를 미리 그대로 돌린다** (`spec_check:` 가 front matter에 없으므로 기본 ON — `pipeline.resolve_spec_check`). `@param` 누락을 스테이지 실패 전에 잡는다:

```bash
python - "$BASE" <<'PY'
import subprocess, sys
from pathlib import Path
sys.path.insert(0, ".")
from aidev import specs
base = sys.argv[1]
diff = subprocess.run(["git", "diff", "--unified=0", base, "--", "aidev"],
                      capture_output=True, text=True).stdout
found = []
for path, touched in sorted(specs.changed_lines(diff).items()):
    if specs.is_exempt(path):
        continue
    new = Path(path).read_text(encoding="utf-8")
    old = subprocess.run(["git", "show", "{0}:{1}".format(base, path)],
                         capture_output=True, text=True).stdout
    found += specs.check_text(path, new, touched, old)
print("\n".join(str(v) for v in found) or "spec self-check: clean")
sys.exit(1 if found else 0)
PY
```

**검증 D — 테스트:** `python -m pytest -q` (설치본이 아니라 워크트리를 테스트하려면 `python -m` 이 필수).

---

## 5. 커버리지 측정 (before → after)

워크트리에 `graph.db` 가 없으므로 **첫 편집 전에** before를 떠야 한다. 설치된 `aidev` 바이너리가 아니라 워크트리 코드를 쓴다 (`aidev/cli.py:489` 에 `__main__` 가드가 있어 `python -m aidev.cli` 가 동작하고, 상대 임포트도 정상이다).

**before (편집 시작 전 첫 명령):**
```bash
python -m aidev.cli graph build --repo .
python -m aidev.cli graph status --repo . | tee /tmp/graph-before.txt
```
`funcs  <N>   spec <C>% (<A>/<B> non-test)` 줄이 before다. 55% 근처여야 한다 — 아니면 그 값을 그대로 보고한다.

**대상 목록 DB 확인 (편집 전, 요구사항이 말한 "DB 근거"):**
```bash
sqlite3 .aidev/graph/graph.db "
SELECT (SELECT COUNT(*) FROM calls c WHERE c.callee_name = f.name) AS callers,
       f.path, f.qualname, f.lineno
FROM functions f
WHERE f.is_test = 0 AND f.has_spec = 0 AND f.lang = 'py'
  AND f.path LIKE 'aidev/%'
  AND f.name NOT LIKE '\_\_%' ESCAPE '\'
ORDER BY callers DESC, f.path, f.lineno;"
```
§2의 89개와 대조한다. 차이가 나면 (예: property가 DB에 `kind='method'` 로 잡히는 방식 때문에) **§2를 기준으로 삼되 차이를 결과에 한 줄로 적는다.** 목록이 늘어나면 호출 수 상위부터 채우고 120개를 넘기지 않는다.

**after (모든 편집 후):**
```bash
python -m aidev.cli graph build --repo .
python -m aidev.cli graph status --repo . | tee /tmp/graph-after.txt
```

`.aidev/graph/` 는 스스로를 `*` 로 무시하므로(`build.ensure_graph_dir`) 빌드가 워킹 트리를 더럽히지 않는다 — 커밋에도, `git status` 검사에도 안 잡힌다.

**보고:** RESULT 에 `graph: spec coverage 55% (373/678) → NN% (462/678), +89 functions` 형태 한 줄 + 파일별 개수.

---

## 6. 작업 순서 (implement=140턴)

pipeline.py 는 5492줄이다. **파일 전체를 읽지 말고** §2의 좌표 주변만 스팬으로 읽는다. 순서는 작은 파일 → 큰 파일로, 각 단계 끝에서 검증 B/C를 돌린다.

1. **before 측정** (§5) — 편집 전 반드시.
2. `aidev/specs.py` (1개) → `aidev/runner.py` (3개) → `aidev/cli.py` (5개) → `aidev/verify.py` (5개). 작고 독립적. 여기서 형식 감각을 잡는다. **검증 B + C 1회.**
3. `aidev/workspace.py` (11개). 스팬 3개: 54~130, 205~290, 335~360 / 548~580 / 704~712.
4. `aidev/storage.py` (24개). 스팬 3개: 131~200, 209~320, 563~700.
5. `aidev/pipeline.py` (40개). 스팬 8개: 700~720, 865~925, 980~1050, 1140~1180, 1335~1430, 2028~2070, 2420~2430 / 2653~2660, 3120~3135 / 3450~3480, 3914~3920 / 4090~4100 / 4362~4370 / 5449~5460. **가장 큰 덩어리 — 턴 예산의 절반을 여기 남겨 둔다.**
6. **검증 A + B + C + D 전량.**
7. **after 측정** (§5) 후 보고.

시간이 부족해지면: **pipeline.py 안에서 호출 수 상위부터** 남긴다 (`say`, `now_iso`, `write_state`, `stage_entry`, `set_status`, `read_state`, `fail_slice`, `slices_root`). property 접근자 15개는 가장 마지막이다 — 개수는 벌지만 정보 밀도가 가장 낮다. 부분 완료도 커버리지는 오르고 검증 A/B/C/D는 그대로 통과한다.

---

## 7. 위험 요소

| 위험 | 실제 여부 | 대응 |
|---|---|---|
| docstring 추가가 "코드 변경"으로 판정 | `__doc__` 가 None→str 로 바뀌는 것 외에 실행 의미 변화 없음 | §4 검증 B가 이를 정확히 증명한다. 결과 보고에 "docstring 형식 채택 + AST 동등성 증명" 을 명시 |
| verify 의 명세 검사가 스테이지를 실패시킴 | **실재한다.** docstring은 함수 본문 안에 들어가므로 그 함수가 "touched" 로 잡히고 `@param` 전수 검사를 받는다 | §4 검증 C 를 각 파일 끝날 때마다 실행. `@param` 누락 0 |
| `KIND_UNCHANGED` 위반 | 없음. 이 89개는 이전 명세가 빈 문자열이라 `""` ≠ 새 명세 | — |
| 명세를 안 붙인 함수까지 검사에 걸림 | 없음. `--unified=0` 은 실제 삽입 줄만 보고하고, 손대지 않은 함수는 소급 금지로 건너뛴다 | — |
| 줄 번호가 밀려 테스트가 깨짐 | 없음. `tests/test_graph.py:586` 의 `line_of()` 가 마커를 실행 시점에 찾는다. 하드코딩된 줄 번호 없음 | — |
| 실제 저장소를 인덱싱하는 테스트 | `test_graph.py:594-629` 가 `report.functions > 500`, `ts 함수 > 20` 을 본다 — 명세 추가는 함수 **개수**를 바꾸지 않는다 | — |
| `@property` 위 데코레이터 문제 | docstring을 쓰기로 했으므로 해당 없음 | — |
| 섹션 배너 주석(`# --- v0.5: ...`)이 명세로 흡수 | docstring 사용으로 해당 없음. 배너는 손대지 않는다 | — |
| `graph build` 가 워킹 트리를 더럽힘 | 없음. `.aidev/graph/.gitignore` 가 `*` | — |
| 설치본 `aidev` 를 테스트/빌드해 버림 | **실재한다** | `python -m pytest`, `python -m aidev.cli` 만 쓴다 |
| 89개 × pipeline.py 5492줄 = 턴 초과 | 가능 | §6의 스팬 읽기 + 우선순위 폴백 |
| DB 확인 결과가 §2와 다름 | 가능 (property/method 분류, `_` 접두 필터 등) | §5의 지침대로 §2 기준 유지 + 차이 한 줄 보고. 목록을 120개 넘게 늘리지 않는다 |

---

## 8. Done Criteria 대응

| 요구 | 충족 방법 |
|---|---|
| diff에 주석 외 변경 0 (기계 확인) | §4 검증 A(경로) + B(docstring 제거 AST 동등성). 두 명령의 출력을 RESULT에 붙인다 |
| pytest 전량 통과 | §4 검증 D: `python -m pytest -q` |
| 커버리지 상승 보고 | §5 before/after `graph status` 한 줄 + 파일별 개수 |
| README 언급 | 하지 않는다 |
