# Auto-Recovery — 구현 계획

## 0. 읽고 확정한 사실 (추측 아님)

| 사실 | 근거 |
| --- | --- |
| `run_stage`(`aidev/pipeline.py:2612`)는 plan/implement/test/diagnose/decompose와 repair·amend 재실행까지 **모두 지나는 단 하나의 발사구**다. 그 안의 `while True` 루프가 이미 "실패 → 판단 → 재발사"를 하고 있다 | `run_pipeline:3764`, `run_verify:3453`, `run_diagnosis:3345`, `epic.run_epic:563` 전부 이 함수를 부른다 |
| **usage limit 경로는 이미 절반 이상 있다.** `looks_like_quota:1592`, `parse_reset_at:1604`(`\b(1\d{9})\b` epoch 파싱 = 요구사항의 `reached\|<타임스탬프>` 그대로), `sleep_seconds:1651`, `state["quota"]`, `STATUS_QUOTA_WAIT`, `resume_quota_wait:3671`(프로세스가 죽었다 살아나도 남은 대기를 마저 한다) | `run_stage:2727-2756` |
| 남은 여유는 **30초**다(`:2735`). 요구사항은 60초 | 같은 줄 |
| 턴 소진은 `result_subtype == "error_max_turns"`로 온다. 이 저장소는 이미 그 문자열을 알고 "쿼터가 아니다"라고 테스트한다 | `tests/test_pipeline.py:3418` |
| 판정에 필요한 계측은 **이미 전부 telemetry에 있다**. `observed.file_accesses`(operation/path/count), `observed.calls`(turn/tool/target/category/result_is_error), `estimated.repeated_reads`(path/count/repeat_bytes), `estimated.repeated_commands`(command/count), `observed.error_results`, `exact.num_turns` | `telemetry.to_dict():519-578`, `to_row():74` |
| Remaining 계산도 이미 있다. `progress.touched_paths` + `progress.remaining_paths`, 그리고 plan 경로 추출 `_PLAN_PATH_RE:686`. `write_progress:2898`가 그 조합으로 `## Remaining`을 렌더한다 | `aidev/progress.py:88-102`, `pipeline.py:2896-2915` |
| `StageSpec`(`:2313`)는 STAGES 밖 단계를 위한 탈출구다. `run_diagnosis:3308`가 readonly·고정 프롬프트·별도 phase로 세션 하나를 사는 **완성된 선례**다 — Observer는 이걸 복제하면 된다 | 같은 함수 |
| `prompt = spec.prompt or build_prompt(...)` 뒤에 `_RESUME_NOTE` / `_FRESH_SESSION_NOTE` / `resume_progress_note`가 **꼬리로 덧붙는다**(`:2688-2692`). observation 주입 자리는 여기다 | 같은 줄 |
| `phase`는 `telemetry.PHASES`에 있어야 한다. `"review"`가 **비어 있다** — Observer의 phase로 정확히 맞다(PHASES를 안 건드린다) | `telemetry.py:25` |
| `execute_stage`는 `RunConfig.disallowed_tools`를 안 채운다(`:2398-2419`). `runner.resolve_disallowed_tools`는 프로파일 + 이 필드를 합친다 | `runner.py:110-120` |
| `reporter.configure_output`이 stdout에 `errors="replace"`를 걸어둔다 → cp949 콘솔에서 한글 로그가 죽지 않는다 | `reporter.py:36-43`, `cli.py:596` |
| 이 slice 자신의 front matter는 `test_commands: python -m pytest -q`, `max_turns: implement=160`. 즉 implement는 **`aidev verify`와 `python -m aidev.specs`만** Bash로 돌릴 수 있다(diet) | `.aidev/history/20260819-auto-recovery/requirement.md`, `allowed_tools_for:2181` |

**핵심 결론: 새 발사구도, 새 상태 머신도 만들지 않는다.** `run_stage`의 실패 분기 하나에 "조치 계산 → 조치 실행 → `continue`"를 끼우는 것이 전부다. 계산은 순수 함수로 새 모듈에 분리한다.

---

## 1. 새 파일 `aidev/recovery.py` — 판독·판정·견적 (순수)

`progress.py`와 같은 층위: **파일도 세션도 state도 건드리지 않는다.** 입력은 telemetry dict와 문자열, 출력은 숫자와 판정. `pipeline`을 import하지 않는다(순환 금지). `pipeline`이 이걸 import한다. 이렇게 잘라야 임계값 테스트가 세션 없이 돈다.

### 1.1 상수 (전부 모듈 상단, 근거 주석 필수)

```python
CAUSE_QUOTA, CAUSE_TURNS, CAUSE_OTHER = "usage_limit", "turns", "other"

# claude -p가 턴 상한에서 끝났을 때의 result subtype. 나머지는 같은 사인의 다른 표현.
TURN_MARKERS = ("error_max_turns", "max_turns", "maximum number of turns")

# 병리 임계값 — 전부 "한 세션 안에서"의 수치다.
EDIT_REPEAT_MAX = 5        # 같은 파일을 5번 이상 고쳤고
EDIT_REPEAT_UNITS = 2      # 손댄 파일이 2개 이하 = 한 자리에서 맴돈 것
READ_REPEAT_MAX = 4        # 같은 파일 4회 이상 재독
READ_REPEAT_TOTAL = 12     # 재독 총량(첫 읽기 제외)
COMMAND_REPEAT_MAX = 3     # 같은 명령 3회 이상 (실패 반복의 관측 가능한 형태)
ERROR_RESULT_MAX = 10      # 도구 에러 결과 누적

SAFETY_MARGIN = 0.2        # 요구사항의 "여유 20%"
MIN_EXTENSION = 20         # 이보다 작은 연장은 세션 하나 값을 못 한다
BLIND_EXTENSION = 40       # Remaining을 셀 수 없을 때(plan/decompose)의 정액
DEFAULT_TURN_CAP = 300     # 연장 후 단계 예산의 절대 상한
EXTENSION_LIMITS = {"off": 0, "conservative": 1, "aggressive": 2}
OBSERVER_LIMITS  = {"off": 0, "conservative": 0, "aggressive": 1}

SUGGESTIONS = ("switch-approach", "rescope", "call-human")
DEFAULT_SUGGESTION = "switch-approach"
```

### 1.2 공개 API

```python
def is_turn_death(subtype: Optional[str], text: str) -> bool
```
`subtype`이 `error_max_turns`거나 `text`(= `pipeline.failure_text(run_dir)`, 즉 events.jsonl 마지막 result + stderr 꼬리)에 `TURN_MARKERS`가 있으면 True. **두 출처를 모두 본다** — 요구사항이 "events.jsonl 꼬리 판독"이라 했고, telemetry는 그 스트림의 파생물이라 서로를 검증한다.

```python
def classify(subtype, text, quota: bool) -> str   # CAUSE_QUOTA | CAUSE_TURNS | CAUSE_OTHER
```
분류기의 이름값. `quota`는 `pipeline.looks_like_quota`의 결과를 그대로 받는다(마커 목록을 두 벌 두지 않는다). 우선순위: 쿼터 > 턴 > 그 외.

```python
@dataclass
class Verdict:
    healthy: bool
    units: int          # 이 세션이 실제로 진척시킨 파일 수
    turns: int
    reasons: List[str]  # 병리 판정의 근거 (사람이 읽는 문장, Observer 프롬프트에 그대로 감)
    edits: List[Tuple[str, int]]     # (path, count) 내림차순
    rereads: List[Tuple[str, int]]
    commands: List[Tuple[str, int]]
    errors: int

def verdict(telemetry: Dict[str, Any]) -> Verdict
```
- `units` = 편집/쓰기된 서로 다른 경로 수. **하나도 없으면** 읽은 서로 다른 경로 수(plan·decompose 같은 readonly 단계의 산출 단위는 '읽기'다).
- 병리 조건(하나라도 걸리면 `healthy=False`, 근거를 `reasons`에 한 줄씩):
  1. `units == 0` — "아무 파일도 만지지 않았다"
  2. `max(edit count) >= EDIT_REPEAT_MAX` **그리고** `len(edits) <= EDIT_REPEAT_UNITS` — 같은 파일 재편집
  3. `max(read count) >= READ_REPEAT_MAX` 또는 재독 총량 `>= READ_REPEAT_TOTAL` — 재독 폭주
  4. `max(repeated_commands count) >= COMMAND_REPEAT_MAX` — 같은 테스트 반복
  5. `errors >= ERROR_RESULT_MAX`
- 아무것도 안 걸리고 `units >= 1`이면 건강. **판정은 2치**다(요구사항이 그렇게 정의했다). 애매를 병리로 밀지 않는 이유: 애매한 세션까지 Observer로 보내면 conservative에서 그냥 죽고, aggressive에서 돈만 쓴다.

```python
def extension_turns(remaining: int, v: Verdict, budget: int, cap: int) -> int
```
```
budget >= cap                    -> 0            (상한 도달)
pace  = v.turns / max(1, v.units)
want  = ceil(remaining * pace * (1 + SAFETY_MARGIN))   # remaining >= 1
want  = BLIND_EXTENSION                                # remaining == 0
return min(max(want, MIN_EXTENSION), cap - budget)
```
요구사항의 `Remaining 개수 × 실측 소화 속도(턴/파일) + 여유 20%` 그대로다.

```python
def render_activity(telemetry: Dict[str, Any], limit: int = 20) -> str
```
Observer의 유일한 입력이 될 **행동 요약 md 블록**: 턴 수/도구 카운트, 편집 상위 N(경로·횟수), 재독 상위 N, 반복 명령, 에러 결과 수, 마지막 `limit`개 도구 호출(`turn tool target` 한 줄씩, target은 이미 160자로 잘려 있다). 전부 telemetry dict에서만 만든다 — events.jsonl 원문을 프롬프트에 넣지 않는다(수백 MB가 될 수 있다).

```python
def parse_suggestion(text: str) -> str
```
observation.md에서 `suggestion:` 줄을 읽어 `SUGGESTIONS` 중 하나로. 없거나 못 읽으면 `DEFAULT_SUGGESTION`. **`call-human`이면 엔진은 재시도하지 않는다** — 외부 눈이 "사람 불러라"라고 했는데 한 번 더 태우는 건 그 눈을 산 이유를 지운다.

---

## 2. `aidev/pipeline.py`

### 2.1 상수·스테이지 (`:82-91`, `:190` 근처)

```python
OBSERVE_STAGE = "observe"
TURN_STAGES  = STAGES + (DIAGNOSE_STAGE, OBSERVE_STAGE)
MODEL_STAGES = STAGES + (DIAGNOSE_STAGE, "decompose", OBSERVE_STAGE)
STAGE_TURN_DEFAULTS = {DIAGNOSE_STAGE: 20, OBSERVE_STAGE: 20}   # 로그만 읽고 답한다
QUOTA_RESET_MARGIN_S = 60.0      # was 30.0 (:2735) — 요구사항의 "여유(60초)"
AUTO_EXTEND_MODES = ("off", "conservative", "aggressive")
DEFAULT_AUTO_EXTEND = "conservative"
```
`MODEL_STAGES`에 `observe`가 들어가는 것이 요구사항 §4의 "모델 믹스 문법 재사용"이다 — **새 front matter 키 없이** `model: observe=claude-sonnet-5` / `--model-stage observe=...`가 그대로 먹는다. `TURN_STAGES`에 넣는 것은 `max_turns: observe=30`을 위해서다.

`import` 목록(`:53-64`)에 `recovery` 추가.

### 2.2 front matter 두 스위치 (`resolve_briefing:541` 바로 아래)

```python
def resolve_auto_resume(fields) -> bool        # 없으면 True. on/off 단어는 _OFF_WORDS/_ON_WORDS 재사용
def resolve_auto_extend(fields) -> str         # 없으면 "conservative". off/conservative/aggressive 외엔 PipelineError
```
`spec_check:` / `briefing:`과 **완전히 같은 모양**(빈 값·오타는 거부, 조용한 무시 금지). `KNOWN_FRONT_MATTER_KEYS:576`에 `"auto_resume", "auto_extend"` 추가.

### 2.3 `PipelineConfig` (`:2090`)

```python
auto_resume: bool = True
auto_extend: str = DEFAULT_AUTO_EXTEND
turn_cap: int = recovery.DEFAULT_TURN_CAP

def extension_limit(self) -> int:   # recovery.EXTENSION_LIMITS[self.auto_extend]
def observer_limit(self) -> int:    # recovery.OBSERVER_LIMITS[self.auto_extend]
```

### 2.4 state 원장 — `recovery_record(state)`

`stage_entry:1312`와 같은 규약: **처음 물을 때 만든다.** 그래서 `new_state`도, `setdefault("quota", ...)`가 있는 세 곳(`:4996`, `:5088`, `:5236`)도 건드릴 필요가 없고 옛 state.json이 그대로 읽힌다. `schema`는 **2 그대로**(전부 optional 키).

```json
"recovery": {
  "extensions":  [{"stage","attempt","at","from","to","remaining","units","turns","pace","reason"}],
  "observations":[{"stage","attempt","at","run_id","suggestion","path","reasons"}],
  "waits":       [{"stage","attempt","at","kind":"usage_limit","resume_at","wait_s","source"}],
  "open":        {"stage","at","path"},        // 주입 대기 중인 observation (progress와 같은 수명)
  "stopped":     {"at","stage","pin","detail"} // 상한에 걸려 자동화를 멈춘 지점
}
```
`state["quota"]`는 **그대로 둔다**(기존 reader·테스트가 읽는다). `waits`는 덧붙이는 원장이다.

### 2.5 `run_stage` (`:2612`) — 유일한 구조 변경

교체 대상은 `:2705-2756` 한 덩어리다. 새 모양:

```python
        if not run.quota:
            if run.ok:
                entry["failures"] = 0
                if isinstance(state.get("quota"), dict):
                    state["quota"].update({"waiting": False, "resume_at": None})
                return run
            # 죽은 단계는 언제나 먼저 기록한다: 이 파일이 견적의 입력이자 Observer의 입력이다
            write_progress(cfg, rec, stage, attempt, run.telemetry, run.text,
                           progress.REASON_INCOMPLETE,
                           turn=int((run.telemetry.get("exact") or {}).get("num_turns") or 0),
                           budget=cfg.turns_for(spec.stage), state=state)
            action = plan_recovery(cfg, rec, state, spec.stage, run, attempt)
            if action is None:
                return run                                  # 기존 failure 경로, 변경 없음
            if action["kind"] == "extend":
                cfg.stage_max_turns[spec.stage] = action["to"]
                rec.write_state(state)
                continue                                    # 같은 session resume + 넓어진 예산
            text = run_observer(cfg, rec, state, spec.stage, run, requirement, attempt)
            if not text:
                return run
            # 병리 세션을 그대로 물려주면 관찰이 같은 머리 위에 얹힌다: 새 session으로
            entry.pop("session_id", None)
            session, fresh = None, True
            rec.write_state(state)
            continue

        if retries >= cfg.quota_max_retries: ...            # 이하 쿼터 경로 (아래 2.6)
```

불변식 유지 확인: `attempt`는 계속 증가 → `stage_run_id`가 매번 새 run dir를 만든다(기존 쿼터 재시도와 동일). `record_run`은 연장 재시도도 한 줄씩 남긴다(청구서는 시도의 합이라는 기존 규약). `note_stage_failure`는 여전히 `run_pipeline`이 최종 실패에만 부른다.

### 2.6 usage limit — 60초·한글 로그·`auto_resume: off`

`:2727-2756` 안에서:
- `wait = max(0.0, run.reset_at - time.time()) + QUOTA_RESET_MARGIN_S`
- `cfg.auto_resume`가 False면 **대기 없이** 원장(`waits`, `stopped{pin:"auto_resume"}`)에 남기고, 재개 시각과 재개 명령을 말한 뒤 `return run` → 기존 failed 경로.
- 로그는 요구사항의 문장 그대로 한 줄 + 기존 상세 한 줄:

```python
say("한도 도달 — {0} 자동 재개 예정".format(resume_at.strftime("%H:%M")))
say("  retry {0}/{1} in {2} ({3}), resuming session {4}".format(...))
```
`resume_quota_wait:3671`(프로세스가 죽었다 살아난 경로)도 같은 문장을 쓰도록 맞춘다.

### 2.7 `plan_recovery` — 조치 결정 (새 함수, `write_progress` 뒤)

```python
def plan_recovery(cfg, rec, state, stage, run, attempt) -> Optional[Dict[str, Any]]
```
```
auto_extend == "off"          -> None
stage == OBSERVE_STAGE        -> None          # 관찰자를 관찰하지 않는다(재귀 금지)
턴 소진이 아님                 -> None          # 그 외 에러 = 기존 경로, 요구사항 §1-c
verdict = recovery.verdict(run.telemetry)
건강:
    연장 횟수 >= cfg.extension_limit()  -> _stop_auto(state, "extensions"); None
    remaining = len(progress.remaining_paths(plan_paths(rec), progress.touched_paths(run.telemetry, (cfg.cwd, cfg.repo))))
    extra = recovery.extension_turns(remaining, verdict, cfg.turns_for(stage), cfg.turn_cap)
    extra <= 0                          -> _stop_auto(state, "turn-cap");   None
    say("정직한 소진 — Remaining {0}건, +{1}턴 연장 재개")
    원장 append -> {"kind": "extend", "to": budget + extra}
병리:
    stage == DIAGNOSE_STAGE             -> None
    관찰 횟수 >= cfg.observer_limit()    -> _stop_auto(state, "observer");  None
    say("병리 소진 — " + 근거 첫 줄 + " → Observer 소환")
    -> {"kind": "observe", "verdict": verdict}
```
`_stop_auto`는 `state["recovery"]["stopped"]`를 쓰고 **사람이 읽을 한 줄**을 말한다("자동 연장 상한(N회) 도달 — 자동화 중지, `--resume-slice`로 사람이 판단하라"). 상한 도달의 결과는 새 상태가 아니라 **기존 failed**다(요구사항 §5).

`plan_paths(rec)`는 `write_progress:2898`의 두 줄을 뽑아낸 헬퍼다. **같은 함수를 둘이 쓰므로** 로그의 "Remaining N건"과 progress.md의 `## Remaining`이 어긋날 수 없다.

### 2.8 Observer (새 함수 + 프롬프트, `run_diagnosis:3308` 옆)

```python
OBSERVER_BLOCKED_TOOLS = ("Read", "NotebookRead", "Glob", "Grep", "LS", "WebFetch", "WebSearch")

def run_observer(cfg, rec, state, stage, run, requirement, attempt) -> str
```
`run_diagnosis`를 그대로 본뜬다.

```python
spec = StageSpec(
    stage=OBSERVE_STAGE,
    policy={"safety_profile": "readonly", "permission_mode": None},
    phase="review",                       # 비어 있던 기존 telemetry phase
    allowed=(),
    disallowed=OBSERVER_BLOCKED_TOOLS,    # ← StageSpec 새 필드
    prompt=_OBSERVE_PROMPT.format(...),
)
```
- **`StageSpec`에 `disallowed: Tuple[str, ...] = ()` 필드를 더하고**, `execute_stage:2412` 근처에서 `disallowed_tools=",".join(spec.disallowed) or None`을 `RunConfig`에 넘긴다. readonly 프로파일이 이미 Bash/Edit/Write/Task를 막고, 여기에 탐색 도구까지 막으면 **Observer는 프롬프트 말고는 볼 것이 없다** — "로그만 입력"이 지시가 아니라 기계가 된다(기계 > 양식 > 지시). 기본값이 `()`라 다른 모든 단계는 바이트 단위로 그대로다.
- 프롬프트 입력: `recovery.render_activity(run.telemetry)` + `verdict.reasons` + `progress.read(progress.md)` + `failure.md`(있을 때) + requirement 본문 + `stage/attempt/budget`. requirement를 넣는 이유는 하나다 — 그게 없으면 "scope 재검토"라는 선택지가 판단 불가능한 문장이 된다. **plan.md와 코드는 넣지 않는다.**
- 출력 계약(모델이 이것만 뱉게):

```
# OBSERVATION
## 반복 중인 행동
## 막힌 지점 — 추정 원인
## 제안
suggestion: switch-approach | rescope | call-human
<2-5줄: 무엇을 다르게 하라>
```
- 저장: `write_text_atomic(rec.dir / "observation.md", text + "\n")` (`SliceRecord.observation_path` 프로퍼티 추가, `diagnosis_path:1004` 옆).
- `stage_entry(state, OBSERVE_STAGE)` 갱신 → `render_summary`의 `shown`(`:3904`)이 STAGES 밖 단계를 이미 줍기 때문에 **비용이 요약표에 저절로 뜬다**.
- `recovery.parse_suggestion(text) == "call-human"`이면 원장에 남기고 `""`를 반환 → 재시도 없이 failed. 그 외엔 `state["recovery"]["open"] = {"stage", "at", "path"}`를 세우고 텍스트를 반환.

### 2.9 observation 주입

```python
_OBSERVATION_NOTE = """\

AN OUTSIDE OBSERVER READ THIS STAGE'S LOGS
------------------------------------------
{observation}

It saw only the logs, never the code: where the code contradicts it, the code
wins. Do not repeat the behaviour it names - take its suggestion first.
"""

def observation_note(rec, state, stage) -> str
```
`resume_progress_note:2937`와 **완전히 대칭**: `state["recovery"]["open"]`의 stage가 일치하고 그 단계가 아직 done이 아닐 때만 파일을 읽어 붙인다. `run_stage:2692` 다음 줄에 `prompt += observation_note(rec, state, stage)`. state 경유이므로 프로세스가 죽었다 `--resume-slice`로 살아나도 관찰이 그대로 주입된다.

`run_pipeline:3797`의 progress 정리 블록에서 `state["recovery"]["open"]`도 같이 지운다(끝난 단계의 관찰을 다음 단계에 물려주지 않는다).

### 2.10 CLI·`_config`·dry-run·요약

- 플래그 3개(`:4399` 쿼터 플래그 옆): `--no-auto-resume`, `--auto-extend {off,conservative,aggressive}`(default `None`), `--turn-cap N`(default 300).
- `_config:4515`: `auto_resume=resolve_auto_resume(fields) and not args.no_auto_resume`(spec_check와 같은 양면 규약), `auto_extend=args.auto_extend or resolve_auto_extend(fields)`(CLI > front matter), `turn_cap=args.turn_cap`.
- `start_slice`의 dry-run(`:4686` guards 줄 아래) 한 줄 추가:
  `recovery  auto-resume on, auto-extend conservative (연장 1회), turn cap 300`
- `render_summary`: `_briefing_lines`를 본뜬 `_recovery_lines(state)` 한 줄 — 표는 넓히지 않는다.
  `Auto      연장 1 (implement 80->128), Observer 1 (switch-approach), 한도 대기 1`
  원장이 비면 아무것도 안 찍는다(=이 기능 이전과 바이트 동일).
- **죽은 프로세스 보완책**(요구사항 §2가 plan에 제안하라고 한 것): `list_slices:5982`의 STATUS 셀을 `quota_wait`일 때 `quota_wait 03:40`으로 만든다(`state["quota"]["resume_at"]`의 시각). 26자 컬럼 안이고 줄이 늘지 않아 `--list`의 컬럼 폭 테스트(`test_pipeline.py:677`)를 깨지 않는다. 여기에 `resume_slice --dry-run`(`:4902`)에 `wait    03:40 이후 재개 예정 — aidev pipeline --repo <repo> --resume-slice <id>` 한 줄. **데몬도 crontab도 만들지 않는다**(요구사항 "하지 않는 것": 새 발사의 자동화 금지). 살아 있는 프로세스는 sleep으로 버티고, 죽었으면 다음 명령이 시각과 명령어를 알려준다 — 실제 재개는 이미 `resume_quota_wait`가 남은 대기를 마저 하고 이어간다.

---

## 3. `aidev/epic.py`

- `validate_items:297`에 `pipeline.resolve_auto_resume(fields)` / `resolve_auto_extend(fields)` 두 줄 추가 + docstring의 "All seven declared keys" → nine.
- `_DECOMPOSE_PROMPT:229`의 "exactly seven keys" 문장에 `'auto_resume:' (on/off)`, `'auto_extend:' (off/conservative/aggressive)`를 더하고 아홉으로.
- 그 외 변경 없음. 에픽의 decompose는 `run_stage`를 지나므로 자동 조치를 **그대로 물려받는다**(`EpicRecord`도 `.dir`/`.label`/`.write_state`를 가지므로 `write_progress`가 이미 그러하듯 동작한다). 단 Observer는 `rec.dir`에만 쓰므로 에픽에서도 안전하다.

---

## 4. `tests/fake_pipeline_claude.py` — 새 사인 두 개

기존 경로는 **한 바이트도 안 건드린다**(새 env가 없으면 지금 이벤트 그대로). 추가:

```
AIDEV_FAKE_MODE=turns     첫 AIDEV_FAKE_TURN_FAILS(기본 1)회를 error_max_turns로 죽인다
AIDEV_FAKE_SICK=1         그 죽음이 병리 패턴을 남긴다 (같은 파일 Edit 6회 + 같은 pytest 3회, 결과는 is_error)
AIDEV_FAKE_OBSERVATION    observe 단계의 최종 답 (기본: suggestion: switch-approach 를 담은 정형 문서)
```
- 건강 패턴: 서로 다른 파일 3개 Edit + 재독 없음 → `units=3`, 병리 근거 0.
- `num_turns`는 argv의 `--max-turns` 값을 그대로 보고한다 → 견적이 실측 예산을 쓴다는 것과, **연장이 실제로 CLI까지 갔는지**를 테스트가 argv로 검증할 수 있다.
- `detect_stage`에 `"OBSERVE STEP"`을 **diagnose보다 먼저** 넣는다(관찰 프롬프트가 인용하는 progress/failure 안에 다른 단계 이름이 섞여 있어도 오분류되지 않게).
- result 이벤트는 `subtype: "error_max_turns"`, `is_error: true`, exit 1.

---

## 5. 테스트

### 5.1 새 파일 `tests/test_recovery.py` (세션 0, 순수 함수)
- `is_turn_death` / `classify`: `error_max_turns` → turns, `usage limit reached|...` → usage_limit, `TypeError...` → other. 쿼터가 턴을 이긴다.
- `verdict`: 건강(3파일 편집·반복 없음) / 병리 5종(재편집·재독·재독총량·명령반복·에러) 각각, 그리고 `units==0`.
- `extension_turns`: 실측 케이스 — `budget=80, turns=80, units=8, remaining=4` → pace 10 → `ceil(4*10*1.2)=48`. `remaining=0` → 40. `MIN_EXTENSION` 하한. `budget=290, cap=300` → 10으로 잘림. `budget=300` → 0.
- `parse_suggestion`: 세 값 + 없을 때 기본값.
- `render_activity`: 편집·재독·반복명령·마지막 호출이 다 들어가고, 빈 telemetry에서도 예외 없이 문자열.

### 5.2 `tests/test_pipeline.py` 추가 (fake 기반 end-to-end)
1. **한도 → 대기 → 자동 재개**: 기존 `test_quota_failure_waits_for_the_reset_and_resumes_the_session` 확장 — 로그에 `한도 도달 —`과 `자동 재개 예정`, `state["recovery"]["waits"][0]["kind"] == "usage_limit"`.
2. **`auto_resume: off`**: `_sleep`을 `pytest.fail`로 두고, 한도 사망이 **기다리지 않고** exit 1, `stopped["pin"] == "auto_resume"`, 로그에 재개 명령이 있다.
3. **턴 소진(건강) → 견적 → 연장 재개**: `AIDEV_FAKE_MODE=turns`, `AIDEV_FAKE_TURN_FAILS=1`, `auto_extend: aggressive`. exit 0. 로그에 `정직한 소진 — Remaining N건, +M턴 연장 재개`. **재시도 invocation의 argv `--max-turns`가 커져 있다.** `state["recovery"]["extensions"][0]["to"] == from + M`.
4. **연장 상한(conservative=1회)**: `AIDEV_FAKE_TURN_FAILS=99` → 연장 1회만 하고 failed(exit 1), `stopped["pin"] == "extensions"`, invocation 수가 정확히 2.
5. **총 턴 상한**: `--turn-cap 100`, `max_turns: implement=90` → 연장분이 10으로 잘린다. `--turn-cap 80`이면 연장 자체가 안 일어나고 `pin == "turn-cap"`.
6. **병리 → Observer → observation.md → 주입 재시도**: `AIDEV_FAKE_SICK=1`, `auto_extend: aggressive`. `observation.md` 존재, `state["stages"]["observe"]["status"] == "done"`, 재시도 프롬프트에 `AN OUTSIDE OBSERVER` + 관찰 본문, **그 재시도는 `--resume`가 없다**(새 session), Observer invocation의 argv에 `--disallowedTools`가 `Read`를 포함, `--model`이 `model: observe=...`를 따랐다.
7. **Observer 상한 1회 / conservative에서는 Observer 없음**: conservative + SICK → observe 단계가 아예 안 생기고 failed.
8. **`auto_extend: off`**: 턴 소진이 즉시 failed, 원장 비어 있음 = v0.6 동작 그대로.
9. **`call-human`**: `AIDEV_FAKE_OBSERVATION`이 `suggestion: call-human` → 재시도 없이 failed, 이유에 observation.md 경로.
10. **원장·요약**: `render_summary` 출력에 `Auto` 줄, 원장이 비면 그 줄이 없다.
11. **재개 시 연장 유지**: 연장 후 프로세스가 죽은 상태를 만들고 `--resume-slice` → argv의 `--max-turns`가 연장된 값(state에서 복원).
12. front matter: `auto_extend: sometimes` → exit 2, `auto_resume: maybe` → exit 2, dry-run에 `recovery` 줄.

### 5.3 **고쳐야 하는 기존 테스트 3개** (요구사항이 값을 바꾼 결과)
- `test_quota_failure_waits_for_the_reset_and_resumes_the_session:529` — `120 <= sum(slept) <= 160` → **여유가 30→60초**이므로 `<= 190`으로. (이 한 줄을 놓치면 전량 통과가 깨진다.)
- `test_every_known_front_matter_key_has_a_reader:2846` — 집합에 `auto_resume`, `auto_extend` 추가.
- `test_an_unknown_front_matter_key_warns_and_is_ignored:2816` — `known:` 문자열 끝에 두 키 추가.

`test_epic.py`는 변경 불필요(쿼터 폴백 경로만 쓴다).

---

## 6. `README.md`

1. `### 실패와 쿼터`(:878)를 **`### 실패와 자동 복구 (v0.7)`**로 확장: 사인 3분류 표, 한도 대기의 60초 여유와 한글 로그, 건강/병리 임계값 표(위 §1.1 숫자 그대로), 견적 공식, Observer의 입력·출력·차단 도구, 안전핀 3종과 `auto_extend` 모드별 상한 표.
2. `### 안전핀`(:902)에 항목 추가: 자동 연장 slice당 최대 2회·총 턴 상한 300·Observer 1회·상한 도달 시 기존 failed로 정지·모든 자동 조치는 `state.json`의 `recovery`에 남는다.
3. 옵션 표(:986)에 `--auto-extend` / `--no-auto-resume` / `--turn-cap` 3행.
4. front matter 문서: 일곱 키 → 아홉 키(:667-672, :679의 `known:` 예시 출력, epic 절).
5. 저장 구조(:794-811)에 `observation.md` 한 줄, state 키 문단(:848)에 "v0.7이 더한 `recovery`도 optional이라 `schema`는 **2 그대로**".
6. `단계별 턴 예산`(:293) 절 끝에 "이 예산은 이제 **바닥이 아니라 시작점**이다 — 건강한 소진은 엔진이 견적을 내서 올린다" 한 줄 + 자동 연장 절로 링크.

---

## 7. 검증 방법

- `aidev verify` (= `python -m pytest -q`) — implement에 허용된 것은 이 래퍼와 `python -m aidev.specs`뿐이므로 그걸로 돈다. 전량 통과 + 신규 `tests/test_recovery.py` 통과.
- `python -m aidev.specs`로 새/변경 함수의 명세 주석(한 줄 요약 + `@param` + 분기 있으면 `@flow`)을 스스로 확인한다 — 이 저장소에서 명세 누락은 취향이 아니라 verify FAIL이다.
- 회귀의 핵심 지표: **`auto_extend: off` + `auto_resume: on`(=기본 쿼터 동작)에서 프롬프트와 이벤트가 지금과 동일**. `StageSpec.disallowed` 기본값 `()`, `recovery` 키의 지연 생성, `state["quota"]` 보존이 그 근거다.

---

## 8. 위험과 대응

| # | 위험 | 대응 |
| --- | --- | --- |
| R1 | **무한 재발사.** 연장 후에도 매번 턴에서 죽으면 루프가 안 끝난다 | 상한이 `while` 안이 아니라 **원장 길이**로 걸린다(프로세스가 죽었다 살아나도 카운트가 이어진다). `off/conservative/aggressive` = 0/1/2회, Observer 0/0/1회, 예산 절대 상한 300. 테스트 4·5·7이 정확히 이걸 친다 |
| R2 | **재귀.** Observer가 턴에서 죽으면 또 Observer | `plan_recovery`의 첫 줄이 `stage == OBSERVE_STAGE -> None`. diagnose도 Observer 대상에서 제외 |
| R3 | **오분류로 건강한 세션을 병리로 몰아 돈을 쓴다** | 병리 판정은 conservative(기본)에서 **아무 세션도 사지 않는다**. Observer는 aggressive에서만, slice당 1회, readonly 20턴 |
| R4 | **견적 폭주.** `units=1, turns=80, remaining=15`면 1440턴을 요구한다 | `min(want, cap - budget)`으로 언제나 잘린다. 잘렸다는 사실을 로그와 원장에 남긴다 |
| R5 | **plan 단계엔 Remaining이 없다**(plan.md가 아직 없거나 낡음) | `remaining == 0` → `BLIND_EXTENSION=40` 정액. 견적을 지어내지 않고 "못 재겠다"를 명시적 상수로 표현 |
| R6 | **기존 쿼터 테스트가 60초 여유로 깨진다** | §5.3에 못 박았다. 이건 요구사항이 바꾼 값이므로 테스트를 따라 고치는 것이 맞다 |
| R7 | **한글 로그가 cp949 콘솔에서 죽는다** | `reporter.configure_output`이 stdout에 `errors="replace"`를 이미 건다(`reporter.py:36-43`). 로그 문장에만 한글을 쓰고 **파일 이름·state 키·프롬프트는 전부 ASCII** |
| R8 | **`--disallowedTools` 추가가 다른 단계에 샌다** | `StageSpec.disallowed` 기본값 `()` → `",".join(()) or None` = `None` = 지금과 동일. Observer만 값을 준다 |
| R9 | **에픽/amend/repair 경로에서만 터진다** | 조치는 전부 `run_stage` 한 곳에 있으므로 네 경로가 같은 코드를 지난다. `EpicRecord`가 갖지 않은 속성(`read_plan`, `failure_path`)은 `write_progress:2897`가 이미 하듯 `hasattr`/`Path(rec.dir)/...`로만 접근 |
| R10 | **turn death 오탐**(`error_max_turns`가 아닌데 턴으로 읽음) | 두 출처(telemetry subtype + events.jsonl 꼬리) 모두를 보고, 어느 쪽도 아니면 `CAUSE_OTHER` → 기존 경로. 오탐의 비용은 세션 한 번이고 상한이 그걸 1~2회로 묶는다 |
| R11 | **구현 도중 턴 소진**(160턴, 파일 6개) | 작업 순서를 §9처럼 **각 단계가 그 자체로 green**이 되게 자른다 |

---

## 9. 작업 순서 (턴이 끊겨도 다음 세션이 이어받을 수 있게)

1. `aidev/recovery.py` 전체 + `tests/test_recovery.py` — 세션 없이 도는 순수 계산. 여기까지가 전체 위험의 절반이고, 끊겨도 다음이 이걸 그대로 쓴다.
2. `pipeline.py` 상수·front matter 2개·`PipelineConfig` 3필드·CLI 3플래그·`_config` 배선 + `KNOWN_FRONT_MATTER_KEYS` + 기존 테스트 3개 수정. (여기서 한 번 verify: 전량 green이어야 한다.)
3. usage limit 절반: 60초 여유, 한글 로그, `auto_resume: off`, `waits` 원장, `--list`/dry-run 시각 표시.
4. `plan_recovery` + 연장 경로(`run_stage` 구조 변경) + `recovery_record` + fake의 `turns` 모드.
5. Observer: `StageSpec.disallowed` → `execute_stage`, `_OBSERVE_PROMPT`, `run_observer`, `observation_note`, `observation_path`, fake의 `SICK`/`OBSERVATION`.
6. `_recovery_lines` 요약 줄 + `epic.py` 2곳 + README 6곳.

## 10. 하지 않는 것 (요구사항이 명시적으로 뺀 것)

- 새 발사의 자동화 — 데몬·cron·watcher 없음. 자동인 것은 **재개**뿐이다.
- 크레딧 구간 진입 판단, UI, 브리핑·그래프·리포트 그래프 변경.
- `STATE_SCHEMA` 증가, `PHASES` 확장, 새 slice status 값 추가.
- `QUOTA_MARKERS` 재작성 — 실측 문구가 더 모이기 전까지 잠정값 그대로 둔다.
