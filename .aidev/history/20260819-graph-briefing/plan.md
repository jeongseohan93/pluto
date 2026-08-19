# 그래프 엔진 2단계 — 브리핑 생성기 구현 계획

## 0. 무엇을 만드는가 (한 문장)

세션 발사 직전에 엔진이 Function DB를 선조회해 **LOD 3단 브리핑 md**를 차리고,
slice 디렉터리에 보존한 뒤 plan/implement 프롬프트에 동봉한다. `briefing: off`면
프롬프트는 **바이트 단위로 지금과 같다.**

읽은 실제 코드에서 확정한 사실 (추측 아님):

- `run_stage`(`aidev/pipeline.py:2414`)는 plan / implement / test / diagnose /
  decompose가 **모두 지나는 단 하나의 발사구**다. repair(=verify 실패 후 implement
  재실행, `run_verify` line 3234)와 amend(=implement/test 재개, `run_pipeline`
  line 3545)도 여기를 지난다. 따라서 훅은 여기 하나면 된다.
- `prompt = spec.prompt or build_prompt(...)` (line 2466). decompose와 diagnose는
  `spec.prompt`가 고정이라 이 분기를 타지 않는다 → **자동으로 브리핑 대상 제외**.
- `graph.update_repo`(`aidev/graph/build.py:181`)는 DB가 없으면 전체 빌드, 있으면
  증분이다. `refresh_if_dirty`(line 344)는 marker가 없으면 `None`. 즉 신선도 처리는
  이미 있는 두 함수의 조합이면 끝난다.
- `.aidev/graph/`는 `ensure_graph_dir`가 `*` 담긴 `.gitignore`를 먼저 쓴다
  (`build.py:66`). 그래서 브리핑이 worktree 안에서 그래프를 빌드해도
  `git status --porcelain`에 안 보이고 — **plan readonly 증명(`finish_stage`
  line 3393의 before/after 비교)을 깨지 않는다.** 이건 이번 슬라이스의 가장 큰
  단일 리스크이고, 아래 §7에 전용 테스트를 둔다.
- `events.estimate_tokens`(`aidev/events.py:296`, `CHARS_PER_TOKEN=4`)가 이 프로젝트의
  유일한 토큰 추정기다. 브리핑 토큰 수는 이걸 쓴다 (새 추정 방식 금지).
- `telemetry.to_dict()["observed"]["calls"]`는 호출마다 `{"tool","target",...}`를
  담는다(`telemetry.py:74`, Bash의 target은 명령 문자열). graph 조회 동사 횟수는
  여기서 셀 수 있다 — §6.

## 1. 새 파일: `aidev/briefing.py`

**왜 graph 패키지 안이 아니라 top-level인가.** `aidev/graph/`의 docstring이 스스로를
"the engine's data layer"라고 못 박고 "never imports pipeline"이라고 쓴다. 브리핑은
데이터층의 *소비자*(단계·requirement·plan을 안다)이므로 층이 하나 위다. 이 모듈은
`graph`와 `events`만 import하고 `pipeline`은 절대 import하지 않는다(순환 금지).
`pipeline`이 이 모듈을 import한다.

### 1.1 공개 API

```python
@dataclass
class Briefing:
    text: str            # 프롬프트에 동봉될 md 전문
    tokens: int          # events.estimate_tokens(utf-8 bytes)
    sections: List[str]  # ["repo_map", "related", "scope", "reuse"] 중 실제로 존재한 것
    scope: List[str]     # plan이 지목한 경로/이름 (사이드카에 기록, 사람이 읽는 용)
    key: str             # 캐시 키
    reused: bool = False # 캐시 재사용이면 True (재생성 스킵)
    graph: Dict[str, Any] = field(default_factory=dict)  # functions/files/coverage/commit
```

```python
def build(repo, stage, requirement, plan="", *, graph_directory=None,
          limits=None) -> Briefing
```
`repo`는 **cfg.cwd(worktree)**다. 여기서 파일 본문 발췌도 읽는다.

```python
def ensure_graph(repo, graph_directory=None) -> Optional[UpdateReport]
```
DB가 없으면 `graph.update_repo`(=전체 빌드), dirty marker가 있으면 같은 함수로 증분,
둘 다 아니면 `None`. 요구사항 §3의 "stale이면 증분 갱신 먼저"가 이 함수 하나다.

```python
def cache_key(stage, commit, requirement, plan) -> str
def read_cached(path_md, path_json, key) -> Optional[Briefing]
def write_cache(path_md, path_json, brief) -> None
```

### 1.2 LOD 3단 — 렌더링 순서와 내용

순서 자체가 요구사항 §3의 "불변부 앞, 가변부 뒤"다. 1번은 커밋이 안 바뀌면 그대로고,
3번은 plan마다 바뀐다.

```
# BRIEFING  <stage>
graph: 1189 functions, 42 files, spec 68%   (base 48e8872, 2026-08-19T14:02:11+09:00)

## 1. REPO MAP   (전역 — 저해상)
aidev/                 12 files   734 functions
aidev/graph/            6 files   181 functions
tests/                 16 files   274 functions
entry points (most called):
  say                    aidev/pipeline.py:3615    142 callers
  write_text_atomic      aidev/pipeline.py:1161     31 callers
  ...

## 2. RELATED   (관련부 — 중해상)
matched on: briefing, graph, prompt, stage, cache
  build_prompt           aidev/pipeline.py:1871   The prompt one stage is given, ...
  refresh_if_dirty       aidev/graph/build.py:344 Update the graph when it was marked stale, ...
  +18 more (aidev graph summaries --dir aidev)

## 3. SCOPE   (plan이 지목한 것 — 고해상)      ← implement 이후 단계에만
build_prompt(stage, requirement, ...)  aidev/pipeline.py:1871-1931  py
  The prompt one stage is given, built from what the engine already knows.
  @param stage  which stage is about to run
  @flow  plan (+replan) -> implement/test template -> ...
  calls 6: _replan_note aidev/pipeline.py:1966 | ...
  callers 1: run_stage aidev/pipeline.py:2466
  1871 | def build_prompt(
  1872 |     stage: str,
  ...   (발췌 상한까지)

## 4. ALREADY EXISTS   (재사용 후보)           ← implement 단계에만
  render_summary         aidev/pipeline.py:3660   Per-stage tokens / cost / changed files
  ...
기존 함수 재사용 우선. 유사 기능 신설 시 사유 명시. 통합 리팩토링은 금지.

---
이외는 aidev graph show/callers/calls/summaries로 요청하라.
파일 통읽기 전에 조회 우선.
```

- **저해상**: `db.directory_counts()`(디렉터리별 파일·함수 수) + `db.entry_points(N)`
  (resolved 피호출 edge가 많은 순, non-test). 상한 `REPO_MAP_TOKENS = 900`.
- **중해상**: requirement(+ implement면 plan) 텍스트에서 식별자를 뽑아 토큰화 →
  `db.search_tokens(...)`로 name/qualname/summary 매칭. **임베딩 없음, 순수 문자열.**
  토큰화 규칙: `[A-Za-z_][A-Za-z0-9_]{2,}` 추출 → snake_case/camelCase 분해 →
  소문자화 → `_STOPWORDS`(the, and, for, add, new, use, ... + `aidev`, `test`) 제거.
  랭킹: 이름 완전일치 > 이름 토큰 겹침 수 > summary 부분일치. 상한
  `RELATED_TOKEN_CAP = 1200`, 최대 `RELATED_MAX = 30`행. 잘리면 `+N more (aidev graph
  summaries --dir <가장 흔한 디렉터리>)`.
- **고해상**: `stage != "plan"`일 때만. plan.md에서
  - 경로: `_PLAN_PATH_RE`와 같은 모양의 정규식(이 모듈 안에 자체 정의 — `pipeline.
    plan_scale`을 import하면 순환이다. 서로 답하는 질문이 다르므로 중복이 아니라
    별개다. 한 줄 주석으로 명시).
  - 함수 이름: 백틱 안 식별자 / `name()` 꼴 / 경로 뒤 `:name`.
  각 대상에 대해 `db.by_path(paths)` + `db.find(name)` → 함수당 **명세 레코드 전문**
  (`db.tags_of`) + `db.calls_of` / `db.callers` 한 줄씩 + **본문 발췌**
  (`repo/path`를 읽어 `lineno..end_lineno` 슬라이스, 함수당 `EXCERPT_LINES = 40`행
  상한, 줄번호 접두). 상한 `SCOPE_TOKEN_CAP = 2500`. 그래프에 없는 경로는
  `(not indexed yet — this file does not exist on this branch)` 한 줄로 남긴다
  (신설 파일이 정상 케이스이므로 침묵하면 안 된다).
- **재사용 후보(§2)**: `stage == "implement"`에만. plan이 이름 지은 함수들의 **이름
  토큰**과 겹치는 기존 non-test 함수를, scope에 이미 나온 것은 빼고, 겹침 수
  내림차순으로 `REUSE_MAX = 20`행. 과최적화 금지 — 편집거리도 tf-idf도 없다.
  이어서 규약 문구 한 줄을 **원문 그대로** 박는다.
- **말미 고정 문구**: 요구사항의 두 줄을 상수 `QUERY_FOOTER`로 원문 그대로.

상한 집행은 "줄 리스트를 만들고 `estimate_tokens`가 상한을 넘는 동안 뒤에서 한 줄씩
버린다"로 통일한다. 줄 중간을 자르지 않는다(좌표가 깨지면 브리핑의 존재 이유가 없다).

### 1.3 캐시 (§3)

- 파일: `<slice>/briefings/<stage>.md`, 사이드카 `<slice>/briefings/<stage>.json`.
- 키: `sha1(stage | head_commit(cwd) | sha1(requirement) | sha1(plan))`.
  plan digest가 곧 scope의 상위집합이므로 "같은 커밋 + 같은 scope"를 정확히 덮는다
  (더 좁게 잡으면 사람이 게이트에서 plan.md를 고친 걸 놓친다). 사이드카에는 사람이
  읽을 `scope` 목록도 함께 적는다.
- `head_commit`이 없으면(비-git, 레거시 in-place) **재사용하지 않는다** — 같음을
  증명할 수 없으면 다시 만든다.
- 히트: md를 그대로 읽어 `reused=True`로 돌려주고 그래프 조회도 하지 않는다
  (재생성 스킵이 요구사항이다). `ensure_graph`는 히트 판정 **전에** 부르지 않는다 —
  캐시가 맞으면 갱신조차 사지 않는다. 단 dirty marker가 있으면 커밋이 같아도 코드가
  움직였다는 뜻이므로 **먼저 갱신하고 키를 다시 계산**한다(커밋은 그대로라 키는
  같지만, marker는 소비되어야 다음 조회가 두 번 값을 치르지 않는다).

## 2. `aidev/graph/db.py` — 읽기 메서드 4개 추가

스키마 변경 없음 → `GRAPH_SCHEMA`는 1 그대로. SQL은 이 파일에만 산다는 집안 규칙을
지키기 위해, briefing.py가 `db.conn.execute`를 직접 쓰지 않는다.
`summaries()`(line 369) 아래에 붙인다.

```python
def directory_counts(self, depth: int = 1) -> List[sqlite3.Row]
    # SELECT path, COUNT(*) ... GROUP BY path  → 파이썬에서 상위 depth 조각으로 합산
def entry_points(self, limit: int = 12) -> List[sqlite3.Row]
    # calls c JOIN functions t ON t.id=c.resolved_id
    # WHERE t.is_test=0 GROUP BY t.id ORDER BY COUNT(*) DESC LIMIT ?
def by_path(self, paths: Sequence[str]) -> List[sqlite3.Row]
    # WHERE path IN (...) ORDER BY path, lineno   (빈 목록이면 [] 즉시)
def search(self, tokens: Sequence[str], limit: int = 60) -> List[sqlite3.Row]
    # name/qualname LIKE %tok% OR summary LIKE %tok% (is_test=0), 토큰당 한 번,
    # 파이썬에서 id로 중복 제거 + 겹침 수 집계
```

`aidev/graph/__init__.py`: `from .db import GraphDB, open_db`로 넓히고 `__all__`에
`open_db` 추가 (briefing이 `graph.open_db`를 쓴다).

## 3. `aidev/pipeline.py` — 스위치 · 프롬프트 · 훅

### 3.1 front matter 스위치 (§4)

- `resolve_briefing(fields)`를 `resolve_spec_check`(line 507) 바로 아래에.
  `_OFF_WORDS`/`_ON_WORDS`를 그대로 재사용, 오류문은
  `"briefing: needs 'on' or 'off', got {0!r}"`. 기본 True.
- `KNOWN_FRONT_MATTER_KEYS`(line 539)에 `"briefing"` 추가.
  → **기존 테스트 2개가 이 문자열에 걸린다.** `tests/test_pipeline.py:2789`의
  `known: ...` 문자열과 `:2819`의 집합. 둘 다 `briefing`을 더해 갱신 (§7).
- `PipelineConfig`에 `briefing: bool = True` (line 2033 `graph_hook` 옆).
- `_config`(line 4270 근처):
  `briefing=resolve_briefing(fields) and not getattr(args, "no_briefing", False)`.
- `add_parser`: `--no-graph` 바로 뒤에
  `--no-briefing` (`action="store_true"`, help "세션 발사 전 브리핑을 차리지 않는다").
- `start_slice`의 `--dry-run` guards 줄(line 4365)에 `, briefing {0}` 추가.
- **`--no-graph`는 브리핑도 끈다.** 그 플래그의 계약이 "그래프를 아예 쓰지 않는다"이고
  (`_open_workspace`의 docstring), `tests/test_graph.py:563`이 worktree에
  `.aidev/graph`가 아예 없기를 요구한다. `cfg.briefing and cfg.graph_hook`로 판정.

### 3.2 프롬프트 슬롯

- 새 상수:
```python
_BRIEFING_NOTE = """
{briefing}
"""
```
- `_PLAN_PROMPT`: `REQUIREMENT` 블록과 `INSTRUCTIONS` 사이에 `{briefing}` 한 칸.
- `_IMPLEMENT_PROMPT`: `{approval}` 다음, `INSTRUCTIONS` 앞에 `{briefing}` 한 칸.
- `_TEST_PROMPT`는 **건드리지 않는다** (test 단계는 브리핑 대상이 아니다 — 요구사항이
  plan/implement/amend/repair만 지목했고, verify 엔진이 켜져 있으면 test 세션 자체가
  없다). `extra` dict가 implement 전용이므로 `briefing`은 거기로 넘기면 test 템플릿과
  충돌하지 않는다.
- `build_prompt(..., briefing: str = "")`:
  - plan: `_PLAN_PROMPT.format(requirement=..., briefing=_brief_note(briefing))`
  - implement: `extra["briefing"] = _brief_note(briefing)`
  - `_brief_note(text)`는 빈 문자열이면 `""`를 돌려준다 → **off일 때 프롬프트가
    지금과 바이트 단위로 같다.** 이걸 테스트로 못 박는다.
  - 브리핑 텍스트는 `format`의 *인자*이지 템플릿이 아니므로 안에 `{`/`}`가 있어도
    안전하다(JS 시그니처).

### 3.3 발사 훅 — `run_stage` 안 한 곳

`BRIEFING_STAGES: Tuple[str, ...] = ("plan", "implement")`

`run_stage`의 while 루프 안, `set_status(...)` 바로 앞:

```python
brief = prepare_briefing(cfg, rec, state, stage, requirement, fixed=spec.prompt)
...
prompt = spec.prompt or build_prompt(..., briefing=brief.get("text", ""))
```

`prepare_briefing(cfg, rec, state, stage, requirement, fixed=None) -> Dict[str, Any]`:

1. `fixed is not None`(decompose/diagnose) 이거나 `stage not in BRIEFING_STAGES`
   이거나 `not (cfg.briefing and cfg.graph_hook)` → `{}` 즉시. **아무 일도 하지 않는다.**
2. `getattr(rec, "briefings_dir", None) is None` (EpicRecord) → `{}`.
3. `try:` 안에서 `briefing.build(cfg.cwd, stage, requirement, rec.read_plan(), ...)`,
   캐시 히트가 아니면 md + 사이드카 기록(`write_text_atomic` / `write_json_atomic`).
4. `state["briefing"][stage] = {"tokens", "chars", "sections", "path", "reused",
   "graph_functions", "at"}` — 바로 뒤 `set_status`가 published 한다.
5. `say("briefing: {0} tokens, sections {1} -> {2}{3}")` 한 줄
   (재사용이면 `" (reused)"`).
6. `except Exception as exc:` → `say("warning: no briefing this stage ({0})".format(exc))`
   후 `{}`. **파생 캐시가 슬라이스를 죽일 수 없다** — `mark_graph_stale`(line 2968)이
   이미 세운 원칙 그대로.

`SliceRecord`에 프로퍼티 2개 (line 981 `plans_dir` 옆):
```python
@property
def briefings_dir(self) -> Path:  # <slice>/briefings/
def briefing_path(self, stage: str) -> Path:  # briefings/<stage>.md
def briefing_meta_path(self, stage: str) -> Path:  # briefings/<stage>.json
```

`mirror_history`(line 2854)는 **손대지 않는다** — 브리핑은 그래프에서 언제든 다시
만들 수 있는 파생물이고, 매 단계 커밋에 5k 토큰짜리 md를 태우는 것은 값을 두 번
치르는 일이다. slice 디렉터리에만 남는다(요구사항이 요구한 보존 위치가 거기다).

## 4. 계측 (§5)

- `StageRun`에 `briefing_tokens: int = 0`, `graph_queries: int = 0` 필드 추가
  (기본값이 있으므로 기존 생성자 호출 전부 그대로).
- `run_stage`가 `execute_stage` 반환 직후 두 값을 채우고 `record_run` 호출:
  `run.briefing_tokens = brief.get("tokens", 0)`,
  `run.graph_queries = count_graph_queries(run.telemetry)`.
- 새 함수:
```python
GRAPH_QUERY_PREFIX = "aidev graph"
def count_graph_queries(telemetry: Dict[str, Any]) -> int:
    """세션이 조회 동사를 몇 번 썼는지 - Bash 호출 target에서 센다."""
```
  `telemetry["observed"]["calls"]`를 훑어 `tool == "Bash"`이고 target이
  `aidev graph`로 시작하는 것을 센다.
- `record_run`(line 2554)에 `"briefing_tokens"`, `"graph_queries"` 두 키 추가
  (runs.json은 append-only이고 새 키는 옵셔널이라 기존 리더가 깨지지 않는다).
- `render_summary`(line 3660) — RESULT 표는 **열을 늘리지 않는다**(포맷 문자열이
  여러 테스트의 눈에 걸린다). 대신 totals 줄 뒤에 브리핑이 하나라도 있을 때만
  한 줄을 더한다:
```
briefing   plan 1.2k, implement 3.1k tok   reused 1   graph queries 0
```
  하나도 없으면 출력은 지금과 완전히 같다.

**조회 횟수가 왜 지금은 0으로 읽히는가 (요구사항이 요구한 사유 명시).**
계측 자체는 위처럼 가능하고 실제로 넣는다. 다만 오늘 어떤 단계도 `aidev graph`를
실행할 권한이 없다: plan은 readonly 프로파일이 `Bash` 자체를 `--disallowedTools`로
막고(`runner.py:31`), implement/test는 `allowed_tools_for`(line 2088)가 내주는
규칙에 graph 동사가 없다. 그래서 이 슬라이스는 **권한을 새로 열지 않는다** — 여는 순간
`allowed_tools_for`의 계약이 바뀌고, 정확한 동등 비교를 하는 기존 테스트 3개
(`test_a_setup_command_no_longer_crowds_out_the_declared_test_command`,
`test_declared_test_commands_replace_the_built_in_rules`, 그리고 `aidev`가 PATH에
있느냐에 따라 결과가 갈리는 diet 테스트)가 환경에 따라 흔들린다. 요구사항이 요구한
것은 "고정 문구"와 "가능하면 계측"이지 권한 개방이 아니다. **탈출구는 이미 있다** —
사람이 `--allow-tool 'Bash(aidev graph:*)'`를 주면 그 순간부터 카운터가 0이 아닌
값을 읽는다. 이 사실을 README에 그대로 적는다.

## 5. `aidev/epic.py` — 일곱 번째 키

- `validate`(line ~298)의 리졸버 목록에 `pipeline.resolve_briefing(fields)` 추가.
  이 함수의 docstring이 스스로 "여섯 키 중 둘이 검사에서 빠져 있었다"를 회고하고 있으니
  새 키를 여기 안 넣는 것이 똑같은 결함이다.
- `_DECOMPOSE_PROMPT`(line ~229)의 "exactly six keys" → seven, 그리고
  `'briefing:' (on/off)` 한 조각 추가.

## 6. README

1. `### 파이프라인 훅 — lazy` 끝줄(1129) "브리핑 생성기와 plan 프롬프트 주입은
   2단계..." 를 걷어내고 그 자리에 새 절 **`## 브리핑 생성기 (v0.6, 2단계)`**:
   - 왜 (실측 근거: plan 탐색 20-59턴, implement 재탐색 $2-4/slice, repeated reads ×5-11)
   - LOD 3단이 각각 무엇인지 + 실제 출력 예시 블록
   - 생성 시점(plan/implement 발사 직전, repair·amend 포함), 저장 위치
     `.aidev/slices/<id>/briefings/<stage>.md`, 브랜치에 안 실리는 이유
   - 신선도·캐시 규약 (dirty면 증분 먼저 / 같은 커밋+같은 plan이면 재사용)
   - `briefing: off`와 `--no-briefing`, `--no-graph`가 브리핑도 끈다는 사실
   - 계측: RESULT의 briefing 줄, runs.json의 `briefing_tokens` / `graph_queries`,
     그리고 **조회 동사에 권한이 없어 지금은 0이라는 것과 `--allow-tool`로 여는 법**
   - 매칭은 문자열/식별자 겹침이 전부라는 것 — 임베딩은 하지 않는다
2. 플래그 표(1014 아래)에 `| --no-briefing | 세션 발사 전 브리핑을 차리지 않는다 (v0.6) |`
3. front matter 문서: 679행의 `known: ...` 예시 문자열과 666-668행의 "여섯 키"를
   `briefing` 포함으로 갱신.

## 7. 검증

`python -m pytest -q` (worktree의 aidev를 테스트하려면 `python -m` 필수).

### 새 파일 `tests/test_briefing.py`

`tests/test_graph.py`의 `mini` fixture 패턴을 그대로 빌려 쓴다(같은 디렉터리이므로
`from test_graph import mini` 가능 — test_graph도 test_pipeline에서 fixture를 그렇게
가져온다).

1. `test_the_briefing_has_all_three_scales` — plan 단계는 REPO MAP + RELATED,
   SCOPE 없음; implement 단계는 SCOPE + ALREADY EXISTS까지 4절.
2. `test_the_footer_is_verbatim_and_last` — 고정 문구 두 줄이 끝에 원문 그대로.
3. `test_related_matches_identifiers_not_magic` — requirement에 `helper`를 쓰면
   `pkg/other.py:helper`가 좌표와 함께 뜨고, 아무 관련 없는 단어는 아무것도 못 부른다.
4. `test_scope_carries_the_spec_record_and_a_real_excerpt` — plan.md가 `pkg/core.py`와
   `top`을 지목하면 `@param`/`@flow` 원문 + 소스 발췌 줄번호가 나온다.
5. `test_reuse_candidates_only_for_implement` — 이름 토큰이 겹치는 기존 함수가
   나오고 규약 문구가 붙는다. plan 단계에는 그 절이 아예 없다.
6. `test_every_section_respects_its_token_cap` — 함수를 200개 만든 repo에서
   `estimate_tokens(brief.text)`가 총상한 이하이고 `+N more`가 찍힌다.
7. `test_a_missing_graph_is_built_before_the_briefing` — DB 없는 repo에서 build()
   한 번에 graph.db가 생긴다.
8. `test_a_dirty_graph_is_refreshed_first` — `mark_dirty` 후 새 함수를 쓰면 그
   함수가 브리핑에 들어오고 marker가 사라진다.
9. `test_the_same_commit_and_plan_reuse_the_briefing` — 두 번째 호출이
   `reused=True`, md의 mtime이 그대로. plan.md를 고치면 `reused=False`.
10. `test_a_broken_graph_costs_no_briefing_and_no_exception` — `open_db`를 None으로
    monkeypatch → 예외 없이 빈 결과.

### `tests/test_pipeline.py` 추가 (fake 기반, Done Criteria 직결)

11. `test_the_plan_stage_is_launched_with_a_briefing` — 슬라이스 완주 후
    `briefings/plan.md` 존재 + `invocations(log)[0]["prompt"]`에 `# BRIEFING`과
    고정 문구가 들어 있음. **그리고 worktree의 `git status --porcelain -uall`이
    비어 있음** (readonly 증명이 그래프 빌드에 걸리지 않았다는 증거).
12. `test_briefing_off_changes_the_prompt_by_not_one_byte` — 같은 requirement를
    `briefing: off`로 한 번, on으로 한 번 돌려 plan 프롬프트를 비교:
    off 쪽에는 `BRIEFING`이 없고, `briefings/` 디렉터리 자체가 생기지 않는다.
    추가로 `build_prompt("plan", req)`(인자 없음)과 `build_prompt("plan", req,
    briefing="")`가 문자열로 같음을 단위로 못 박는다.
13. `test_no_briefing_flag_matches_the_front_matter_switch` — `--no-briefing`.
14. `test_the_briefing_cost_is_recorded` — `state["briefing"]["plan"]["tokens"] > 0`,
    `runs.json`의 plan 항목에 `briefing_tokens > 0`과 `graph_queries == 0`,
    capsys 출력에 `briefing` 요약 줄.
15. `test_implement_gets_the_scope_and_the_reuse_rule` — implement 프롬프트에
    `기존 함수 재사용 우선`과 `## 3. SCOPE`.
16. `test_a_retry_reuses_the_briefing_it_already_paid_for` —
    `AIDEV_FAKE_MODE=quota`, `QUOTA_FAILS=1`, `_sleep` 무력화 → plan이 두 번 발사되고
    두 번째 발사 로그에 `(reused)`.

### 기존 테스트 수정 (전부 "새 기본값을 반영"이지 "실패를 덮기"가 아니다)

- `tests/test_graph.py:512` `test_a_stage_commit_marks_the_graph_stale_without_parsing_anything`
  → argv에 `"--no-briefing"` 추가. 이 테스트의 주장은 "**커밋 훅은** 파싱하지 않는다"이고,
  브리핑을 끄면 그 주장이 그대로 참으로 남는다. 주석 한 줄로 이유를 남긴다.
- `tests/test_graph.py:530` `test_the_first_query_after_a_commit_pays_for_the_refresh`
  → 같은 이유로 `"--no-briefing"` 추가 (DB가 미리 있으면 "There is no DB at all yet"
  이라는 이 테스트의 전제가 거짓이 된다).
- `tests/test_pipeline.py:2789` known 키 문자열에 `, briefing` 추가.
- `tests/test_pipeline.py:2819` 집합에 `"briefing"` 추가.
- `tests/test_epic.py:468` parametrize에 `("briefing: maybe", "needs 'on' or 'off'")` 추가.

수동 확인 1회: `aidev graph build --repo .` 후 이 저장소 자신을 대상으로
`python -c "from aidev import briefing; ..."`로 브리핑을 찍어 사람이 읽어보고 토큰 수를
RESULT에 적는다(1189 함수짜리 실물에서 상한이 실제로 먹는지 — tmp repo로는 못 보는 것).

## 8. 무엇이 잘못될 수 있는가

| 위험 | 왜 실재하는가 | 대응 |
|---|---|---|
| **plan readonly 증명 파괴** | 브리핑이 worktree 안에 graph.db를 만든다. `before`/`after` porcelain 비교가 그걸 보면 모든 plan 단계가 실패한다 | `ensure_graph_dir`가 `*` .gitignore를 먼저 쓴다(`build.py:70-77`, 기존 테스트 `test_graph.py:377`이 이미 보장). §7의 11번이 `git status` 공백을 직접 확인한다. 이게 깨지면 전 테스트가 빨개지므로 조용히 지나갈 수 없다 |
| **fake stub의 단계 오인** | `fake_pipeline_claude.detect_stage`는 프롬프트에서 `"<stage> stage"`를 찾는다. 브리핑이 그 문자열을 담은 summary를 실어오면 stub이 엉뚱한 단계로 답한다 | e2e repo에는 소스 파일이 거의 없어 실물 위험은 없다. 그래도 브리핑 헤더를 `# BRIEFING <stage>`로 쓰고 stub이 먼저 보는 `DIAGNOSE STEP`/`decompose` 같은 마커를 브리핑이 생성하지 않도록 절 제목을 영문 대문자 명사구로 고정 |
| **첫 브리핑이 전체 빌드라 느리다** | 새 worktree에는 `.aidev/graph/`가 없다(gitignore되므로 따라오지 않는다). 큰 저장소면 plan 발사 전에 수 초 | 받아들인다 — 그 값을 치러야 브리핑이 성립한다. `say`에 파일·함수 수를 찍어 비용이 보이게 한다. 싫으면 `--no-briefing` |
| **프롬프트가 오히려 비싸진다** | 상한 없는 브리핑은 절약하려던 토큰을 그대로 쓴다 | 절별 상한 + 총상한, 그리고 토큰 수를 RESULT/계기판에 **강제로** 노출. A/B는 `briefing: off`로 실측 |
| **캐시가 낡은 브리핑을 먹인다** | 커밋은 같은데 사람이 worktree 파일을 고친 경우 | dirty marker가 있으면 커밋이 같아도 먼저 갱신한다. 그래도 남는 창은 "커밋도 같고 marker도 없는데 파일만 다름"뿐 — 그 경우 그래프 자체가 이미 틀렸으므로 브리핑만의 문제가 아니다 |
| **`{}`가 든 브리핑이 `.format`을 깬다** | JS 시그니처, JSX 발췌 | 브리핑은 템플릿이 아니라 `format`의 인자다. 12번 테스트가 중괄호 든 발췌로 이를 확인 |
| **epic 경로 오염** | decompose는 EpicRecord로 `run_stage`를 부른다 | `spec.prompt`가 고정이라 분기 자체를 안 탄다 + `briefings_dir` 없는 레코드는 즉시 반환. test_epic 전량으로 확인 |
| **한 슬라이스가 파일 10개를 건드린다** | 크기 경고(`PLAN_FILE_WARN=12`, `PLAN_TEST_WARN=5`) | 10파일·테스트 4파일로 문턱 아래. requirement가 이미 `max_turns: implement=160`을 선언해 두었다 |

## 9. 하지 않는 것 (명시)

임베딩·시맨틱 검색, UI, Graph Notes, codebase-map 스킬, decompose 지도,
`allowed_tools_for`의 권한 개방, test 단계 브리핑, `_TEST_PROMPT` 변경,
`mirror_history` 확장, `GRAPH_SCHEMA` 상승, 브리핑 실패로 슬라이스를 죽이는 경로.
