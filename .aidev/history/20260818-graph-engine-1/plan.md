# 계획 — 그래프 엔진 1단계: 파서 코어 + Function DB

## 0. 먼저 확인한 것 (현재 트리 실측)

| 사실 | 근거 |
| --- | --- |
| 이 패키지는 **의존성이 0개**다 | `pyproject.toml:15` `dependencies = []`, `requires-python = ">=3.10"` |
| sqlite3는 이미 집안 관행이다 | `aidev/storage.py:24,538` `Database` — 스키마 문자열 + `_migrate()` + `with self.conn` 트랜잭션 |
| 명세 주석 규약의 정의는 `aidev/specs.py`에 있다 | `:36` `SPEC_PARAM_RE`, `:174` `_comment_block_above`(docstring 없으면 `def` 위 `#` 블록), `:206` `functions`, `:85` `looks_like_test` |
| 서브커맨드 등록 방식 | `aidev/cli.py:54` `sub = parser.add_subparsers(...)`, `:96` `pipeline.add_parser(sub)`. 각 커맨드가 `set_defaults(func=...)`, `main()`(`:476`)이 `args.func(args)` 호출 |
| stage 커밋은 **한 곳**을 지난다 | `aidev/pipeline.py:2769` `commit_stage` — 호출부 3곳(`:3072` repair, `:3388` 일반 stage, `:4285` requirement 커밋)이 전부 여기로 모인다 |
| stage 커밋은 `git add -A`다 | `aidev/workspace.py:406` `commit_all` — **무시되지 않은 모든 파일**이 slice 브랜치에 실린다 |
| readonly stage 전에 worktree 청결 검사가 있다 | `aidev/pipeline.py:1343` `workspace_dirty_reason` → `repo_changes(cwd, include_slice_files=True)` → `.aidev/` 아래도 (LIVE_STATE_FILES 제외) **전부 위반으로 본다** |
| `git status --porcelain -uall` | `aidev/workspace.py:221` `porcelain` — `--ignored`가 없으므로 무시된 파일은 안 보인다 |
| `.gitignore`에 `.aidev/`는 없다 | `.gitignore` 전체 37줄, `.aidev` 언급 없음 |
| 파이프라인 플래그 관행 | `--no-spec-check`/`--no-verify-engine`/`--no-write-guard`(`aidev/pipeline.py:3812-3831`) → `_config`(`:4041-4044`)에서 `PipelineConfig`(`:1911-1917`) 불리언으로 |
| JS/TS 실물 | `desktop/src/**` 34개 `.ts/.tsx` — `export function f()`, `function f()`, `const X: Record<T,U> = {...}`, JSX(`<svg .../>`), 템플릿 리터럴 안 `${...}`, 콜백 화살표 함수. 예: `desktop/src/renderer/src/lib/graph-lookup.ts:10,25`, `.../SliceStatusBadge.tsx:21,29,42` |
| 테스트 관행 | fixture 파일 디렉터리 없음 — 소스를 문자열로 tmp_path에 쓴다(`tests/test_specs.py:29`). git repo fixture는 `tests/test_pipeline.py:60` `repo` |

### 이 slice가 스스로 지켜야 하는 규약

- `spec_check`가 켜져 있다(front matter에 `spec_check:` 없음). **새로 쓰는 모든 함수**는
  ① 기능 한 줄 ② 파라미터마다 `@param <이름>` ③ 분기/루프가 있으면 `@flow` 를 가져야 한다.
  아래에서 **기존 함수를 고치는 곳**(`specs.comment_block_above`, `commit_stage`, `_config`,
  `add_parser`, `build_parser`)도 같은 규칙을 받는다 — 특히 `_comment_block_above`는 지금
  `@param`이 없으므로(`aidev/specs.py:174-176`) 이름을 바꾸는 순간 `KIND_PARAM` 위반이 된다. 같이 채운다.
- `test_commands: python -m pytest -q`가 선언돼 있으므로 검증은 엔진이 돌린다. 손으로 확인할 때도
  **`python -m pytest`** 를 쓴다(worktree에서 맨 `pytest`는 설치본을 테스트한다).
- 임시 파일/실험 스크립트를 남기지 않는다. `.aidev/graph/`는 산출물이지 잔재가 아니다(아래 §6).

---

## 1. 기술 결정

### 1.1 파서: **의존성 추가 없음** (Python=stdlib `ast`, JS/TS=자체 스캐너)

tree-sitter를 쓰지 않는다. 근거:

1. 이 도구는 무인 루프에서 임의의 worktree를 돌아다니며 실행된다. `dependencies = []`(`pyproject.toml:15`)는
   설치 실패라는 고장 모드 자체가 없다는 뜻이고, `tree-sitter` + 언어 문법 3종(네이티브 휠)을 넣는 순간
   macOS/Windows/파이썬 마이너 버전마다 휠 유무가 파이프라인의 새 실패 지점이 된다.
2. 요구사항이 호출 관계를 **best effort / 미해석은 미해석으로 기록**이라고 이미 규정했다. 정확한 타입 해석이
   목표가 아니라 좌표 조회가 목표다. 이름 기반 해석에는 CST가 필요 없다.
3. Python 쪽은 `ast`로 **정확**하다(줄 범위·qualname·호출 노드 전부 정확). 남는 것은 JS/TS 하나뿐이고,
   Done Criteria가 요구하는 것은 "jsx 샘플에서 함수 추출 검증"이다.
4. JS/TS는 정규식만 쓰지 않는다 — **문자 스캐너로 문자열/템플릿/주석/정규식 리터럴을 공백으로 지운 사본**을
   만든 뒤, 그 사본 위에서 선언 정규식과 중괄호 매칭을 돌린다. JSX는 표현식 컨테이너 `{...}`가 균형이 맞으므로
   중괄호 매칭이 그대로 성립한다. 스캔이 깨지면 그 **파일 하나만** 실패 목록으로 간다.

한계는 문서에 명시한다: JS/TS는 이름 있는 선언(`function f`, `const f = () =>`, `class` 본문의 메서드)만
잡고, 객체 리터럴 메서드와 익명 콜백은 잡지 않는다.

### 1.2 저장: **SQLite 단일 파일** `.aidev/graph/graph.db`

json 대신 SQLite인 이유 — 두 가지 접근 패턴 모두에서 json이 진다:

- **조회**: 에이전트는 `show <이름>` 하나를 묻는다. SQLite는 `functions(name)` 인덱스 한 번, json은
  **전체 그래프를 파싱해서 메모리에 올린 뒤** 한 줄을 꺼낸다. 호출 관계는 `calls(callee_name)` 인덱스가
  `callers`를 그대로 답한다.
- **증분 갱신**: 파일 1개 변경이 SQLite에서는 `DELETE ... WHERE path=?` + `INSERT` 수십 행이다.
  json은 **매번 전체 문서를 다시 직렬화해서 다시 쓴다**. "변경 파일만 재파싱"이라는 요구를 만족해도
  쓰기 비용은 전체다.
- stdlib이고(`sqlite3`), 이 repo에 이미 같은 패턴이 있다(`aidev/storage.py:538`).

**마이그레이션 코드는 쓰지 않는다.** DB는 소모품 캐시라는 것이 요구사항의 원칙이므로,
`meta.schema != GRAPH_SCHEMA`면 파일을 지우고 전체 재빌드한다(한 줄 안내).

---

## 2. 새 패키지 `aidev/graph/`

`aidev/hooks/`(`aidev/hooks/__init__.py:1`)에 이미 서브패키지 선례가 있다. hatchling은
`packages = ["aidev"]`로 하위 패키지를 자동 포함하므로 `pyproject.toml` 변경은 없다.

```
aidev/graph/
├── __init__.py     공개 API 재수출 + add_parser 위임
├── model.py        dataclass + 명세 태그 파서 (언어 공통)
├── pyparse.py      Python: ast
├── jsparse.py      JS/TS/JSX/TSX: 문자 스캐너 + 중괄호 매칭
├── db.py           스키마 + GraphDB (쓰기/질의)
├── build.py        파일 탐색 · 해시 캐시 · build/update · 통계
└── commands.py     argparse 배선 + 출력 렌더링
```

`aidev.graph`는 `..specs`, `..workspace`, `..storage`만 import한다. **`pipeline`은 절대 import하지
않는다** — pipeline이 graph를 import하므로 순환이 된다. `.aidev` 상수는 `build.py`에 자체 정의하고
"pipeline에도 같은 상수가 있으나 import하면 순환"이라고 주석으로 남긴다.

### 2.1 `model.py` — 명세 태그 규약("닫힌 코어, 열린 주변")

```python
CORE_TAGS: Tuple[str, ...] = ("param", "flow", "why")   # 도구가 의미를 해석하는 것만

@dataclass
class SpecTag:      tag: str; value: str; core: bool; line: int   # tag는 '@' 없는 소문자
@dataclass
class ParsedCall:   name: str; raw: str; lineno: int
@dataclass
class ParsedFunction:
    qualname: str; name: str; lineno: int; end_lineno: int
    params: List[str]; signature: str; kind: str        # function|method|arrow|class-method
    spec: str; summary: str; tags: List[SpecTag]; calls: List[ParsedCall]
@dataclass
class ParsedFile:   path: str; lang: str; functions: List[ParsedFunction]; error: str = ""

def parse_spec(text: str) -> Tuple[str, List[SpecTag]]:
    """명세 주석 한 덩이를 요약 한 줄과 태그들로 가른다. 미지 태그도 그대로 담는다."""
```

- 요약 = `@`로 시작하지 않는 **첫 비어있지 않은 줄**.
- 태그 정규식 `^\s*@([A-Za-z_][\w-]*)\s*(.*)$` (MULTILINE). 값은 다음 `@`나 빈 줄 전까지 이어 붙인다.
- `core = tag.lower() in CORE_TAGS`. **미지 태그는 버리지 않고 `core=False`로 그대로 저장**한다 —
  `pipeline.unknown_front_matter_keys`(`aidev/pipeline.py:549`)가 front matter 미지 키에 대해 하는 것과
  같은 원칙이며, 검사 규칙은 붙이지 않는다(요구사항 "태그는 데이터, 검사는 블록").

### 2.2 `pyparse.py`

```python
def parse_python(text: str, path: str) -> ParsedFile
```

- `ast.parse` → `SyntaxError`/`ValueError`면 `ParsedFile(error="SyntaxError: ... (line N)")`로 돌려주고
  **예외를 밖으로 내보내지 않는다**.
- 방문: `FunctionDef`/`AsyncFunctionDef`를 전부 담는다 — `specs.functions`(`:206`)와 달리 **던더도 중첩
  함수도 뺀다** (`__init__`의 좌표는 조회 대상이고, DB는 검사기가 아니다). qualname은 `specs`와 같은
  점 표기(`SliceRecord.ensure`, `outer.inner`), 클래스 안이면 `kind="method"`.
- 명세: `ast.get_docstring(node) or specs.comment_block_above(lines, node.lineno)` → `parse_spec`.
- 시그니처: `posonly+args+vararg+kwonly+kwarg`를 순서대로 렌더링. 애너테이션/기본값은 `ast.unparse`
  (3.10 보장, `pyproject.toml:13`)로, 실패하면 이름만. `params`는 `self`/`cls` 제외,
  `*args`/`**kwargs`는 접두사 유지 — `specs._params`(`:189`)와 같은 규칙.
- 호출: 각 함수의 **직속 본문**(중첩 함수 본문은 그 중첩 함수 소유)에서 `ast.Call`을 모은다.
  `Name`→`id`, `Attribute`→`attr`(raw는 점 표기 전체), 그 외는 건너뛴다.

### 2.3 `jsparse.py`

```python
def blank_strings(text: str) -> str   # 테스트가 직접 부를 수 있게 공개
def parse_js(text: str, path: str) -> ParsedFile
```

1. `blank_strings`: 한 글자씩 훑으며 `'`/`"`/`` ` ``(+`${}` 깊이)/`//`/`/* */`/정규식 리터럴 안의 내용을
   공백으로 바꾼다(줄바꿈은 보존해 줄 번호가 어긋나지 않게). 정규식 시작 판정은 직전 비공백 문자가
   `(,=:[!&|?{};+-*%~^<>` 또는 줄머리일 때만 — 나눗셈과의 구분은 휴리스틱이고, 틀리면 중괄호 균형이
   깨져 **그 파일만** 실패 목록으로 간다.
2. 선언 인식(정리된 사본 위, 줄 단위):
   - `(export )?(default )?(async )?function <이름>(...)`
   - `(export )?(const|let|var) <이름> ... = (async )?(function|\(...\)\s*=>|<식별자>\s*=>)`
   - `class <이름>` 블록 안 깊이 1의 `(static |async |get |set )?<이름>(...)  {` → `kind="method"`,
     qualname `Class.method`
3. 끝 줄: 헤더가 `{`로 닫히면 정리된 사본에서 중괄호 매칭, 아니면(식 본문 화살표) 같은 줄 또는 `;`까지.
4. 명세: 선언 바로 위 `/** ... */`(줄머리 `*` 제거) 또는 `//` 연속 블록 → `parse_spec`.
   → JSDoc `@param`이 그대로 코어 태그로 들어온다.
5. 호출: 범위 안 정리된 사본에서 `([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(` 를 훑고
   키워드(`if for while switch catch return typeof function class await new import require ...`)를 제외.
   점 표기는 마지막 조각이 `name`, 전체가 `raw`.
6. 안전핀: 파일 1.5MB 초과, 또는 스캐너 반복이 길이의 4배를 넘으면 `error="skipped: too large/unparsable"`.

### 2.4 `db.py` — 스키마

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE files (
    path TEXT PRIMARY KEY, lang TEXT, hash TEXT, size INTEGER, mtime REAL,
    parsed INTEGER, error TEXT, funcs INTEGER, scanned_at TEXT);

CREATE TABLE functions (
    id INTEGER PRIMARY KEY, qualname TEXT, name TEXT, path TEXT, lang TEXT,
    lineno INTEGER, end_lineno INTEGER, params TEXT, signature TEXT, kind TEXT,
    summary TEXT, spec TEXT, has_spec INTEGER, is_test INTEGER);

CREATE TABLE calls (
    id INTEGER PRIMARY KEY, caller_id INTEGER, callee_name TEXT,
    callee_raw TEXT, lineno INTEGER, resolved_id INTEGER);

CREATE TABLE tags (
    func_id INTEGER, tag TEXT, value TEXT, core INTEGER, lineno INTEGER);

CREATE INDEX idx_func_name ON functions(name);
CREATE INDEX idx_func_qual ON functions(qualname);
CREATE INDEX idx_func_path ON functions(path);
CREATE INDEX idx_calls_callee ON calls(callee_name);
CREATE INDEX idx_calls_caller ON calls(caller_id);
CREATE INDEX idx_tags_func ON tags(func_id);
```

- `path`는 항상 **repo 상대 + `/` 구분자**(`PurePath.as_posix`), Windows 대비.
- `is_test = specs.looks_like_test(path)` — 테스트 파일도 색인한다(테스트가 부르는 곳을 `callers`가
  답해야 하므로). 다만 **커버리지 %에서는 제외**하고 `status`가 둘 다 보고한다.
- `GraphDB` 메서드: `replace_file(ParsedFile, hash, size, mtime)`(그 파일 행 삭제 후 삽입),
  `drop_file(path)`, `file_rows()`, `resolve_calls()`, `stats()`,
  질의: `find(name)`, `callers(name)`, `calls_of(func_id)`, `summaries(prefix)`, `tags_of(func_id)`.
- `sqlite3.connect(path, timeout=5.0)`; 쓰기는 `with self.conn` 트랜잭션(=`storage.Database.save_run` 관행).

**호출 해석 규칙**(`resolve_calls`, 매 build/update 마지막에 **전체 재실행**):
같은 파일 안에 그 이름의 함수가 정확히 하나면 그것으로, 아니면 repo 전체에서 정확히 하나일 때만
`resolved_id`를 채운다. 그 외(0개=외부, 2개 이상=동명 모호)는 **NULL로 남겨 미해석을 미해석으로 기록**한다.
전체 재실행이 필요한 이유: 파일 재파싱은 행을 지우고 다시 넣으므로 다른 파일에서 그 함수를 가리키던
`resolved_id`가 낡아진다. 인덱스 덕에 수천 행 갱신은 밀리초 단위다.

### 2.5 `build.py`

```python
GRAPH_SCHEMA = 1
SUFFIX_LANG = {".py": "py", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
               ".ts": "ts", ".tsx": "ts"}

def graph_dir(repo: Path, override: Optional[Path] = None) -> Path   # <repo>/.aidev/graph
def db_path(repo, override=None) -> Path                             # .../graph.db
def discover(repo: Path) -> List[Path]
def build_repo(repo, graph_dir=None) -> UpdateReport                 # 항상 처음부터
def update_repo(repo, graph_dir=None) -> UpdateReport                # 변경분만
@dataclass class UpdateReport:
    full: bool; scanned: int; unchanged: int; reparsed: List[str]
    added: List[str]; removed: List[str]; failed: List[str]; functions: int
    def one_line(self) -> str
```

- `discover`: git이 있으면 `workspace.list_files(repo)`(신규, §5.3) = `git ls-files -z --cached --others
  --exclude-standard` → **`.gitignore`를 공짜로 존중하고 node_modules를 자동 배제**한다.
  git이 없으면 `os.walk` + 배제 목록(`.git .aidev node_modules .venv venv dist build out
  __pycache__ .pytest_cache .vite dist-electron *.egg-info data`). 두 경우 모두 `SUFFIX_LANG` 필터 +
  실제 존재 확인(삭제된 추적 파일 제외).
- 캐시 규약: `files.mtime`/`size`가 그대로면 해시 계산도 건너뛴다(빠른 길). 다르면 바이트를 읽어
  `sha256`을 구하고, **해시가 같으면 mtime만 갱신하고 재파싱하지 않는다.** 다르면 재파싱.
  DB에 있는데 디스크에 없는 경로는 행 삭제(`removed`).
- `meta`: `schema`, `repo`, `built_at`, `updated_at`, `base_commit`(=`workspace.head_commit(repo)`),
  `branch`(=`workspace.current_branch`), `dirty`(=`porcelain`이 비어있지 않은지), `aidev_version`.
- `update_repo`가 DB 없음/스키마 불일치를 만나면 조용히 `build_repo`로 승격(`full=True`).
- 첫 줄에 `.aidev/graph/.gitignore`(내용 `*`)를 항상 써 둔다 — §6.

### 2.6 `commands.py` — 출력(토큰 효율 우선)

`show` (동명이 여럿이면 블록을 반복, 경로로 구분):
```
run_pipeline(cfg, rec, state, requirement, amend=None)  aidev/pipeline.py:3276-3418  py
  The slice loop itself: every stage in order, with a gate after any of them.
  @param cfg  the slice's configuration
  @flow  stage -> run -> commit -> gate
  @custom  이 태그는 도구가 해석하지 않는다
  calls 14: commit_stage aidev/pipeline.py:2769 | run_stage aidev/pipeline.py:2282 | +12
  callers 2: cmd_pipeline aidev/pipeline.py:3877 | resume_slice aidev/pipeline.py:4306
```
태그는 **소스 순서 그대로, 원문 그대로** 찍는다(미지 태그도 동일하게 — 요구사항 §1). 목록은 12개에서
`+N`으로 자르고 `graph callers/calls`를 안내한다.

`callers <이름>` — 한 줄에 하나:
```
merge_slice  aidev/pipeline.py:4700  (1 definition)
  aidev/pipeline.py:3951  _dispatch
  tests/test_pipeline.py:812  test_merge_puts_the_work_on_the_base
```
`calls <이름>`:
```
run_pipeline  aidev/pipeline.py:3276
  3305  banner            aidev/pipeline.py:3436
  3312  json.dumps        (unresolved)
  3350  main              (ambiguous: 4)
```
`summaries [--dir <경로>]`:
```
27 functions in aidev/graph/
aidev/graph/build.py:41  discover  탐색 대상 파일들 - git이 알면 git에게 묻는다
...
```
`status`:
```
graph  /path/.aidev/graph/graph.db   (schema 1, built 2026-08-18T21:03:11+09:00)
base   0bfabda  slice/20260818-graph-engine-1  (worktree dirty)
files  151 parsed, 2 failed, 1 skipped   (py 96, ts/js 57)
funcs  1042   spec 78% (612/781 non-test)   tags @param 1832 @flow 402 @why 31
unknown tags  @custom 3, @origin 1
calls  3811 edges, 2260 resolved, 1551 unresolved (of which 604 ambiguous)
failed desktop/src/renderer/src/x.tsx:88  unbalanced braces
       tests/fixtures/broken.py:12  SyntaxError: invalid syntax
```
공통 규칙: 좌표는 항상 `path:line`, 이름 없음은 종료코드 1, DB 없음/사용법 오류는 2(안내 한 줄:
`no graph yet — aidev graph build --repo <r>`), `sqlite3.OperationalError`(잠김)는 한 줄 경고 + 2.

---

## 3. CLI 배선

### 3.1 `aidev/graph/commands.py: add_parser(sub)`

`pipeline.add_parser`(`aidev/pipeline.py:3663`)와 같은 모양. `graph` 파서에 중첩 서브파서를 만들고
각 동사가 `set_defaults(func=...)`. 동사 없이 `aidev graph`만 치면 graph 도움말을 찍고 2를 돌려주도록
`cmd.set_defaults(func=_usage)`를 **서브파서 추가 전에** 걸어 둔다(`cli.main:476`이 top-level 도움말을
찍는 것보다 낫다).

공통 옵션(모든 동사): `--repo PATH`(기본 cwd, `pipeline._dispatch:3892`와 같은 해석),
`--graph-dir DIR`(기본 `<repo>/.aidev/graph`; **테스트와 실험용 — 환경변수는 두지 않는다**,
전역 환경변수는 repo마다 DB가 섞이는 함정이 된다).

| 동사 | 인자 |
| --- | --- |
| `build` | — |
| `update` | — |
| `status` | — |
| `show` | `name` (필수) |
| `callers` | `name` |
| `calls` | `name` |
| `summaries` | `--dir PATH` (repo 상대/절대/파일 하나 모두 허용), `--limit N` (0=전부, 기본 0) |

이름 조회 순서: `qualname` 정확일치 → `name` 정확일치 → `qualname LIKE '%.'||name`. 전부 나열하고
경로로 구분한다(요구사항 §3).

### 3.2 `aidev/cli.py`

- `:20` import에 `graph` 추가: `from . import __version__, graph, pipeline, reporter, runner, verify, workspace`
- `:96` `pipeline.add_parser(sub)` 바로 아래에 `graph.add_parser(sub)`
- `:1-8` 모듈 docstring 사용례에 `aidev graph build --repo ~/pluto` / `aidev graph show run_pipeline` 두 줄 추가
- `build_parser`의 docstring은 이미 있고 인자가 없으므로(`:42`) 손대면 안 되는 곳은 없다 — 본문만 바뀌면
  "본문 바뀌었는데 명세 그대로"(`KIND_UNCHANGED`) 위반이 되므로 **한 줄을 갱신**한다.

---

## 4. 파이프라인 훅 (연결만)

### 4.1 `aidev/pipeline.py`

1. `:53` import에 `graph` 추가(순환 없음 — graph는 pipeline을 import하지 않는다).
2. `PipelineConfig`(`:1917` `verify_timeout` 옆)에 `graph_hook: bool = True` + 주석 한 줄.
3. `add_parser`(`:3827` `--no-spec-check` 옆)에
   `--no-graph` (`action="store_true"`, help: "do not refresh the function graph after a stage commit").
4. `_config`(`:4047` 옆)에 `graph_hook=not getattr(args, "no_graph", False),`.
5. 신규 함수(`commit_stage` 바로 아래):

```python
def refresh_graph(cfg: PipelineConfig) -> None:
    """Stage 커밋 뒤 Function DB를 증분 갱신한다. 캐시는 slice를 죽이지 않는다.

    @param cfg  the slice's configuration - --no-graph turns this off
    @flow  off -> return ; update -> one line ; any failure -> one warning line
    주요 내부 변수: report(무엇이 다시 파싱됐는지)
    """
    if not cfg.graph_hook:
        return
    try:
        report = graph.update_repo(cfg.cwd)
    except Exception as exc:   # 파생 캐시가 파이프라인을 멈추게 두지 않는다 - 요구사항 §4
        say("warning: graph update skipped ({0})".format(exc))
        return
    say(report.one_line())
```

6. `commit_stage`(`:2803-2808`)의 `if sha:` 블록 끝, `return None` 직전에 `refresh_graph(cfg)` 호출.
   호출부 3곳(requirement/stage/repair)이 전부 여기를 지나므로 **한 곳만 고친다**. `commit_stage`의
   docstring에 "커밋 뒤 그래프 증분 갱신을 시킨다(실패해도 커밋은 유효)" 한 줄을 더한다(본문이 바뀌므로
   명세도 바뀌어야 한다 — `KIND_UNCHANGED` 회피).

`launch_slice:4285`의 requirement 커밋에서도 훅이 돈다. DB가 비어 있으므로 그때가 **최초 전체 빌드**이고,
plan 단계가 시작되기 전에 그래프가 존재하게 된다 — 2단계(브리핑 주입)가 바로 얹힐 자리다.
그래프는 worktree 안에 생기고 slice와 함께 사라진다. 사용자 체크아웃의 그래프는 `aidev graph build`로
사람이 만든다.

---

## 5. 기존 파일 수정 (최소)

### 5.1 `aidev/specs.py` — `_comment_block_above` → `comment_block_above` (공개)

`:174` 이름을 공개형으로 바꾸고 `:228`의 유일한 호출부를 따라 고친다. docstring은 규약대로 채운다:

```python
def comment_block_above(lines: Sequence[str], lineno: int) -> str:
    """The run of ``#`` comments directly above a ``def``, oldest line first.

    Public since the graph parser reads the same convention: docstring first,
    then the comment block. Two copies of this would drift.

    @param lines   the file's lines, 0-based
    @param lineno  the 1-based line the ``def`` is on
    """
```
`pipeline._looks_like_test = specs.looks_like_test`(`aidev/pipeline.py:670`)와 같은 "한 번만 정의" 관행이다.
`specs._params`는 **건드리지 않는다** — 그래프는 애너테이션/기본값까지 필요해서 자체 렌더러를 쓴다.

### 5.2 `.gitignore`

```
# 파생 캐시: Function DB는 코드에서 언제든 다시 만들 수 있다
.aidev/graph/
```
이것만으로는 대상 repo(조커 등)를 지키지 못한다. 그래서 §2.5대로 **`.aidev/graph/.gitignore`(내용 `*`)를
빌드할 때마다 써 둔다.** 이 자기무시 파일이 진짜 방어선이다: `commit_all`의 `git add -A`(`workspace.py:422`)가
DB를 slice 브랜치에 싣는 것도, `workspace_dirty_reason`(`pipeline.py:1343`)이 readonly stage 직전에
그래프 파일을 위반으로 보는 것도 이걸로 함께 막힌다.

### 5.3 `aidev/workspace.py` — `list_files` 신규 (`:234` `porcelain` 아래)

```python
def list_files(repo: Path) -> Optional[List[str]]:
    """Tracked and not-ignored-untracked paths, '/' separated. None when git cannot answer.

    ``-z`` because a path with a space or a quote would otherwise come back quoted.

    @param repo  the repository or worktree to ask
    """
    # git ls-files -z --cached --others --exclude-standard
```
graph 쪽에서 `.gitignore`를 다시 구현하지 않기 위한 한 줄짜리 위임이다.

### 5.4 `README.md`

- `## 다른 터미널에서 관찰 (v0.1.2)`(`:1031`) 앞에 새 절 **`## Function Graph (v0.6)`**:
  설계 원칙 한 줄("사람에겐 시선, AI에겐 DB"), 6개 명령 사용례, 태그 규약(코어 4종 + 미지 태그 보존),
  캐시 규약(코드가 진실 / 언제든 버려도 됨 / 해시 기반), **파서 한계**(JS/TS는 이름 있는 선언만,
  호출은 이름 기반 best effort, 미해석·모호는 그대로 표시), tree-sitter를 쓰지 않은 이유, 파이프라인 훅과
  `--no-graph`.
- `## 저장 구조`(`:1276`)의 설명 문단에 `.aidev/graph/graph.db`(파생 캐시, git 무시) 한 단락 추가.
- `## 테스트`(`:1307`)에 `tests/test_graph.py`가 tmp repo와 **이 repo 자신**을 대상으로 돈다는 한 줄.
- `## 로드맵`(`:1374`) `v0.6  Codebase Memory ← 지금 여기` 옆에 "(1단계: 파서 + Function DB — 완료)".

---

## 6. 테스트 — `tests/test_graph.py` (신규)

fixture 디렉터리를 만들지 않는다(집안 관행: 소스는 문자열로 tmp_path에 쓴다).
`tests/test_pipeline.py`의 `repo`/`git`/`claude_bin`/`argv` fixture를 이름으로 import한다
(`tests/test_specs.py:17`과 같은 방식).

**파서**
1. Python: 함수/메서드/중첩/async/던더의 qualname·줄 범위·시그니처·params 정확성.
2. 명세: docstring 요약 + `@param`/`@flow`/`@why` 코어 태그, docstring이 없을 때 `#` 블록 fallback.
3. **미지 태그 fixture**: `@custom 검사기는 이걸 해석하지 않는다` + `@origin ...` → DB에 `core=0`으로
   저장되고 `show` 출력에 **원문 그대로** 나온다. (Done Criteria)
4. 파싱 실패: 구문 오류 `.py` 1개 + 중괄호가 깨진 `.ts` 1개 → 빌드는 완주하고 `status`의 실패 목록에
   그 둘만 있으며, 나머지 파일의 함수는 정상 색인.
5. **JS/TS**: `.jsx` 샘플(`export function App()`, `const Row = ({x}) => (...)`, `class Store {` 안 메서드,
   JSDoc `/** ... @param ... @custom ... */`, JSX 안 템플릿 리터럴 `${}`) → 함수 4종 추출 + 줄 범위 +
   호출 간선. `.tsx`/`.ts`도 같은 fixture로 확장자만 바꿔 1건. (Done Criteria)

**DB / 캐시**
6. `build` → `status`가 파일 수/함수 수/커버리지 %/실패 목록/미지 태그 통계를 전부 낸다.
7. **증분**: build → 파일 A의 함수 하나를 고치고 update → `report.reparsed == ["A"]`,
   파일 B의 `functions` 행이 `id`까지 그대로, A의 새 요약이 DB에 반영, 사라진 함수는 사라짐. (Done Criteria)
8. 내용은 그대로 두고 mtime만 바꾼 파일 → `unchanged`(재파싱 0).
9. 파일 삭제 → update가 그 파일 행과 함수 행을 지운다.
10. 스키마 불일치(meta.schema를 손으로 99로 바꿈) → update가 전체 재빌드로 승격.

**조회 동사** (전부 `aidev.cli.main([...])` + capsys)
11. `show`/`callers`/`calls`/`summaries`가 좌표 형식(`path:line`)으로 답한다.
12. 동명 함수 2개(다른 경로) → `show`가 **둘 다** 경로로 구분해 나열.
13. `summaries --dir <하위경로>`가 범위를 좁힌다. 없는 이름은 종료코드 1, DB 없이 조회는 2.

**파이프라인 훅**
14. `--requirement`로 slice를 돌린 뒤(기존 pipeline 테스트 배선 재사용):
    worktree에 `.aidev/graph/graph.db`가 있고, **`git ls-files`에는 `.aidev/graph`가 없고**,
    stage 커밋 후 `git status --porcelain -uall`이 깨끗하다(= readonly stage 검사가 살아 있다).
15. `graph.update_repo`를 예외를 던지도록 monkeypatch → 파이프라인은 그대로 성공하고 출력에
    `warning: graph update skipped`가 한 줄 있다.
16. `--no-graph`면 DB가 만들어지지 않는다.

**자기 자신 (Done Criteria)**
17. `repo_root = Path(__file__).resolve().parent.parent`(= 이 worktree; `aidev/pipeline.py`가 없으면 skip)를
    `--graph-dir tmp_path`로 빌드 →
    - `show run_pipeline`이 `aidev/pipeline.py:3276`을 답한다(줄 번호는 하드코딩하지 않고
      **실제 파일에서 `def run_pipeline(` 줄을 찾아 비교**한다 — 구현이 줄을 밀어도 회귀가 아니다),
    - `callers merge_slice`가 `aidev/pipeline.py`의 호출 지점을 포함,
    - `desktop/src/**`의 `.ts/.tsx` 함수가 0보다 많다(JS 파서가 실제 코드에서 동작),
    - 파싱 실패 파일 수가 전체의 5% 미만.

---

## 7. 검증 절차 (구현 단계가 실제로 칠 것)

```bash
python -m pytest -q                                   # 전량 (기존 + 신규)
python -m aidev.specs --base <requirement 커밋>        # 명세 규약 자체 점검
python -m aidev.cli graph build --repo .              # Pluto 자신 전체 빌드 완주
python -m aidev.cli graph status
python -m aidev.cli graph show run_pipeline
python -m aidev.cli graph callers merge_slice
python -m aidev.cli graph calls commit_stage
python -m aidev.cli graph summaries --dir aidev/graph
git status --porcelain -uall                          # .aidev/graph가 안 보여야 한다
```
마지막 확인: `.aidev/graph/`는 **커밋되지 않는다**(자기무시 + `.gitignore`). 로컬에 남은 DB는 잔재가
아니라 산출물이지만, 검증 뒤 지워도 아무 것도 잃지 않는다는 것이 이 설계의 요점이다.

---

## 8. 위험과 대응

| 위험 | 대응 |
| --- | --- |
| **DB가 slice 브랜치에 실려 merge 때 본진 오염** (`commit_all`의 `git add -A`) | 빌드마다 `.aidev/graph/.gitignore`(`*`) 생성 + repo `.gitignore` 추가. 테스트 14가 `git ls-files`로 증명 |
| **readonly stage 직전 청결 검사 실패**로 slice가 통째로 죽음 (`workspace_dirty_reason`) | 같은 자기무시 파일로 `git status -uall`에서 사라진다. 기존 pipeline 테스트 전량이 회귀망 역할을 한다 |
| 훅 예외가 파이프라인을 죽임 | `refresh_graph`의 광범위 `except Exception` — 의도적이며 주석으로 근거를 남긴다. 테스트 15 |
| 첫 stage 커밋(requirement)에서 대형 repo 전체 빌드로 지연 | `git ls-files`가 node_modules/무시 파일을 애초에 빼고, 대형 파일은 1.5MB에서 자른다. 그래도 느리면 `--no-graph` |
| JS 정규식 리터럴 오판 → 중괄호 불균형 | 파일 단위 실패로 격리하고 `status` 실패 목록에 사유를 남긴다. 전체를 죽이지 않는다(요구사항 §1) |
| 동명 함수 폭발(`main`, `run`)로 `calls`가 쓰레기 | 같은 파일 우선 → repo 전체 유일할 때만 해석. 나머지는 `(ambiguous: N)`으로 **모른다고 말한다** |
| 두 프로세스 동시 접근(훅 + 사람) | `timeout=5.0`, 짧은 트랜잭션, 조회 쪽 `OperationalError`는 경고 한 줄 |
| Windows 경로 구분자 | 저장 시 `as_posix()` 정규화, `--dir` 입력도 같은 함수로 정규화 |
| 명세 검사에 이 slice가 걸림 | 새 함수 전부 규약대로 작성 + `comment_block_above`/`commit_stage`/`_config`/`add_parser`/`build_parser`의 명세를 함께 갱신 |
| 자기 빌드 테스트가 site-packages를 훑음 | repo 위치를 `tests/__file__` 기준으로 잡고, `aidev/pipeline.py`가 없으면 skip |

## 9. 하지 않는 것 (요구사항 그대로)

브리핑 생성기·plan 프롬프트 주입, 그래프 UI/Graph Notes/codebase-map 스킬, 커스텀 태그 검사 규칙,
도메인 추론·`@origin` 자동·ERD·고급 흐름 쿼리, 조커 repo 대상 실행. 훅은 **연결만** 하고 소비는 2단계다.
