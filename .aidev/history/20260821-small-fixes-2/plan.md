# PLAN — 소탕 2호: 임시파일 가드 + 실측 수리 4건

## 0. 실측 확인 (이 plan을 쓰기 전에 읽은 것)

| 항목 | 근거 |
| --- | --- |
| §2 원인은 **state.json 캐싱도, worktree/본진 선택도 아니다.** 발사 시점에 `launch_slice`가 `tasks/*.md`를 `.aidev/slices/<id>/requirement.md`로 **복사(동결)**하고(`pipeline.py:5439`), 이후 `continue_slice`(`:5652`) / `amend_slice`(`:5719`) / `replan_slice`(`:5891`)는 **그 동결본만** 읽는다. 원본 `tasks/*.md`는 다시 열리지 않는다 | `tasks/pipeline-gen2b.md` = `implement=160` vs `.aidev/history/20260818-pipeline-gen2b/requirement.md:3` = `implement=120`. `tasks/graph-trace.md` = `implement=140` vs `.aidev/history/20260820-graph-trace/requirement.md:3` = `implement=80` — 상향 미반영 2건 모두 이 한 가지 원인 |
| §4의 실물이 저장소 안에 있다 | `tasks/graph-trace.md`가 지금 **cp949로 저장돼 있다**(utf-8로 읽으면 `# Graph �� ? Trace ���`). 이 파일로 `--requirement`를 쏘면 `read_text(encoding="utf-8")`(`pipeline.py:5308`)이 `UnicodeDecodeError`를 그대로 올리고, `cmd_pipeline`(`:5080`)은 `PipelineError`만 잡으므로 traceback이 노출된다 |
| §3 현행 동작 | `finish_stage`(`pipeline.py:4166-4192`)에서 `TEST_RESULT: FAIL`은 `return`만 하고 `failure.md`를 **안 쓴다**. 같은 블록의 spec 위반 분기(`:4182-4186`)만 `verify.render_failure_md`로 쓴다 |
| §5 대상 파일이 **이 브랜치에 없다** | `Glob **/functiondb*` → 0건, `grep -i "functiondb\|context-ab"` → `tasks/small-fixes-2.md`와 그 사본뿐. `tasks/specs/`도 없다. 본진 working tree에 untracked로 있을 가능성이 크지만 worktree에서는 보이지 않고, 본진을 들여다보는 것은 격리 규약 위반이다 |
| 신규 추가 파일 목록을 만들 수 있는 자리 | `spec_base_commit`(`pipeline.py:3654`)이 이미 `commits["requirement"]`를 base로 준다. `run_spec_check`(`:3673`)가 그 base로 `base..HEAD`를 읽는다. implement 커밋은 test 단계 **전에** 이미 끝나 있다(`run_pipeline:4396`) |

---

## 1. 바뀌는 파일

| 파일 | 무엇이 바뀌나 |
| --- | --- |
| `aidev/verify.py` | 임시파일 이름 판정기(순수 함수), `VerifyResult.temp_files`, 리포트 렌더링, 세션 보고문 → `VerifyResult` 변환기 |
| `aidev/workspace.py` | `added_paths()` 한 개 추가 (`git diff --diff-filter=A`) |
| `aidev/pipeline.py` | 임시파일 검사 호출(엔진·폴백 양쪽), 폴백 FAIL의 `failure.md`, front matter 재동기화, utf-8 한 줄 에러, `--no-temp-guard`, `tasks/specs/` 발사 거부 |
| `aidev/epic.py` | requirement 읽기 2곳을 공용 헬퍼로 |
| `aidev/cli.py` | `cmd_run`의 prompt 읽기에 같은 한 줄 에러 |
| `tests/fake_pipeline_claude.py` | `AIDEV_FAKE_TEMPFILE` 모드 |
| `tests/test_verify.py` | 임시파일 가드 단위 + 파이프라인 테스트 |
| `tests/test_pipeline.py` | 상향 재현, 폴백 failure.md, 비utf8, `tasks/specs/` 거부 |
| `tasks/specs/README.md` (신규) | 우산 명세 보관소 규약 |
| `README.md` | 4곳 문서 갱신 |

건드리지 않는 것: `desktop/**`, `aidev/briefing.py`, `aidev/graph/**`, `aidev/reporter.py`, `aidev/recovery.py`, `aidev/telemetry.py`, `aidev/runner.py`.

---

## 2. §1 — 임시파일 기계 검출

### 2.1 패턴 목록 (여기서 확정)

`aidev/verify.py`에 둔다. git도 저장소도 모르는 **이름만 보는 순수 함수**라서 verify가 맞는 집이고, 단위 테스트가 붙는다.

```python
# 잔재 3건(.tsscratch, reindent_tmp.py, reindent.py)이 만들어 준 목록. 이름을
# 통째로 보지 않고 basename을 [^A-Za-z0-9]+ 로 쪼갠 '토큰'과 맞춘다: 'oldest.py'는
# 'old'가 아니고 'useDebug.ts'는 'debug'가 아니다. 오탐 하나가 slice 하나를
# 죽이므로, 넓히는 쪽이 아니라 정확한 쪽으로 골랐다.
TEMP_TOKENS: Tuple[str, ...] = (
    "tmp", "temp", "scratch", "scratchpad", "reindent",
    "debug", "bak", "backup", "wip", "untitled",
)
# 이름 어디에 있든 임시인 조각. '.tsscratch'처럼 토큰으로 안 쪼개지는 것들.
TEMP_SUBSTRINGS: Tuple[str, ...] = ("scratch",)
# 확장자 자체가 임시인 것. 편집기/머지 잔재.
TEMP_SUFFIXES: Tuple[str, ...] = (".tmp", ".temp", ".bak", ".orig", ".rej", ".swp", ".swo")
```

```python
def looks_temporary(path: str) -> bool:
    """이 경로의 파일 이름이 임시 파일 냄새를 풍기는가.

    @param path  저장소 상대 경로 (구분자는 아무거나)
    @flow  basename 소문자화 -> '~' 꼬리 -> 확장자 -> 부분문자열 -> 토큰
    """
```

- 판정은 **basename만** 본다. `src/debug/panel.ts`는 통과, `src/debug.ts`는 걸린다.
- `name.endswith("~")`도 걸린다(편집기 백업).
- `looks_temporary`는 `.py`/`.ts` 같은 확장자 토큰도 같이 보게 되지만 `py`/`ts`는 목록에 없으므로 무해하다.

### 2.2 무엇을 검사 대상으로 삼나 — `pipeline.run_temp_file_check`

`run_spec_check` 바로 옆(`pipeline.py:3673` 근처)에 같은 모양으로 둔다.

```python
def run_temp_file_check(cfg: PipelineConfig, state: Dict[str, Any]) -> List[str]:
    """이번 slice가 새로 추가한 파일 중 임시 냄새가 나는 것들, 경로 순으로.

    @param cfg    슬라이스 설정 - temp_guard가 이 검사를 끈다
    @param state  state.json, 이 slice가 어느 커밋에서 시작했는지 아는 곳
    @flow  off -> [] ; base 없음 -> [] ; 커밋된 추가분 + 아직 안 커밋된 추가분
           -> .aidev/ 제외 -> looks_temporary -> 정렬
    주요 내부 변수: added(신규 추가 경로), found(임시 후보)
    """
```

두 출처를 합친다.

1. **커밋된 신규 추가**: `workspace.added_paths(cfg.cwd, base, head)` — 새 함수, `git diff --name-only --diff-filter=A <base> <head>`. `base`는 `spec_base_commit(cfg, state)`(= requirement 커밋), `head`는 `workspace.head_commit(cfg.cwd)`. 잔재 3건은 전부 이 경로다.
2. **아직 커밋 안 된 추가**: `git_porcelain(cfg.cwd)`의 `??` / `A ` / `AM` 줄을 `_porcelain_path`로 푼 것. test 단계 커밋은 verify **이후**에 일어나므로, 이걸 안 보면 implement가 남긴 임시파일이 test 커밋에 실려 통과한다. (`.gitignore` 적용분은 porcelain에 안 나오므로 `__pycache__`·`node_modules`·`data/`는 자동 제외된다.)

제외 규칙 — **누락하면 자기 자신을 죽인다**:
- `_under_aidev(path)` 인 것 전부. `mirror_history`가 worktree의 `.aidev/history/<slice>/`에 쓰고, `write_text_atomic`이 그 옆에 `*.tmp`를 잠깐 만든다. 이 제외가 없으면 모든 slice가 자기 기록 때문에 FAIL한다.
- 첫 경로 조각이 `node_modules`, `data`인 것.

`workspace.GitError`는 삼키고 `say("note: ...")` 한 줄 + `[]`. **검사기가 stage를 죽이는 이유가 되어서는 안 된다**는 `run_spec_check`의 규칙을 그대로 따른다.

### 2.3 결과가 흐르는 길

`aidev/verify.py`:

```python
@dataclass
class VerifyResult:
    ...
    spec_violations: List[Any] = field(default_factory=list)
    temp_files: List[str] = field(default_factory=list)   # 새 필드

    @property
    def clean(self) -> bool:
        return self.ok and not self.spec_violations and not self.temp_files

    def failure_count(self) -> int:
        counted = self.failed or len(self.failures)
        return counted + len(self.spec_violations) + len(self.temp_files)

    def to_dict(self):  # "temp_files": list(self.temp_files) 추가
```

- `summarize_for_agent`: spec 줄 뒤에 `"  temp file: {0}"` 를 최대 `MAX_FAILURES_LISTED`개.
- `render_failure_md`: `## Spec violations` 뒤에 새 절
  ```
  ## Temporary files (N)
  - reindent_tmp.py
  - desktop/src/.tsscratch
  이 slice가 새로 추가한 파일이고, 이름이 임시 작업 잔재로 읽힌다. 브랜치에
  남길 파일이 아니면 지우고, 남길 파일이면 임시로 읽히지 않는 이름으로 바꿔라.
  ```
  20개 초과분은 `- ... N more`.
- `worst_exit_code`는 손대지 않는다. `aidev verify` CLI 경로는 `temp_files`를 채우지 않으므로 동작이 그대로다.

`aidev/pipeline.py`:

- `run_verify`(`:3939` 바로 아래): `result.temp_files = list(run_temp_file_check(cfg, state))` — spec 검사와 **같은 자리, 같은 무조건성**("명령이 초록이어도 규약 위반은 위반이다").
- `failure_grade`(`:3737`): `if not result.parsed and not result.spec_violations and not result.temp_files: return "large"`. 이게 없으면 명령이 통과해 `parsed=False`인 채 임시파일만 걸렸을 때 등급이 large가 되어 **진단 세션을 산다** — 지우면 끝나는 실패에 세션값을 쓰는 셈이다.
- `record_verify`는 이미 `result.to_dict()`를 저장하므로 `state["stages"]["test"]["verify"]["attempts"][n]["temp_files"]`가 자동으로 남는다.

### 2.4 끄는 스위치

오탐이 slice를 통째로 죽이므로 탈출구가 필요하다. front matter 키는 **늘리지 않는다**(`warn_unknown_front_matter`의 known 목록·README·`epic.validate_items`가 전부 따라 움직여야 한다). `--no-verify-engine` / `--no-spec-check`와 같은 계열의 **플래그 하나**로 끝낸다.

- `PipelineConfig`에 `temp_guard: bool = True`
- `add_parser`(`:4996` `--no-spec-check` 옆)에 `--no-temp-guard`
- `_config`에 `temp_guard=not getattr(args, "no_temp_guard", False)`
- `start_slice` dry-run의 `guards` 줄(`:5353`)에 `temp-guard {on|off}` 추가

---

## 3. §2 — 상향 미반영 수정

### 3.1 무엇을 고치나

동결본은 **본문(body)의 기록**으로 유지하고, **front matter만** 원본에서 다시 읽는다. 본문까지 다시 읽으면 사람이 요구사항 본문을 바꿔치기했을 때 slice가 조용히 다른 물건이 된다 — 그건 `--amend`가 하는 일이다.

1. `new_state`는 그대로 두고, `launch_slice`에 `source: Optional[Path] = None`을 추가한다. `start_slice`가 `launch_slice(args, repo, data_dir, text, source.stem, source=source)`로 넘기고, `launch_slice`가 `state["requirement_source"] = str(Path(source).resolve())`를 남긴다 (schema 2 이후 키 규약대로 **있을 때만** 넣는다 — epic이 만든 slice는 원본 파일이 없으므로 이 키가 없고, 그러면 재동기화는 그냥 건너뛴다).

2. 새 함수:

```python
def refresh_front_matter(rec: SliceRecord, state: Dict[str, Any]) -> List[str]:
    """발사 이후 원본 requirement의 front matter가 바뀌었으면 동결본에 반영한다.

    본문은 건드리지 않는다: 예산·모델·검증 명령은 사람이 slice 도중에 고쳐도
    되는 손잡이지만, 본문이 바뀌는 것은 다른 slice가 되는 일이라 --amend의 몫이다.

    @param rec    동결본을 들고 있는 slice 기록
    @param state  state.json - 원본 경로가 여기 남아 있다
    @flow  원본 경로 없음/못 읽음 -> [] ; 새 front matter 검증 -> 같으면 []
           : 동결본을 (새 front matter + 옛 본문)으로 다시 쓰고 바뀐 키를 돌려준다
    주요 내부 변수: fresh(원본 front matter), frozen(동결본 front matter)
    """
```

- 원본이 없거나(이름이 바뀌었거나 지워졌거나) 읽히지 않으면 `say("note: ...")` 한 줄 + `[]`. **resume이 이것 때문에 죽어서는 안 된다.**
- 새 front matter는 **동결본을 다시 쓰기 전에** `resolve_max_turns` / `resolve_models` / `resolve_test_commands` / `resolve_setup` / `resolve_spec_check` / `resolve_auto_resume` / `resolve_auto_extend`로 전부 검증한다. 오타가 들어왔으면 `PipelineError`(exit 2)로 원본 경로를 지목하고 멈추되, **동결본은 손대지 않는다** — 발사 때와 같은 거부이고, 기록은 깨지지 않는다.
- 바뀐 키는 `["max_turns: implement=80 -> implement=140"]` 모양으로 돌려주고 호출자가 `say`로 찍는다.

3. 호출 자리 — `text = rec.requirement_path.read_text(...)` **직전** 세 곳:
   - `continue_slice`(`:5652`)
   - `amend_slice`(`:5719`)
   - `replan_slice`(`:5891`)

   세 곳 모두 그 뒤로는 코드가 그대로다: 이미 `parse_front_matter` → `_config(..., fields=fields)`로 흐르므로, 동결본이 최신이면 `cfg.turns_for("implement")`가 최신값이 된다.

4. `SliceRecord.requirement_path`의 docstring을 사실에 맞춘다: "the human's own words, copied in at the start and never rewritten by a stage" → 본문은 여전히 그렇고, front matter는 사람이 원본을 고치면 resume이 따라간다는 문장을 추가.

5. `mirror_history`(`:3519`)가 이미 동결본을 worktree로 복사하므로, 갱신된 front matter는 다음 stage 커밋을 타고 브랜치에 남는다 — 별도 작업 없음.

### 3.2 `restore_extensions`와의 순서

`_finish → run_pipeline → restore_extensions`(`:4302`)는 **엔진이 올려 준 예산의 바닥을 복원**할 뿐이고 `granted > cfg.turns_for(stage)`일 때만 덮어쓴다. 사람이 140으로 올렸는데 엔진 원장이 120이면 140이 이긴다 — 기존 주석의 약속 그대로다. 새 코드는 `cfg`가 만들어지기 **전**에 동작하므로 충돌 없음.

---

## 4. §3 — 폴백 test FAIL도 `failure.md`를 남긴다

### 4.1 verify.py에 변환기 하나

```python
def result_from_report(text: str, ok: bool = False,
                       command: str = "(the test stage's own report)") -> VerifyResult:
    """에이전트가 낸 보고문을 엔진의 결과와 같은 모양으로 읽는다.

    폴백 경로에는 실행한 명령도 exit code도 없다. 있는 것은 세션이 쓴 문장뿐이고,
    그 문장은 대개 pytest/jest의 요약을 그대로 인용한다 - 엔진이 읽는 것과 같은
    글자다. 그래서 같은 파서를 태우면 사유와 좌표가 같은 규격으로 나온다.

    @param text     세션의 최종 보고문
    @param ok       명령 자체는 돌았는가 (판정이 FAIL이면 False)
    @param command  리포트의 'Command' 절에 적을 이름
    """
```

`parse_output(text)`로 `failures`/`counts`를 뽑고, `CommandResult(command=command, exit_code=0 if ok else 1, output=text)` 하나를 넣는다. 이러면
- 모델이 `FAILED tests/test_night.py::test_x - AssertionError: ...`를 인용했으면 → `## Failed tests (N)`에 **좌표까지** 나온다.
- 아무것도 파싱 안 되면 → `render_failure_md`의 `## Output (tail 60 lines)` 분기가 보고문 꼬리를 그대로 인용한다. **사유는 항상 남는다.**
- verify.py는 여전히 pipeline을 모른다(문자열 in, dataclass out).

### 4.2 `finish_stage`의 test 분기 재구성 (`pipeline.py:4166-4191`)

```python
if run.stage == "test":
    verdict = parse_test_verdict(run.text)
    entry["verdict"] = verdict
    state["test_verdict"] = verdict
    if verdict == VERDICT_UNKNOWN:
        say("warning: the test stage gave no TEST_RESULT line; result is unknown")
    # 규약은 이 경로에서도 검사된다: 규칙을 만드는 것은 엔진이 아니라 diff다.
    violations = run_spec_check(cfg, state)
    temps = run_temp_file_check(cfg, state)
    if verdict == "fail" or violations or temps:
        state["test_verdict"] = entry["verdict"] = "fail"
        result = verify.result_from_report(run.text, ok=(verdict != "fail"))
        result.spec_violations = list(violations)
        result.temp_files = list(temps)
        head = workspace.head_commit(cfg.cwd) or ""
        write_text_atomic(rec.failure_path, verify.render_failure_md(
            result, slice_id=rec.slice_id, attempt=1, last_commit=head,
            last_subject=(workspace.log_subjects(cfg.cwd, "-1") or [""])[-1] if head else "",
        ))
        return <사유 문자열>
    return None
```

사유 문자열은 세 갈래를 한 문장으로 합치되 **기존 문구를 부분문자열로 보존**한다(`tests/test_pipeline.py:1119`가 `"TEST_RESULT: FAIL" in state["reason"]`을 본다):

- FAIL 판정: `"test stage reported failing tests (TEST_RESULT: FAIL)\n    {failure_path}"`
- spec 위반만: 지금의 `"{n} function(s) changed without their spec keeping up:..."` 그대로
- 임시파일만: `"{n} temporary file(s) added by this slice:\n    ...\n    {failure_path}"`
- 둘 이상이면 줄을 이어 붙인다.

`last_commit`/`last_subject`를 넣는 것이 "verify 엔진 경로와 동일 규격"의 마지막 조각이다 — 엔진 경로(`run_verify:3947-3954`)가 하는 것과 같다.

폴백 경로는 v0.4대로 **재시도 없이 slice가 failed로 끝난다**(exit 1). 바뀌는 것은 문서가 남는다는 것 하나뿐이고, `run_pipeline`의 흐름은 손대지 않는다.

---

## 5. §4 — 비 utf-8 requirement는 한 줄로 거절

`pipeline.py`에 헬퍼 하나:

```python
def read_text_utf8(path: Path, where: str = "") -> str:
    """utf-8로 읽거나, traceback 대신 한 줄로 거절한다.

    실측: cp949로 저장된 tasks/*.md 하나가 UnicodeDecodeError를 그대로 올렸고,
    cmd_pipeline은 PipelineError만 잡으므로 사람이 본 것은 스택 트레이스였다.

    @param path   읽을 파일
    @param where  에러에 이름 붙일 때 쓸 말 (없으면 경로)
    @flow  read_text -> UnicodeDecodeError -> 경로 + 안내 한 줄의 PipelineError
    """
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise PipelineError(
            "requirement 파일을 utf-8로 읽을 수 없다: {0}\n"
            "    파일의 utf-8 인코딩을 확인하라 (utf-8로 다시 저장하면 된다).".format(where or path)
        )
```

`PipelineError`의 기본 code는 `EXIT_USAGE`(2) — **exit code는 비정상 유지**이고, `cmd_pipeline`이 `error: ...`를 stderr에 찍는다. traceback 없음.

교체할 자리:
- `start_slice:5308`
- `continue_slice:5652`, `amend_slice:5719`, `replan_slice:5891` (동결본. 사람이 손으로 고칠 수 있는 파일이므로 같은 보호를 받는다)
- `epic.start_epic:712`, `epic.resume_epic:764` → `pipeline.read_text_utf8(...)`
- `mirror_history:3519`의 `try/except OSError`에 `UnicodeDecodeError`를 더한다(`UnicodeDecodeError`는 `ValueError`라 `OSError`로 안 잡힌다). 여기서는 거절이 아니라 **건너뛰기**가 맞다 — 기록을 못 비추는 것이 커밋을 막을 이유는 아니다.
- `cli.py:189`(`cmd_run`의 prompt)은 `PipelineError`를 잡는 사람이 없으므로 그 자리에서 `except UnicodeDecodeError` → 같은 문장을 stderr에 찍고 `return 2`.

`tasks/graph-trace.md`는 **다시 인코딩하지 않는다** — 이 slice의 범위 밖이고, 파일을 통째로 다시 쓰는 것은 별개의 결정이다. 이 slice가 하는 일은 그 파일을 쐈을 때 사람이 무엇이 잘못됐는지 한 줄로 읽게 만드는 것이다.

---

## 6. §5 — 우산 명세를 발사용 폴더에서 분리

### 6.1 실측: 옮길 파일이 이 브랜치에 없다

`tasks/functiondb-implement-context-ab.md`는 이 worktree 어디에도 없다(§0 참조). 본진 working tree에 untracked로 있을 가능성이 높지만, worktree에서 본진을 들여다보는 것은 이 저장소의 격리 규약이 금지한다. implement 단계가 `git mv`를 실행할 수도 없다 — 그 단계에 주어지는 Bash 규칙은 `aidev verify`와 `python -m aidev.specs`뿐이다(`allowed_tools_for:2365`).

그래서 §5를 **실행 가능한 형태로 쪼갠다**.

### 6.2 implement가 하는 것

1. `tasks/functiondb-implement-context-ab.md`가 **worktree에 있으면**: 첫 줄에 주석을 붙인 사본을 `tasks/specs/functiondb-implement-context-ab.md`로 Write하고 원본을 지운다. `commit_stage`의 `git add -A` + commit이 유사도로 **rename(R)으로 기록**하므로, `git mv`의 결과와 커밋 내용이 동일하다. 첫 줄:
   ```
   <!-- 우산 명세 — 직접 발사 금지, 2-1~2-4로 분해 발사 -->
   ```
2. **없으면**(현재 상태): 없는 문서를 지어내지 않는다. 대신 `tasks/specs/README.md`를 만든다.
   ```markdown
   # tasks/specs — 우산 명세 보관소

   여기 있는 문서는 **직접 발사 금지**다. `--requirement tasks/specs/...`는 거부된다.
   우산 명세는 2-1~2-4처럼 분해해서 `tasks/`의 개별 requirement로 발사한다.

   - `functiondb-implement-context-ab.md` — 아직 이 브랜치에 없다. 본진
     working tree에 untracked로 있다면 사람이 그쪽에서 옮겨야 한다:
     `git mv tasks/functiondb-implement-context-ab.md tasks/specs/`
     (옮긴 뒤 첫 줄에 위 주석을 붙일 것)
   ```
   그리고 최종 보고문에 "명명된 파일이 이 브랜치에 없어 이동은 못 했다"를 **명시**한다.

### 6.3 규약 문구가 아니라 기계로

"발사 금지 문서를 발사용 폴더에서 분리"의 목적은 **폴더 이름**이 아니라 **잘못 쏘는 것을 막는 것**이다. 폴더만 만들면 이 slice의 배경에 적힌 "규약 문구로 안 막힘"을 한 번 더 반복하게 된다. `start_slice`의 맨 앞, `resolve_requirement` 직후에 거부 하나:

```python
SPEC_DIR_NAME = "specs"

def refuse_umbrella_spec(source: Path, repo: Path) -> None:
    """tasks/specs/ 아래 문서는 발사 대상이 아니다.

    @param source  발사하려는 requirement 경로
    @param repo    사용자의 저장소
    @flow  경로 조각에 'tasks/specs'가 있으면 PipelineError, 아니면 아무것도 안 한다
    """
```

`tasks/specs/`를 지나는 경로면 exit 2로 거절하고, "우산 명세는 직접 발사하지 않는다 — 개별 requirement로 분해해서 `tasks/`에 두고 쏴라"를 한 줄로 말한다. `--epic`은 막지 않는다(우산을 분해하는 것이 epic의 일이다).

---

## 7. 테스트

### 7.1 새 stub 모드 — `tests/fake_pipeline_claude.py`

```
AIDEV_FAKE_TEMPFILE   implement가 cwd에 이 이름들(쉼표 구분)의 파일을 만든다.
                      기본값 reindent_tmp.py — 실측 잔재 3건 중 하나 그대로.
```
`AIDEV_FAKE_SPECLESS` 블록 바로 아래, 같은 모양으로.

### 7.2 `tests/test_verify.py`

| 이름 | 내용 |
| --- | --- |
| `test_which_names_read_as_temporary` (parametrize) | HIT: `reindent_tmp.py`, `reindent.py`, `.tsscratch`, `desktop/src/x.tsscratch`, `notes.bak`, `panel.tsx.orig`, `debug.py`, `a/b/wip-notes.md`, `main.py~`. MISS: `aidev/verify.py`, `tests/test_pipeline.py`, `src/debug/panel.ts`, `oldest.py`, `useDebug.ts`, `templates/index.html`, `contemporary.md` |
| `test_a_temp_file_makes_the_result_dirty` | `VerifyResult(ok=True, temp_files=["x_tmp.py"])` → `clean is False`, `failure_count() == 1`, `to_dict()["temp_files"]` |
| `test_render_failure_md_lists_the_temp_files` | `## Temporary files (2)` + 두 경로 + 안내 문장 |
| `test_result_from_report_reads_the_agents_own_words` | `FAILED tests/test_night.py::test_x - AssertionError: expected 3, got 2` 를 담은 보고문 → `failures[0].where() == "tests/test_night.py:.."` 또는 이름, `message`에 사유 |
| `test_a_slice_that_adds_a_temp_file_fails_verification` | **Done Criteria "fake: 임시파일 포함 slice가 FAIL + 목록 표시"**. `AIDEV_FAKE_TEMPFILE=reindent_tmp.py`, `test_commands`는 통과하는 스크립트, `--max-repairs 0` → exit 1, `state["test_verdict"] == "fail"`, `failure.md`에 `## Temporary files (1)`과 `reindent_tmp.py`, `attempts[0]["commands"][0]["exit_code"] == 0`(명령은 통과했다), `attempts[0]["temp_files"] == ["reindent_tmp.py"]` |
| `test_the_repair_round_can_delete_a_temp_file` | 위와 같되 `--max-repairs` 기본 + `AIDEV_FAKE_TEMPFILE`을 두 번째 invocation부터 끄는 방식으로 수리 성공 → exit 0. (stub에 `AIDEV_FAKE_TEMPFILE_FIX=<n>` 추가, `AIDEV_FAKE_SPEC_FIX`와 같은 모양) |
| `test_no_temp_guard_lets_it_through` | 같은 slice + `--no-temp-guard` → exit 0 |
| `test_a_clean_slice_is_not_accused` | 기존 통과 slice가 그대로 통과하는지(회귀 핀). `.aidev/history/` 미러 때문에 오탐 나는 경우를 잡는다 |

### 7.3 `tests/test_pipeline.py`

| 이름 | 내용 |
| --- | --- |
| `test_a_committed_max_turns_rise_reaches_the_next_stage` | **Done Criteria "상향 재현 테스트"**. ① `requirement(repo, front="approval: plan\nmax_turns: implement=80")`, `git_repo(repo)` ② `_sleep`을 게이트 승인 없이 `KeyboardInterrupt`로 만들어 plan 뒤에서 멈춘다(`test_resume_slice_continues_after_the_process_is_killed`와 같은 수법) ③ **본진의 `tasks/doctor.md`를 `implement=140`으로 고치고 `git(repo,"add","-A")`+`commit`** ④ `approvals/plan.md`에 `approved` ⑤ `--resume-slice last` → exit 0 ⑥ `budgets(log) == ["80", "140", "80"]` — implement가 최신값으로 발사됐다 ⑦ 동결본도 갱신됐다: `(slice_dir(repo)/"requirement.md")`에 `implement=140`, **본문은 그대로**(`"밤 페이즈 의사 보호 로직"` 여전히 포함) |
| `test_a_body_edited_after_launch_does_not_change_the_slice` | 원본의 **본문**만 바꾼 뒤 resume → 동결본 본문 불변, 세션 프롬프트에도 옛 본문. front matter만 따라간다는 경계선의 증명 |
| `test_a_broken_max_turns_edit_stops_the_resume_and_keeps_the_record` | 원본을 `max_turns: planz=10`으로 고치고 resume → exit 2, stderr에 `unknown stage 'planz'`, **동결본은 손상되지 않음**(여전히 옛 값), 세션 0개 추가 |
| `test_a_slice_with_no_source_file_resumes_unchanged` | 발사 후 `tasks/doctor.md`를 지우고 resume → 정상 완주, 동결본 그대로 (epic slice와 같은 경로) |
| `test_the_fallback_test_stage_writes_a_failure_report` | **Done Criteria "폴백 FAIL 시 failure.md 존재"**. `test_commands` 없음(폴백 경로), `AIDEV_FAKE_VERDICT=FAIL` + `AIDEV_FAKE_TEXT`에 `FAILED tests/test_night.py::test_x - AssertionError: expected 3, got 2\nTEST_RESULT: FAIL` → exit 1, `failure.md` 존재, 안에 `# FAILURE`·`tests/test_night.py`(좌표)·`expected 3, got 2`(사유)·`last commit:` 줄 |
| `test_the_fallback_report_quotes_the_tail_when_nothing_parses` | 파싱 불가한 보고문 → `## Output (tail` 절에 보고문 꼬리. 사유는 언제나 남는다 |
| `test_a_requirement_that_is_not_utf8_is_refused_in_one_line` | **Done Criteria "비utf8 시 친절 에러 한 줄"**. `path.write_bytes("# 의사\n본문\n".encode("cp949"))` → exit 2, stderr에 경로 + `utf-8 인코딩 확인`, `"Traceback"`이 stderr에 **없음**, `invocations(log) == []`, `not pipeline.slices_root(repo).exists()` |
| `test_a_frozen_requirement_that_is_not_utf8_is_refused_on_resume` | 동결본을 cp949로 덮어쓴 뒤 `--resume-slice` → 같은 한 줄, exit 2 |
| `test_an_umbrella_spec_cannot_be_launched` | `tasks/specs/umbrella.md`를 만들고 `--requirement tasks/specs/umbrella.md` → exit 2, `분해` 안내가 stderr, 세션 0개 |

### 7.4 `tests/test_cli.py`

- `test_a_prompt_that_is_not_utf8_is_refused_in_one_line` — `aidev run --prompt <cp949 파일>` → exit 2, 한 줄.

### 7.5 테스트 파일 이름 주의 (자기 자신을 죽이는 함정)

**새 테스트 파일을 `tests/test_temp_guard.py` 같은 이름으로 만들면 안 된다.** basename 토큰에 `temp`가 들어가 이 slice가 자기 가드에 걸려 FAIL한다. 위 테스트는 전부 **기존 파일**(`tests/test_verify.py`, `tests/test_pipeline.py`, `tests/test_cli.py`)에 넣는다. 새 파일이 꼭 필요하면 `tests/test_tempguard.py`도 `temp`로 안 쪼개지지만 — 그냥 새 파일을 만들지 않는 쪽이 안전하다.

---

## 8. 검증

```
python -m pytest -q          # 이 slice의 test_commands. 엔진이 직접 돌린다.
```

Done Criteria 대응:

| 기준 | 무엇이 증명하나 |
| --- | --- |
| fake: 임시파일 포함 slice가 FAIL + 목록 표시 | `test_a_slice_that_adds_a_temp_file_fails_verification` |
| 상향 재현 테스트: 커밋된 상향값으로 발사됨 | `test_a_committed_max_turns_rise_reaches_the_next_stage` (`budgets(log) == ["80","140","80"]`) |
| 폴백 FAIL 시 failure.md 존재 | `test_the_fallback_test_stage_writes_a_failure_report` |
| 비utf8 시 친절 에러 한 줄 | `test_a_requirement_that_is_not_utf8_is_refused_in_one_line` |
| specs/ 이동 + 발사 금지 주석 | 대상 파일 부재 → `tasks/specs/README.md` + `test_an_umbrella_spec_cannot_be_launched`. 이동 자체는 **미완으로 명시 보고** (§6.1) |
| 기존 pytest 전량 통과 | 전체 실행 |

이 slice 자신이 §1의 첫 피험자다: implement가 임시파일을 남기면 자기 verify에 걸린다.

---

## 9. 잘못될 수 있는 것

| 위험 | 왜 위험한가 | 대응 |
| --- | --- | --- |
| **temp 가드 오탐이 모든 slice를 죽인다** | `mirror_history`가 worktree의 `.aidev/history/<slice>/`에 쓰고 `write_text_atomic`이 `*.tmp`를 스친다. 제외를 빠뜨리면 **모든** slice가 FAIL한다 | `_under_aidev` 제외를 먼저 넣고, `test_a_clean_slice_is_not_accused`로 핀을 박는다 |
| 기존 저장소에 이미 임시 이름 파일이 있다 | 검사는 **`base..HEAD`의 신규 추가분 + 미커밋 추가분**만 본다. 소급 없음 — `specs.check`의 "소급 금지"와 같은 규칙 | 설계로 배제 |
| 목록이 너무 좁다/넓다 | `debug`는 실제 모듈 이름일 수 있다(`src/debug.ts`) | basename 토큰 정확 일치로 좁히고, `--no-temp-guard` 탈출구를 둔다 |
| `--diff-filter=A`가 rename을 놓친다 | git이 rename으로 잡으면 `R`이라 `A`에 안 나온다 | 이 결함의 실측 사례는 전부 순수 신규 추가다. rename까지 잡으려다 정상 리팩터링을 죽이는 쪽이 더 나쁘다 — 의도적으로 `A`만 본다 |
| **front matter 재동기화가 slice를 도중에 바꿔치기한다** | `test_commands`를 원본에서 고치면 verify가 다른 명령을 돌린다 | 본문은 절대 안 따라간다. front matter 손잡이는 사람이 slice 도중에 고쳐도 되는 것들이고, 바뀐 키를 `say`로 **소리 내어** 찍는다 |
| 재동기화 중 원본이 깨졌다 | 오타 있는 front matter가 동결본을 덮어쓰면 기록이 망가진다 | **검증을 통과한 뒤에만** 동결본을 다시 쓴다. 실패하면 exit 2 + 동결본 무손상 |
| 원본이 지워졌거나 이름이 바뀌었다 | resume이 죽으면 안 된다 | note 한 줄 + 동결본 유지 |
| epic이 만든 slice | `requirement_source`가 없다 | 키가 없으면 재동기화 자체를 건너뛴다. epic 경로는 한 글자도 안 바뀐다 |
| 폴백 `failure.md`가 다음 stage 커밋에 실린다 | `mirror_history`가 `failure_path`를 이미 복사한다(`:3537`) — 그게 의도된 동작 | 그대로 둔다 |
| `state["reason"]` 문구 변경이 기존 테스트를 깬다 | `test_pipeline.py:1119`가 `"TEST_RESULT: FAIL"`을 본다 | **부분문자열로 보존**하고 경로만 덧붙인다 |
| `UnicodeDecodeError`가 `OSError`로 안 잡힌다 | `mirror_history`의 기존 `except OSError`를 그냥 지나쳐 traceback | `except (OSError, UnicodeDecodeError)`로 명시 |
| §5의 대상 파일이 없다 | Done Criteria 하나가 완결되지 않는다 | 지어내지 않는다. `tasks/specs/` + 발사 거부 기계 + README에 남은 일을 명시하고, 최종 보고에서 **못 한 것으로 보고**한다 |
| implement가 `git mv`를 못 쓴다 | 허용된 Bash 규칙에 git이 없다 | Write(신규) + 원본 삭제로 같은 커밋을 만든다(git이 rename으로 기록). 삭제 수단이 없으면 중복 파일을 남기지 말고 못 했다고 보고한다 |
| README가 실제 동작과 어긋난 채 남는다 | 881-882줄이 "폴백은 failure.md를 안 쓴다"고 못 박고 있다 | §10에서 4곳 갱신 |

---

## 10. README 갱신 (4곳)

1. **`### 단계별 턴 예산 (max_turns, v0.4.1)`** (293) — 끝에 한 문단: 발사 시점 동결본이 stage가 읽는 원본이었고(실측 2건: gen2b 160→120, trace 140→80), 이제 resume / `--amend` / `--replan`이 원본 `tasks/*.md`의 **front matter만** 다시 읽어 동결본에 반영한다. 본문은 따라가지 않는다(그건 `--amend`의 몫). 원본이 없거나 못 읽으면 note 한 줄 뒤 동결본 그대로.
2. **`### 검증의 결정론화 (v0.5)`** (321) — 임시파일 가드 절: 무엇을 보는가(신규 추가분 basename), 패턴 목록, `.aidev/` 제외, `--no-temp-guard`. 실측 3건(`.tsscratch`, `reindent_tmp.py`, `reindent.py`)을 근거로 적는다.
3. **`### 테스트 판정`** (881-882) — "폴백 경로는 v0.4 그대로 slice가 failed로 끝난다" 뒤에 "**이제 failure.md도 같은 규격(사유·좌표·last commit)으로 남긴다**". 재시도는 여전히 없다.
4. **`## Slice Pipeline (v0.2)`** (87) 근처 — `tasks/specs/`는 우산 명세 보관소이고 `--requirement`로 쏘면 거부된다는 두 줄.

인코딩 한 줄 에러는 README의 별도 절을 만들 만한 크기가 아니다 — 2번 절 안에 한 문장으로 붙인다.
