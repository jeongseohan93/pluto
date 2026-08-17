# 파이프라인 2세대 — 검증 결정론화 + 산출물 규격: 구현 계획

검증 루프에서 **에이전트 세션을 걷어내고**(엔진 직접 실행), 실패했을 때만 규격화된
문서(`failure.md` / `diagnosis.md` / `progress.md`)로 맥락을 남긴다. 그리고 낭비의
세 가지 기계적 원인(통짜 테스트 출력 / Write 재출력 / 빈 requirement 발사)을
프롬프트 지시가 아니라 **기계**로 막는다.

---

## 0. 읽고 확인한 사실 (근거 있는 것만)

- 단계 목록은 `STAGES = ("plan","implement","test")` (`aidev/pipeline.py:69`),
  정책은 `STAGE_POLICY` (`:74`). 단계별 실행은 `run_stage` (`:1611`) →
  `execute_stage` (`:1501`) → `runner.execute` (`aidev/runner.py:148`), **단계 하나 = 세션 하나**.
- test 단계의 판정은 오직 모델이 쓴 `TEST_RESULT:` 줄이다 (`parse_test_verdict`
  `:1284`, `finish_stage` `:2029`). 즉 현재 test 단계는 "재실행 + 한 줄 기록"만 한다.
- 검증 명령은 이미 front matter로 선언된다: `resolve_test_commands` (`:280`),
  `cfg.test_commands` (`:1364`), 권한 규칙 생성은 `command_tool_rules` (`:407`) /
  `allowed_tools_for` (`:1377`). **엔진이 그대로 실행할 수 있는 argv가 이미 있다**
  (`split_command` `:397`, `run_setup`가 같은 방식으로 이미 subprocess를 돌린다 `:1739`).
- front matter 파서는 알 수 없는 키를 **조용히 담아둔다** (`parse_front_matter` `:193`),
  `_MULTI_FIELDS` (`:190`)만 누적. `max_turns` 문법 파서 `parse_turn_tokens` (`:341`)는
  `120` / `implement=140` 양쪽을 읽는다 — `model:`도 같은 파서를 쓸 수 있다.
- 승인 파일은 `approvals/<stage>.md` 한 장이고 첫 유효 줄이 판정
  (`read_decision` `:555`, `APPROVAL_TEMPLATE` `:536`). `rejected: <사유>`는
  `Decision.reason`으로 이미 state에 남는다 (`await_approval` `:1993`).
- 커밋 라벨은 자유 문자열이다: `commit_stage(label=...)` (`:1891`), amend가
  `amend1/implement`로 쓴다 (`AMEND_LABEL` `:123`). 롤백은 라벨 순서가 아니라
  **도달 가능성**으로 판단하므로 (`rollback_slice` `:3345`) 새 라벨을 더해도 안전하다.
- state는 optional 키를 계속 더해왔고 `STATE_SCHEMA`는 2 그대로다 (`:100-102`,
  README 611-616행). 이번에 더하는 키도 전부 optional → **schema는 2 유지**.
- 빈 requirement 방어는 `start_slice` (`:2694-2696`)와 `launch_slice` (`:2756-2757`)에만
  있다. **`continue_slice` (`:2932`)와 `amend_slice` (`:2994`)는 `requirement.md`를
  읽어 그대로 `_finish`에 넘긴다 — 검사가 없다.** 오늘의 함정은 여기다.
- 테스트는 항상 `main(argv(...))`로 CLI를 통과한다 (`tests/test_pipeline.py:99`).
  Namespace를 직접 만드는 테스트가 없으므로 새 인자는 기존 테스트를 깨지 않는다.
- `runner.execute`는 `subprocess.Popen(...)`에 **env를 넘기지 않는다**
  (`aidev/runner.py:157`) — 환경변수를 자식에 심으려면 여기에 인자를 더해야 한다.

---

## 1. 먼저 확정하는 결정 (구현은 이 결정을 따른다)

| # | 결정 | 이유 |
| --- | --- | --- |
| D1 | **단계 이름은 `test` 그대로 둔다.** "verify"는 그 단계를 실행하는 *엔진*의 이름이고 모듈명(`aidev/verify.py`)·state 키(`stages.test.verify`)로만 쓴다 | 기존 state의 `stage_order`, `approvals/test.md`, `commits["test"]`, `--to test`, `AMEND_STAGES`, `telemetry.PHASES`가 전부 이 문자열에 걸려 있다. 이름을 바꾸면 하위 호환이 통째로 깨지는데 얻는 것은 라벨뿐이다 |
| D2 | **`test_commands`가 선언돼 있으면 엔진 실행(세션 0), 없으면 기존 에이전트 test 단계로 폴백** | 이 slice의 requirement 자신이 `test_commands`를 선언하지 않았고, 기존 테스트 30여 개가 에이전트 test 단계를 지난다. 폴백이 없으면 "명령을 추측"하는 코드를 새로 써야 한다 — 이 저장소가 계속 거부해온 짓이다 |
| D3 | **재시도(=repair) 루프는 엔진 실행 경로에만 있다** | failure.md를 만들 수 있는 쪽만이 재시도의 입력을 갖는다. 에이전트 폴백 경로는 v0.4 동작 그대로 (FAIL → 즉시 slice 실패) |
| D4 | **§3 출력 다이어트는 훅이 아니라 래퍼로 한다** | PostToolUse 훅은 이미 반환된 Bash stdout을 *줄일 수 없다*(컨텍스트를 더할 수만 있다). 세션이 보는 문자열을 줄이려면 세션이 실행하는 명령 자체가 요약본만 찍어야 한다 → `aidev verify` 래퍼 하나만 허용 |
| D5 | **§5의 "세션에 신호"는 포기하고 엔진 산출로 대체한다** (요구사항이 허용한 대안) | headless `claude -p`에는 실행 중인 세션에 말을 거는 채널이 없다. 대신 엔진이 이벤트 스트림에서 턴을 세다가 90%에서, 그리고 단계가 미완으로 끝날 때마다 `progress.md`를 **직접 쓴다**. 재시동 프롬프트에 주입하는 쪽은 요구대로 한다 |
| D6 | **plan 재실행은 새 명령 `--replan <slice>`** | 지금은 반려된 slice를 resume하면 승인 파일을 다시 읽고 그대로 멈춘다(`tests/test_pipeline.py:347`) — 그건 보호 장치이므로 유지한다. 재계획은 사람이 명시적으로 시키는 별개 동작이고, 모양은 `--amend`를 그대로 따른다 |
| D7 | **명세 기계검사는 파이썬 파일만, 테스트 파일 제외, 중첩 함수·dunder 제외** | 테스트 함수는 픽스처가 전부 파라미터라 `@param`을 요구하면 소음만 남는다. 자연 축적 원칙은 "이 slice의 diff에 걸린 함수만"으로 지킨다 |
| D8 | 프롬프트에 **새로 더하는 "지시"는 1개**(명세 규약)뿐. repair / replan / progress 블록은 기존 `_AMEND_NOTE`와 같은 **맥락 주입**이며 지시 카운트에 넣지 않는다 | 요구사항의 "지시 3개 이하" 준수 |

---

## 2. `aidev/verify.py` — 새 모듈 (엔진 검증, pipeline을 import하지 않는다)

순수 계층으로 만든다: 명령을 받아 돌리고, 출력을 구조로 바꾸고, 문서를 렌더한다.
pipeline을 import하지 않으므로 순환이 없다 (pipeline이 이 모듈을 top-level import).

```python
VERIFY_TIMEOUT_S = 1800.0
MAX_FAILURES_LISTED = 20
TRACEBACK_LINES = 15

@dataclass
class FailedTest:      # 하나의 실패 항목
    name: str          # nodeid 또는 테스트 이름
    file: str          # "tests/test_x.py"  (없으면 "")
    line: Optional[int]
    message: str       # assert 메시지 한 줄 (클립)

@dataclass
class CommandResult:
    command: str
    exit_code: Optional[int]   # None = 실행 자체가 불가(없는 실행파일/타임아웃)
    duration_s: float
    output: str                # 전문 (파일로 저장된다)
    log_path: Optional[str]

@dataclass
class VerifyResult:
    ok: bool
    commands: List[CommandResult]
    failures: List[FailedTest]
    passed: int                # 성공 "개수만"
    failed: int
    skipped: int
    parsed: bool               # 출력에서 실패 목록을 실제로 뽑았는가
    spec_violations: List[Any] = ()   # specs.Violation, §7이 채운다
```

함수:

1. `run_commands(commands, cwd, log_dir=None, timeout=VERIFY_TIMEOUT_S, env=None) -> VerifyResult`
   - `split_command`와 같은 규칙이 필요하므로 **`shlex` 분해는 여기서 다시 하지 않고**
     `pipeline.split_command`를 쓰지 않기 위해 동일 로직을 `verify.split_command`로
     옮긴다 → **`pipeline.split_command`는 `verify.split_command`를 재수출**한다
     (`pipeline.split_command = verify.split_command` 식이 아니라, pipeline에서
     `from .verify import split_command`로 import하고 기존 이름을 유지한다.
     기존 테스트가 `pipeline.split_command`를 부르지는 않지만 `command_tool_rules`가 쓴다).
   - 명령 하나씩 순서대로 실행. `run_setup`(`:1756`)의 subprocess 관용을 그대로 복제:
     `shutil.which`로 실행파일 확인 → 없으면 `exit_code=None` + 사유를 output에,
     `stdout=PIPE, stderr=STDOUT, encoding="utf-8", errors="replace"`, timeout 처리.
   - **첫 실패에서 멈춘다** (뒤 명령은 실행하지 않고 `commands`에 미실행으로 남기지 않음).
     lint→test 순서로 선언했을 때 lint 실패에 테스트 전량을 또 돌리는 낭비를 막는다.
   - `log_dir`이 있으면 명령마다 `<log_dir>/<attempt>-<i>-<slug>.log`에 전문을 쓰고
     `log_path`를 채운다 (`pipeline.write_text_atomic` 대신 `verify` 내부의 동일 헬퍼).
   - `ok = 모든 exit_code == 0`.
2. `parse_output(text) -> (List[FailedTest], counts, parsed)`
   - pytest: `^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$` (short summary),
     위치 줄 `^(\S+\.py):(\d+):\s*(.+)$`를 파일별로 첫 매치만 붙인다.
     nodeid `tests/test_x.py::test_y`에서 file을 분리.
   - 카운트: `(\d+) (passed|failed|error|skipped)` 전부 수집.
   - jest/vitest 최소 대응: `^\s*(FAIL)\s+(\S+)`와 `✕ (.+)` — best effort라고 docstring에 명시.
   - 아무것도 못 뽑으면 `parsed=False`, `failures=[]`.
3. `render_failure_md(...) -> str` — §1의 규격 그대로:

```markdown
# FAILURE — <slice-id> · verify attempt <n>
<iso timestamp>

## Command
    <command>            exit <code>   <duration>s

## Failed tests (<failed>)
- tests/test_x.py:42 — test_night_doctor — AssertionError: expected 3, got 2
- ... (+N more; 전문은 아래 로그)

## Spec violations (<n>)              ← 있을 때만 (§7)
- aidev/pipeline.py:812 — run_stage — @param cfg 없음

## Counts
passed 143   failed 3   skipped 1     ← 성공은 개수만

## Traceback (요약)
    <최대 15줄>

full log: <경로>
last commit: <sha7> slice(<id>): implement
```

   - 실패 목록을 못 뽑은 경우(`parsed=False`)에는 `## Failed tests` 대신
     `## Output (tail 60 lines)`를 넣고 그 사실을 한 줄로 적는다.
4. `summarize_for_agent(result) -> str` — §3 다이어트 출력. 실패분만 `file:line — message`,
   성공은 `passed N`, 마지막 줄에 `full log: <path>`. 상한 `MAX_FAILURES_LISTED`.
5. `worst_exit_code(result) -> int` — 래퍼 CLI의 종료 코드.

---

## 3. `aidev/specs.py` — 새 모듈 (§7 함수 명세 기계검사)

pipeline을 import하지 않는다. git 호출은 `aidev.workspace`의 프리미티브만 쓴다.

```python
SPEC_PARAM_RE = re.compile(r"^\s*@param\s+(\*{0,2}\w+)", re.MULTILINE)

@dataclass
class Violation:
    file: str; line: int; func: str; kind: str; detail: str
    # kind: "missing" | "param" | "unchanged"
```

- `changed_python_files(repo, base, head) -> Dict[str, Set[int]]`
  - `git diff --unified=0 <base> <head> -- "*.py"`를 파싱해 **파일별 추가/수정된 새 줄 번호**를
    모은다. workspace에 헬퍼 두 개를 추가한다:
    `workspace.diff_unified(repo, a, b, paths=("*.py",)) -> str`,
    `workspace.show_file(repo, rev, path) -> Optional[str]` (`git show <rev>:<path>`, 실패 시 None).
  - 제외: 테스트 파일(`pipeline._looks_like_test`와 **같은 규칙**을 여기 복제하지 말고
    `specs.looks_like_test`로 옮기고 pipeline이 그것을 쓰게 한다), `.aidev/` 하위, 삭제된 파일.
- `functions(text) -> List[FuncSpec]` — `ast.parse`. 각 항목:
  이름(클래스 접두 포함한 qualname), `lineno`(데코레이터 제외한 `def` 줄),
  `end_lineno`, 파라미터 목록(`self`/`cls` 제외), 그리고 **명세 텍스트**:
  1순위 `ast.get_docstring`, 없으면 `def` 줄 바로 위에 붙은 연속 `#` 주석 블록.
  중첩 함수(부모가 Function인 것)와 dunder(`__x__`)는 제외한다.
  `SyntaxError`면 그 파일은 통째로 건너뛴다(검사기가 FAIL의 원인이 되면 안 된다).
- `check(repo, base, head) -> List[Violation]`
  - 변경 줄이 `[lineno, end_lineno]`에 하나라도 걸리면 그 함수는 "diff 존재".
  - `missing`: 명세가 없거나 첫 비어있지 않은 줄이 없음.
  - `param`: 파라미터가 있는데 `@param <이름>`이 빠짐 (가변인자는 `*args`/`**kwargs` 이름으로).
  - `unchanged`: base 리비전에 같은 qualname의 함수가 있고 그 명세 텍스트가
    **정확히 같음** (공백 정규화 후 비교). 신규 함수에는 적용하지 않는다.
- `python -m aidev.specs [--repo .] [--base <rev>] [--head HEAD]` CLI
  (`if __name__ == "__main__"`): 위반을 `file:line — func — kind: detail`로 찍고
  위반이 있으면 exit 1. base 미지정이면 `AIDEV_SPEC_BASE` 환경변수 → 없으면
  "base를 알 수 없다"고 말하고 exit 2. **implement 단계가 스스로 돌려볼 수 있는
  피드백 채널**이며, 그래서 implement에 `Bash(python -m aidev.specs)` /
  `Bash(python -m aidev.specs:*)` 규칙을 (spec_check가 켜졌을 때만) 함께 허용한다.

---

## 4. `aidev/progress.py` — 새 모듈 (§5)

- `render(state, stage, attempt, telemetry_snapshot_or_dict, plan_text, last_text, reason, repo, slice_id) -> str`
  - `## Done`: telemetry의 `observed.file_accesses` 중 `operation in ("edit","write")` 경로 +
    `observed.calls` 중 category `test`/`exec`의 target(중복 제거, 상위 15개).
  - `## Remaining`: `pipeline.plan_scale`이 이미 쓰는 `_PLAN_PATH_RE`로 plan.md에서 뽑은
    경로 중 편집되지 않은 것. (정규식은 `pipeline`에서 import — 중복 정의 금지)
  - `## Last words`: 세션의 마지막 어시스턴트 텍스트(클립 1200자).
  - `## Resume`: `aidev pipeline --repo <repo> --resume-slice <id>`.
  - 헤더에 `turn <t>/<budget>`과 왜 쓰였는지(`turn-budget 90%` / `stage ended incomplete`).
- `should_snapshot(turn, budget, already) -> bool` — `turn >= ceil(budget*0.9)`이고 아직 안 썼으면 True.
  임계값 상수 `PROGRESS_TURN_RATIO = 0.9`.

---

## 5. `aidev/hooks/write_guard.py` — 새 파일 (§4, 기계 강제)

`aidev/hooks/__init__.py`(빈 파일)와 함께 추가. **자기 완결 스크립트**로 쓴다
(aidev를 import하지 않는다 — 대상 repo의 파이썬에서 경로로 직접 실행되기 때문).

- stdin의 JSON을 읽는다: `{"tool_name": ..., "tool_input": {"file_path": ...}, "cwd": ...}`.
- `AIDEV_WRITE_GUARD=off`면 즉시 exit 0.
- `tool_name != "Write"` → exit 0.
- `file_path`를 `cwd` 기준으로 절대화. **존재하면** stderr에 아래를 쓰고 **exit 2**
  (PreToolUse에서 exit 2 = 호출 차단 + stderr가 모델에게 전달):

  ```
  Write is blocked for a file that already exists:
      <path>
  Use Edit (or MultiEdit) to change it - that sends only the changed lines.
  Write is allowed only for creating a new file.
  ```
- 존재하지 않으면 exit 0 (신규 생성 허용).
- **예외는 전부 삼키고 exit 0** (fail open). 훅 스키마가 바뀌어도 파이프라인이
  벽돌이 되면 안 된다 — 이 원칙을 docstring에 못박는다.

파이프라인 쪽 배선(§8-G): `<slice dir>/hooks/settings.json`을 쓰고
`--settings <path>`를 implement/test 단계의 argv에 붙인다. **대상 repo의
`.claude/settings.json`은 건드리지 않는다** (README 711행의 규약 유지).

---

## 6. `aidev/runner.py` — 두 군데만

1. `RunConfig`에 `env: Dict[str, str] = field(default_factory=dict)` 추가,
   `to_dict()`에 `"env": sorted(self.env)` (**값은 넣지 않는다** — run.json에 값이
   새는 것을 막는다. 키 이름만 기록).
2. `execute`의 `subprocess.Popen(...)`에
   `env=({**os.environ, **cfg.env} if cfg.env else None)` 추가.
3. `RunConfig.settings_path: Optional[str] = None` + `build_command`에
   `if cfg.settings_path: cmd += ["--settings", cfg.settings_path]`
   (`--allowedTools` 뒤, `--resume` 앞). `to_dict()`에도 포함.

---

## 7. `aidev/cli.py` — `aidev verify` 서브커맨드 (§3 래퍼의 실체)

```
aidev verify [--command CMD]... [--cwd DIR]
```
- 인자가 없으면 `AIDEV_VERIFY_COMMANDS`(JSON 배열) / `AIDEV_VERIFY_CWD` /
  `AIDEV_VERIFY_LOG_DIR`을 읽는다. 아무것도 없으면 exit 2 + "이 명령은 파이프라인의
  implement 단계 안에서 쓰라"는 안내.
- `verify.run_commands(...)` 실행 → `print(verify.summarize_for_agent(result))` →
  `return verify.worst_exit_code(result)`.
- 즉 세션이 보는 것은 **요약본뿐**이고 전문은 로그 파일로만 남는다.

---

## 8. `aidev/pipeline.py` — 본체

### A. front matter 확장

- `resolve_models(fields, stages=MODEL_STAGES) -> (Optional[str], Dict[str,str])`
  (`resolve_max_turns` `:317` 바로 아래). `model:` 값을 **`parse_turn_tokens`와 같은
  문법**으로 읽되 값이 숫자가 아니므로 전용 파서 `parse_model_tokens`를 옆에 둔다
  (`<stage>=<model>` / 베어 값 = base, 콤마·공백 구분, 알 수 없는 stage는 에러,
  같은 stage 두 번 다른 값이면 에러 — `parse_turn_tokens`의 에러 문구를 그대로 따른다).
- `MODEL_STAGES = STAGES + ("diagnose", "decompose")`,
  `TURN_STAGES = STAGES + ("diagnose",)`.
  `resolve_max_turns` / `_config`의 `parse_turn_tokens` 기본 stages를 `TURN_STAGES`로
  바꾼다 (기존 에러 테스트 `planz` 는 그대로 unknown).
- `resolve_spec_check(fields) -> bool` — `spec_check: off|false|no|0` → False,
  없으면 True, 그 외 값은 에러.
- `epic.py:229-233`의 "front matter accepts exactly four keys" 문장을
  `model:` / `spec_check:`까지 여섯 개로 갱신.

### B. `PipelineConfig` (`:1342`)

추가 필드: `stage_models: Dict[str,str]`, `spec_check: bool = True`,
`verify_engine: bool = True`, `max_repairs: int = DEFAULT_MAX_REPAIRS`,
`diagnose_threshold: int = DEFAULT_DIAGNOSE_THRESHOLD`,
`verify_timeout: float = VERIFY_TIMEOUT_S`, `write_guard: bool = True`,
`output_diet: bool = True`.
`model_for(stage)` 추가 (`turns_for` 옆): `stage_models.get(stage) or self.model`.
`turns_for`는 `stage_max_turns.get(stage) or STAGE_TURN_DEFAULTS.get(stage) or max_turns`로
바꾼다 (`STAGE_TURN_DEFAULTS = {"diagnose": 20}` — 진단은 짧아야 싸다).
상수: `DEFAULT_MAX_REPAIRS = 1`, `DEFAULT_DIAGNOSE_THRESHOLD = 3`.

`execute_stage` (`:1520`)의 `RunConfig(model=cfg.model)`을 `cfg.model_for(spec.stage)`로,
`env=`/`settings_path=`를 spec에서 받도록 `StageSpec`에 두 필드를 더한다
(`env: Dict[str,str] = field(default_factory=dict)`, `settings: Optional[str] = None`).
`StageSpec.for_slice`가 stage에 따라 채운다.

### C. 검증 엔진 진입 (§1)

`run_pipeline`(`:2082`)의 stage 루프 안, `if entry.get("status") != "done":` 블록에서
`stage == "test" and cfg.verify_engine and cfg.test_commands` 이면 `run_stage` 대신
**`run_verify(cfg, rec, state, requirement, amend)`** 를 부른다. 반환은
`Optional[str]`(실패 사유) — None이면 통과. 통과 시 `entry.update({"status":"done"})`
→ 기존과 같이 `commit_stage`.

`run_verify`의 절차 (신설 섹션 `# ------- verify engine`):

```
attempt = 0
while True:
    attempt += 1
    result = verify.run_commands(cfg.test_commands, cfg.cwd,
                                 log_dir=rec.verify_dir, timeout=cfg.verify_timeout)
    result.spec_violations = run_spec_check(cfg, rec, state)      # §7, 명령 성패와 무관하게 항상
    record_verify(rec, state, attempt, result)                     # state에 기록, 세션 0
    if result.ok and not result.spec_violations:
        state["test_verdict"] = "pass"; return None
    write failure.md  (verify.render_failure_md)
    if attempt > cfg.max_repairs:  → state["test_verdict"]="fail"; note_stage_failure; return 사유
    grade = "small" if 0 < 실패개수 <= cfg.diagnose_threshold and result.parsed else "large"
    diagnosis = run_diagnosis(...) if grade == "large" else None   # 세션 1개
    reopen implement:  stage_entry("implement") status=pending, session_id 제거
    run = run_stage(cfg, rec, state, "implement", requirement, amend=amend,
                    repair={"n": attempt, "failure": <failure.md 전문>,
                            "diagnosis": <diagnosis.md 전문 or "">})
    if not run.ok: → 실패 사유 반환 (failure.md/diagnosis.md가 사람 보고 자료)
    stage_entry("implement")["status"] = "done"
    commit_stage(cfg, rec, state, "implement", label=REPAIR_LABEL.format(attempt, "implement"))
```

- `REPAIR_LABEL = "repair{0}/{1}"` (AMEND_LABEL과 같은 모양, `:123` 옆에).
- `record_verify`: `stage_entry(state,"test")["verify"] = {"engine": True,
  "attempts": [{"n":..,"ok":..,"commands":[{command,exit_code,duration_s,log}],
  "failed":..,"passed":..,"spec_violations":..,"failure_md":<경로>}]}`.
  `state["test_verdict"]`도 함께 쓴다 (`pass`/`fail`) — 기존 리더가 그대로 읽는다.
- `state["repairs"] = [{n, grade, diagnosis_run_id, at, failed, ...}]` (optional 키).

`run_diagnosis` — 세션 하나, `StageSpec(stage="diagnose", policy={"safety_profile":"readonly",
"permission_mode":None}, phase="repair", prompt=_DIAGNOSE_PROMPT.format(...))`.
`run_stage`로 돌리고(쿼터/세션 정책을 공짜로 얻는다) 결과 텍스트를
`rec.diagnosis_path`에 저장. **readonly**라 코드를 고치거나 테스트를 다시 돌릴 수 없다 —
진단만 하고 값싸게 끝나는 것이 목적이다. `phase="repair"`는 이미 있는
telemetry PHASE라 `aidev stats`가 그대로 집계한다.

프롬프트 `_DIAGNOSE_PROMPT` (산출물 규격을 강제):

```
You are the DIAGNOSE step ... Nobody is watching.
FAILURE REPORT / REQUIREMENT / PLAN  블록
INSTRUCTIONS
- Read the failing code and the failing test. Do not run anything (tools are blocked).
- Your final message is saved verbatim as diagnosis.md. Emit exactly:
# DIAGNOSIS
## Cause
<one line>
## Fix direction
<2-5 lines>
## Where
- <path>:<line> — <why>
```

### D. §7 명세 검사 배선

`run_spec_check(cfg, rec, state) -> List[specs.Violation]`:
- `cfg.spec_check`가 꺼졌으면 `[]`.
- base: `state["commits"].get(REQUIREMENT_STAGE)` → 없으면 `state["workspace"]["base_commit"]`
  → 둘 다 없으면(`--no-worktree`/레거시) **건너뛰고 한 줄 알린다**
  ("no commit range - spec check skipped").
- head: `workspace.head_commit(cfg.cwd)`.
- `specs.check(cfg.cwd, base, head)`.
- **에이전트 폴백 경로에서도 돈다**: `finish_stage`(`:2029`)의 test 분기에서
  verdict가 pass여도 위반이 있으면 실패 사유를 반환한다(이때 failure.md도 쓴다 —
  `verify.render_failure_md`에 명령 결과 없이 위반만 담는 경로를 허용).

### E. §3 출력 다이어트 (래퍼 배선)

- `verify_wrapper_command() -> Optional[str]`: `shutil.which("aidev")`가 있으면
  `"aidev verify"`, 없으면 None.
- `allowed_tools_for` (`:1377`) 수정: stage가 `implement`이고 `cfg.output_diet`이며
  `cfg.test_commands`가 있고 래퍼가 존재하면 → **declared를 원본 명령 규칙 대신
  `("Bash(aidev verify)", "Bash(aidev verify:*)")` 로 바꾼다.** setup 규칙과
  `--allow-tool`은 지금 규칙 그대로 유지. `test` 단계(에이전트 폴백)는 지금 그대로
  원본 명령을 받는다 — 폴백 경로가 달라지면 안 된다.
- 래퍼가 없으면(=`aidev`가 PATH에 없음) 다이어트를 끄고 원래 규칙을 주면서
  `say("note: 'aidev' is not on PATH - the implement stage gets the raw test commands")`.
- `StageSpec.for_slice("implement")`가 env를 채운다:
  `AIDEV_VERIFY_COMMANDS=json.dumps(cfg.test_commands)`, `AIDEV_VERIFY_CWD=str(cfg.cwd)`,
  `AIDEV_VERIFY_LOG_DIR=str(rec.verify_dir)` — 그래서 `for_slice`에 `rec` 인자를 더한다
  (호출부는 `execute_stage`/`run_stage` 두 곳뿐).
- 프롬프트는 손대지 않는다: `_ALLOWED_NOTE`(`:1237`)가 이미 **허용된 규칙 목록을
  그대로** 렌더하므로, 허용 목록이 바뀌면 모델이 듣는 말도 자동으로 바뀐다.
  `_TEST_COMMANDS_NOTE`(`:1245`)만 다이어트일 때 "이 프로젝트의 검증은
  `aidev verify` 한 명령으로 돈다(요약만 돌아온다)"로 문구를 바꾼다.

### F. §5 progress.md

- `SliceRecord`에 `progress_path` / `failure_path` / `diagnosis_path` / `verify_dir` /
  `plans_dir` 프로퍼티 추가 (`:595` 블록).
- `execute_stage`의 `on_event`(`:1559`)에서:
  `if progress.should_snapshot(tel.turn, budget, wrote_progress): write progress.md`.
  budget은 `cfg.turns_for(spec.stage)`.
- `run_stage`가 돌아온 뒤 run이 `ok`가 아니면(실패/중단) 한 번 더 쓴다(최신 상태로 덮어씀).
- state에 `state["progress"] = {"stage":..., "attempt":..., "at":..., "turn":..., "reason":...}`.
- 주입: `_RESUME_NOTE`(`:1219`)와 `_FRESH_SESSION_NOTE`(`:1226`) 뒤에
  `_PROGRESS_NOTE.format(progress=<본문>)`을 붙인다 — 단, `state["progress"]["stage"]`가
  지금 돌리는 stage와 같고 그 stage가 done이 아닐 때만. **주입 뒤에는 stale 방지를 위해
  파일을 지우지 않는다**(사람 보고 자료로 남는다) — 대신 stage가 done이 되는 순간
  `state["progress"]`를 지운다.

### G. §4 Write 차단 배선

- `write_hook_settings(cfg, rec) -> Optional[Path]`: `cfg.write_guard`가 켜져 있고
  `aidev/hooks/write_guard.py`가 존재하면 `<slice dir>/hooks/settings.json`을
  `write_json_atomic`으로 쓰고 경로를 돌려준다.

  ```json
  {"hooks": {"PreToolUse": [{"matcher": "Write",
    "hooks": [{"type": "command", "command": "\"<sys.executable>\" \"<abs guard path>\"", "timeout": 10}]}]}}
  ```
- `StageSpec.for_slice`가 implement/test에 `settings=<경로>`를 채우고,
  `execute_stage`가 `RunConfig(settings_path=...)`로 넘긴다 → `--settings`.
- plan/decompose에는 붙이지 않는다 (readonly라 Write 자체가 이미 막혀 있다).

### H. §6 반려 문서 + `--replan`

- `REJECT_DETAIL_NAMES = ("{stage}-rejected-detail.md", "rejected-detail.md")` — approvals/ 안에서
  이 순서로 찾는다. 없으면 없는 대로 (하위 호환: `rejected: <사유>` 한 줄이 여전히 전부).
- `read_rejection(rec, stage, decision) -> Dict`:
  `{"reason": decision.reason, "detail": <전문 or "">, "grade": "partial"|"full", "path": ...}`.
  grade는 본문에서 `전면` / `full` 키워드가 잡히면 `full`, 그 외 **기본 `partial`**.
- `await_approval`(`:1954`)에서 REJECTED일 때 이것을 읽어
  `state["rejections"].append({...})`(append-only, optional 키)에 남기고,
  `APPROVAL_TEMPLATE`의 주석에 "선택: `rejected-detail.md`에 대상 좌표/문제/요구/범위를
  적으면 재계획에 그대로 주입된다" 한 줄을 추가한다.
- **`--replan <slice>`** (`add_parser` `:2384`, `_dispatch`의 modes 목록 `:2561`에 추가):
  - 대상 slice의 state를 읽고 plan 단계를 재개방:
    `plan.md` → `plans/001.md`로 보존, `approvals/plan.md` → `approvals/plan-001.md`로 보존
    후 승인 파일 삭제(다음 게이트가 템플릿을 새로 쓴다),
    `stage_entry("plan")` status=pending + `session_id`/`approval`/`approval_reason` 제거,
    `state["replans"].append({"n":.., "at":.., "previous_plan": "plans/001.md",
    "rejection": {...}})`, 그리고 implement/test도 pending으로 되돌린다
    (plan이 바뀌면 그 아래는 전부 다시다).
  - `--dry-run`이면 무엇을 되돌릴지만 찍고 종료(다른 명령들과 같은 관용).
  - 그 다음은 `_finish`로 기존 루프에 그대로 들어간다.
- plan 프롬프트에 `_REPLAN_NOTE` 주입 (`build_prompt`의 plan 분기):

  ```
  PREVIOUS PLAN (rejected)
  ------------------------
  <직전 plan 전문>

  REJECTION
  ---------
  reason: <한 줄>
  grade:  partial|full
  <rejected-detail.md 전문>

  Change ONLY what the rejection names; keep everything else as it is. This is a
  diff, not a rewrite.   ← 전면 반려면 이 줄 대신 "Re-plan from scratch."
  ```

### I. §9 빈 requirement 가드

- `guard_requirement(text, where) -> str`: 파일 전문을 받아 front matter를 떼고
  본문을 돌려주되, ① 전문이 공백 → "requirement file is empty: {where}",
  ② 본문이 공백 → "requirement has front matter but no body: {where}",
  ③ 본문의 공백 제외 길이 < `MIN_REQUIREMENT_CHARS`(20) → "requirement body is
  effectively empty ({n} chars): {where}" — 전부 `PipelineError`(exit 2).
- 호출 지점: `start_slice`(`:2694`), `launch_slice`(`:2755`) — 기존 검사를 이것으로 교체.
  **`continue_slice`(`:2932`)와 `amend_slice`(`:2994`)에 새로 추가** (오늘의 함정).
  마지막 안전핀으로 `run_pipeline` 첫 줄에서도 body를 검사한다 — 발사 시점 하나로
  모든 진입점을 덮는다.

### J. 요약 출력 / 미러 / 옵션

- `render_summary`(`:2214`): stage 루프를 `stage_order(state) + [state["stages"]에만 있는 키]`로
  넓혀 `diagnose` 행이 보이게 한다. attempts가 없고 `entry["verify"]`가 있으면
  `row(stage, status, len(attempts_of_verify), "-", "-", "-", "$0.0000")` + 아래에
  `Verify    <command>  exit 0  (143 passed)` 한 줄, 실패면 `failure.md` 경로,
  repair가 있었으면 `Repairs   1  (diagnosis: yes/no)`.
- `mirror_history`(`:1841`): `failure.md` / `diagnosis.md` / `progress.md`가 있으면
  worktree의 history 디렉터리에 함께 투영 (브랜치 커밋이 사람 보고 자료를 싣는다).
  `history_snapshot`(`:1798`)에 `repairs` / `replans` / `rejections` 키 추가.
- 새 CLI 플래그 (`add_parser`): `--max-repairs`(기본 1), `--diagnose-threshold`(기본 3),
  `--verify-timeout`(기본 1800), `--no-verify-engine`(엔진 검증 끄고 v0.4 에이전트 test로),
  `--no-write-guard`, `--no-output-diet`, `--replan`.
  `_config`(`:2625`)가 전부 읽어 `PipelineConfig`에 넣는다.
- `--dry-run` 출력(`:2711`)에 `verify    engine: <commands>` / `agent (no test_commands declared)`,
  `models    plan=..  implement=..`, `guards    write-guard on, spec-check on` 세 줄 추가.

---

## 9. 함수 명세 규약 — **구현하면서 반드시 지킬 형식**

이 slice의 verify가 자기 자신의 diff를 검사한다. 새로 만들거나 **수정한** 함수는
(테스트 파일 제외) 아래 형식을 갖춰야 하고, 이미 있던 함수를 고쳤다면 명세도
**한 글자라도 달라져야** 한다.

```python
def run_verify(cfg, rec, state, requirement, amend=None):
    """검증을 엔진이 직접 돌리고, 실패하면 등급에 따라 한 바퀴 고쳐 온다.

    @param cfg          이 slice의 PipelineConfig (test_commands / max_repairs를 읽는다)
    @param rec          SliceRecord - failure.md / verify 로그가 쓰이는 곳
    @param state        state.json 사전. verdict와 repairs 기록을 여기에 남긴다
    @param requirement  implement를 다시 부를 때 넘길 요구사항 본문
    @param amend        열려 있는 amend 사이클 (없으면 None)
    @flow  run_commands -> spec check -> (fail) failure.md -> 등급 -> diagnose? -> implement -> 반복
    주요 내부 변수: attempt(1부터), result(VerifyResult), grade(small|large)
    """
```

- 첫 줄 = 기능 한 줄 (필수)
- `@param <이름>` = 파라미터마다 한 줄 (self/cls 제외, 필수)
- `@flow` = 분기/루프가 있을 때만 (검사기는 요구하지 않는다)
- "주요 내부 변수" = 임의 (검사기는 요구하지 않는다)
- 기존 코드에 소급하지 않는다. **건드리지 않은 함수는 그대로 둔다.**

---

## 10. 테스트 계획

### 새 파일

**`tests/test_verify.py`**
- `test_pass_path_spawns_no_session`: `test_commands`에 파이썬 스크립트(exit 0)를
  선언하고 slice 완주 → `invocations(log)`가 **2개**(plan, implement)뿐,
  `state["stages"]["test"]["verify"]["engine"] is True`, `test_verdict == "pass"`,
  `runs.json`에 test stage 항목 없음. ← Done Criteria "PASS 경로에 세션 0개 증명"
- `test_fail_writes_failure_md_and_retries_small`: 첫 호출은 pytest 스타일 실패
  2건을 찍고 exit 1, 두 번째부터 exit 0인 스크립트(호출 카운터 파일 사용).
  → `failure.md` 존재 + `Failed tests (2)` + `file:line — message` 형식,
  진단 세션 **없음**(invocation 수로 증명), implement 재실행 1회,
  커밋 라벨 `slice(<id>): repair1/implement` 존재, 최종 done.
- `test_large_failure_summons_a_diagnosis_session`: 실패 6건 → `diagnosis.md` 생성 +
  진단 invocation의 프롬프트에 failure.md 본문이 들어있고 `--disallowedTools`에 Bash 포함,
  재시도 implement 프롬프트에 diagnosis.md 본문이 주입됨.
- `test_repair_limit_leaves_the_reports_for_a_human`: 계속 실패 → exit 1,
  status failed, `failure.md`와 `diagnosis.md`가 남아 있고 state의 `repairs` 길이 == 1.
- `test_parse_output`(순수): pytest 요약/위치 줄 파싱, 카운트, 파싱 실패 시 `parsed=False`.
- `test_summarize_for_agent_is_a_diet`: 성공 200줄 + 실패 2건 출력 → 요약에
  성공 목록이 없고 개수만, 실패 2건은 `file:line`과 함께 있고 전체 길이가 원문의 1/5 미만.
- `test_first_failing_command_stops_the_rest`.

**`tests/test_specs.py`**
- `test_new_function_without_spec_fails`, `test_missing_param_line_fails`,
  `test_unchanged_spec_on_a_modified_function_fails`,
  `test_untouched_function_is_never_checked`(소급 금지),
  `test_test_files_are_exempt`, `test_syntax_error_file_is_skipped`.
  전부 임시 git repo에 두 커밋을 만들어 `specs.check(repo, base, head)` 직접 호출.
- `test_verify_fails_when_a_modified_function_has_no_spec`: 파이프라인 통합 —
  implement 단계에서 스텁이 명세 없는 함수를 파일에 쓰게 하고(`AIDEV_FAKE_SPECLESS=1`
  모드 추가) verify가 FAIL, `failure.md`에 `Spec violations` 절이 있음.
  ← Done Criteria

**`tests/test_hooks.py`**
- guard 스크립트를 subprocess로 직접 호출: 기존 파일 → rc 2 + "Use Edit",
  신규 경로 → rc 0, `tool_name=Edit` → rc 0, 깨진 JSON → rc 0,
  `AIDEV_WRITE_GUARD=off` → rc 0.
- 파이프라인: implement argv에 `--settings <path>`가 있고 그 JSON의
  `hooks.PreToolUse[0].matcher == "Write"`, plan argv에는 없음.
  `--no-write-guard`면 아예 없음. ← Done Criteria

### `tests/test_pipeline.py` 수정 (기존 233개를 깨지 않게)

- **`test_declared_test_commands_replace_the_built_in_rules`(:1337)**: 지금은
  `npm run test:guards`를 선언하고 test 에이전트가 도는 것을 가정한다. 엔진 실행이
  되면 실제로 npm을 돌리려다 실패한다. → `--no-verify-engine`을 붙여 **폴백 경로의
  회귀 테스트로 명시 전환**하고, 다이어트 검증은 새 assert로 옮긴다:
  엔진 모드에서 implement의 허용 규칙이 `Bash(aidev verify)`만인 것
  (래퍼 유무는 `monkeypatch.setattr(pipeline, "verify_wrapper_command", lambda: "aidev verify")`).
- **`test_the_declared_command_is_what_the_test_stage_may_actually_run`(:1359)**:
  같은 이유로 `--no-verify-engine`을 붙인다(측정된 v0.4.1 결함의 회귀 테스트로 계속 산다).
- **`test_a_test_verdict_fail_counts_as_a_stage_failure`(:1460)** 등 나머지 test-stage
  테스트: `test_commands`를 선언하지 않으므로 폴백 경로 = **무수정 통과**.
- 새로 추가:
  - `test_an_empty_requirement_is_refused_at_launch` / `..._on_resume` /
    `..._on_amend`: 빈 파일, 본문 없는 front matter, 20자 미만 본문 3종 →
    exit 2 + 메시지, `invocations(log) == []`. ← Done Criteria
  - `test_progress_md_is_written_when_a_stage_ends_incomplete` +
    `test_resume_injects_progress_md`: 스텁을 `fail` 모드로 돌려 progress.md 생성 확인
    (`## Done`에 편집 파일, `## Resume`에 명령), resume 프롬프트에 본문이 들어있는지.
    ← Done Criteria
  - `test_replan_keeps_the_old_plan_and_injects_the_rejection`:
    반려(사유 + `rejected-detail.md`) → `--replan` → plan 프롬프트에 직전 plan 전문과
    반려 문서가 있고 "Change ONLY what the rejection names"가 있음,
    `plans/001.md`와 `approvals/plan-001.md`가 보존됨.
  - `test_a_plain_rejection_without_a_detail_file_still_works` (하위 호환).
  - `test_model_mix_reaches_the_command`: `model: implement=claude-opus-5` →
    implement argv의 `--model`만 그 값, plan은 없음(또는 `--model` 미지정).
  - `test_legacy_state_without_the_new_keys_resumes`: v0.2 모양 state.json(스키마 1,
    `commits` 없음)으로 resume → spec check가 "skipped"로 지나가고 완주. ← 하위 호환
  - `test_verify_summary_row_shows_the_engine_run`.

### 스텁 확장 `tests/fake_pipeline_claude.py`

- `AIDEV_FAKE_SPECLESS=1`: implement에서 명세 없는 함수를 `aidev_generated.py`에 쓴다.
- `AIDEV_FAKE_DIAGNOSIS`: diagnose 단계(프롬프트에 "DIAGNOSE"가 있으면)에 대한
  기본 텍스트 — `detect_stage`(:156)에 `"diagnose"`를 추가.
- `AIDEV_FAKE_EDIT_EXISTING`: Write 대신 Edit 이벤트를 찍는 모드(진단 불필요, 생략 가능).
- 새 헬퍼 `verify_script(tmp_path, body)` (테스트 쪽, `setup_script`(:1234)와 동형).

---

## 11. README 갱신

`## Slice Pipeline (v0.2)` 아래에 새 절 **`### 검증의 결정론화 (v0.5)`** 를 추가하고,
아래를 고친다:

- "테스트 판정"(623행): 엔진 실행이 기본이고 `TEST_RESULT:` 규약은
  **`test_commands`를 선언하지 않은 폴백 경로에만** 남는다는 것.
- "저장 위치"(571행)의 slice 디렉터리 그림에
  `failure.md` / `diagnosis.md` / `progress.md` / `verify/*.log` / `plans/` / `hooks/settings.json` 추가.
- state 키 문단(611행)에 `repairs` / `replans` / `rejections` / `progress` /
  `stages.test.verify`가 전부 optional이며 **schema는 2 그대로**라고 명시.
- 안전핀 절(658행)에 Write 차단(기존 파일 거부·신규 허용, fail open, `--no-write-guard`)과
  명세 기계검사(대상/제외/`spec_check: off`)를 추가.
- 옵션 표(727행)에 `--replan` / `--max-repairs` / `--diagnose-threshold` /
  `--verify-timeout` / `--no-verify-engine` / `--no-write-guard` / `--no-output-diet`.
- front matter 문서에 `model:` / `spec_check:` 추가 + 예시.
- 반려 문서 규격(`rejected-detail.md`: 대상 좌표 / 문제 / 요구 / 범위, 기본 부분 반려)과
  `--replan`의 차분 재계획 설명.

---

## 12. 구현 순서 (턴 예산 140 기준, 앞이 더 중요)

1. `verify.py` + `specs.py` + 그 단위 테스트 (pipeline을 건드리지 않아 독립적으로 녹색)
2. `runner.py`(env/settings) + `hooks/write_guard.py` + `test_hooks.py`
3. `pipeline.py` A·B(front matter/config) → C(run_verify/repair) → D(spec 배선)
4. `cli.py`의 `aidev verify` + E(다이어트 배선)
5. F(progress) + G(hook 배선)
6. H(`--replan`/반려 문서) + I(빈 requirement 가드)
7. J(요약/미러/플래그) + 기존 테스트 수정 + README

**턴이 90%에 닿으면** 이 slice가 만든 progress.md 규격 그대로
`.aidev/slices/<id>/progress.md`에 "한 것 / 남은 것"을 남기고 멈춘다 —
자기 자신이 첫 사용자다.

---

## 13. 무엇이 잘못될 수 있나

| 위험 | 대응 |
| --- | --- |
| **§7 명세 검사가 이 slice 자신을 FAIL시킨다.** pipeline.py에서 수정할 함수가 20개가 넘고 전부 `@param`이 필요하다 | 고치는 즉시 명세를 쓴다(§9). implement는 `python -m aidev.specs --base <requirement 커밋>`으로 스스로 확인할 수 있다(권한 규칙을 준다). 최후 수단으로 requirement front matter에 `spec_check: off` |
| `unchanged` 규칙이 한 줄만 고친 큰 함수에도 문서 수정을 요구한다 | 요구사항 그대로 구현하되, 위반 메시지에 "명세를 한 줄이라도 갱신하라"를 넣고 `spec_check: off` 탈출구를 README에 적는다 |
| **훅 스키마가 CLI 버전에 따라 다르다** (`--settings`, PreToolUse exit 2 규약) | guard는 무조건 fail open(예외/알 수 없는 입력 → exit 0). 훅이 무시돼도 파이프라인은 v0.4처럼 돌 뿐이다. 훅 자체는 단위 테스트로 증명하고, 파이프라인 테스트는 "argv에 `--settings`가 붙었고 파일 내용이 옳다"까지만 주장한다 (fake claude는 훅을 실행하지 않는다 — 이 한계를 테스트 docstring에 적는다) |
| `aidev`가 PATH에 없어 다이어트 래퍼를 못 준다 | 다이어트를 끄고 원본 명령을 주며 한 줄 알린다. 검증의 정확성은 영향 없음 |
| **repair 루프가 돈을 더 쓴다**(implement 세션 1개 + 진단 1개) | 기본 `--max-repairs 1`, 진단은 실패 3건 초과일 때만, 진단은 readonly + 기본 20턴. PASS 경로에서는 정확히 0개다 |
| `run_verify`가 `run_stage("implement")`를 다시 부르면서 stage 상태 기록이 꼬인다 | implement 엔트리는 pending→running→done을 그대로 지나가고, 커밋만 `repair1/implement` 라벨로 분리한다(amend가 이미 증명한 모양). 롤백은 도달 가능성으로 판단하므로 새 라벨을 안전하게 흡수한다 |
| 엔진이 돌린 pytest가 worktree에 `__pycache__`를 남겨 stage 커밋에 실린다 | 지금 에이전트 test 단계도 똑같다(동일 위험, 변화 없음). `--commit-file-limit`이 폭발만 막는다 |
| 출력 파서가 프로젝트마다 다르다 | `parsed=False`면 failure.md가 원문 tail을 그대로 싣고 등급은 **대형**(진단 소환)으로 간다 — 요약할 수 없을 때가 진단이 가장 필요한 때다 |
| `--replan`이 implement/test까지 되돌려 이미 한 일을 버린다 | 커밋은 브랜치에 그대로 남는다(되감지 않는다). 다음 사이클이 그 위에 쌓이고, 진짜로 되감고 싶으면 기존 `--rollback --to plan`이 있다 |
| Windows: 훅 커맨드 문자열의 역슬래시/공백 | `json.dump`가 이스케이프를 처리하고, 경로는 `"..."`로 감싼다. `verify.run_commands`는 `split_command`의 Windows 분기를 그대로 쓴다 |

---

## 14. 하지 않는 것 (요구사항 그대로)

- scope 앵커·발췌 동봉, 인덱스/쿼리/그래프, UI 변경, fast 모드
- 단계 이름 변경(`test` → `verify`), `STATE_SCHEMA` 상승
- 대상 repo의 `.claude/settings.json` 수정 (훅은 slice 디렉터리의 별도 파일 + `--settings`)
- 기존 코드에 명세 주석 소급 적용
- 프롬프트 지시 추가는 **명세 규약 1개**뿐 (나머지는 기계와 문서 규격으로 한다)
