# Pipeline 결함 3건 수정 — 구현 계획

## 0. 현재 코드에서 확인한 사실 (추측 아님)

| 사실 | 위치 |
| --- | --- |
| 스테이지별 제약은 `STAGE_POLICY`의 `safety_profile` / `permission_mode` 두 개뿐. allow 규칙 개념이 아예 없다 | `aidev/pipeline.py:61-65` |
| `runner.RunConfig`에는 이미 `allowed_tools` 필드가 있고 `build_command`가 `--allowedTools`로 넘긴다. **pipeline이 그 필드를 채우지 않을 뿐이다** | `aidev/runner.py:61`, `aidev/runner.py:121-122` |
| `execute_stage`가 `RunConfig`를 만들 때 `allowed_tools`를 생략한다 | `aidev/pipeline.py:859-873` |
| `write_json_atomic`의 `os.replace`에는 재시도가 없다. `RunStore.write_live` → `_write_json` → 여기로 온다 | `aidev/storage.py:248-257`, `aidev/storage.py:230` |
| `state.json`만 별도로 20회 × 0.05초 재시도를 갖고 있다 | `aidev/pipeline.py:274-285` |
| `run_stage`는 `entry["session_id"]`가 있으면 **실패 이력과 무관하게 항상** `--resume` 한다 | `aidev/pipeline.py:956-975` |
| `entry["status"] = "failed"`를 세우는 곳은 3군데 (PipelineError / `not run.ok` / `finish_stage` 사유) | `aidev/pipeline.py:1176`, `1181`, `1193` |
| `--list`는 `(no slices in ...)`만, `find_slice`는 `no slices in ...`만 낸다 | `aidev/pipeline.py:1552-1555`, `1528-1529` |
| `safety_violations`는 `safety_profile == "readonly"`일 때만 계산된다 → allow 규칙을 붙여도 텔레메트리 위반 카운트에 영향 없음 | `aidev/telemetry.py:471-479` |
| 이 repo의 `.gitignore`는 `.claude/`를 통째로 무시한다. 대상 repo가 그러리라는 보장은 없다 | `.gitignore:26` |

---

## 결함 1 — 테스트 명령 실행 권한

### 선택지 비교

| | A. `--allowedTools` 전달 | B. 대상 repo `.claude/settings.json` 병합 | C. `--settings <file>` (도구 소유 파일) |
| --- | --- | --- | --- |
| 대상 repo 파일 변경 | **없음** | 있음 (settings.json 생성/수정) | 없음 |
| dirty 검사와의 충돌 | 없음 | **있음.** `.claude/`를 ignore하지 않는 repo에서는 `ensure_clean_repo` / `dirty_reason`(`pipeline.py:499-516`)가 다음 slice를 거부한다. 되돌리기 로직·백업·크래시 시 복구가 전부 필요 | 없음 |
| plan(readonly) 사후 검증과의 충돌 | 없음 | 쓰기 시점을 잘못 잡으면 `repo_changes(include_slice_files=True)` before/after 비교(`pipeline.py:1099-1103`)가 plan을 실패시킨다 | 없음 |
| 기존 코드 재사용 | **`RunConfig.allowed_tools` + `build_command`가 이미 있다. 신규 I/O 0줄** | JSON 읽기·병합·쓰기·복구 신규 | `--settings` 플래그 신규 전달 + 파일 생성 |
| 사람 설정과의 관계 | 사람의 `settings.json` / `settings.local.json`에 **추가로** 얹힌다 (덮어쓰지 않음) | 사람 파일을 우리가 편집 = 소유권 충돌 | 병합이지만 파일이 하나 더 생김 |
| 검증 용이성 | stub이 `sys.argv`를 로깅하므로 **정확한 규칙 문자열이 프로세스에 도달했는지 테스트로 증명 가능** | 파일 상태만 검증 가능, 실제 전달 여부는 별개 | argv로 경로만 보임, 내용은 간접 |
| 무제한 Bash 위험 | 없음 (규칙 단위) | 없음 | 없음 |

### 결정: **A (`--allowedTools`)**

요구사항이 명시한 목적("test 단계 실행 전에 테스트 명령 allow 규칙이 보장된다")은 A로 충족되고, B는 그 목적을 위해 **대상 repo를 더럽히는 부작용**을 새로 만든다. 이 파이프라인은 dirty repo를 거부하는 안전핀(`pipeline.py:519-522`)을 핵심 규약으로 쓰고 있어서, 파이프라인 스스로 tracked 파일을 만드는 건 그 규약과 정면으로 충돌한다. 영속적인 규칙이 필요한 사람은 지금처럼 `.claude/settings.local.json`을 직접 두면 되고(이 repo가 이미 그렇게 하고 있다), A는 그것과 싸우지 않고 더해진다.

### 변경 — `aidev/pipeline.py`

1. **상수 추가** (`STAGE_POLICY` 바로 아래):

```python
# acceptEdits는 편집만 자동 승인한다. Bash는 여전히 승인 대상이라, 검증 명령을
# 규칙 단위로 미리 허용하지 않으면 test 단계가 테스트를 한 번도 못 돌린다.
# 무제한 Bash(--dangerously-skip-permissions)는 금지 — 여기 적힌 것만 열린다.
TEST_COMMAND_TOOLS: Tuple[str, ...] = (
    "Bash(npm test)",
    "Bash(npm test:*)",
    "Bash(npm run test:*)",
    "Bash(node --test)",
    "Bash(node --test:*)",
    "Bash(pytest)",
    "Bash(pytest:*)",
    "Bash(python -m pytest:*)",
)
# plan은 readonly라 Bash 자체가 --disallowedTools로 막혀 있다: 여기 넣지 않는다.
STAGES_WITH_TEST_COMMANDS: Tuple[str, ...] = ("implement", "test")
```

정확 일치형(`Bash(npm test)`)과 접두형(`Bash(npm test:*)`)을 **둘 다** 넣는다 — 인자 없는 `npm test`가 접두 규칙에 걸리는지는 CLI 버전에 따라 다르고, 둘 다 두면 어느 쪽이든 통과한다. pytest 계열을 넣는 이유는 이 파이프라인이 자기 자신(Python repo)을 구동하며, Done Criteria가 파이프라인을 통한 pytest 실행을 요구하기 때문이다.

2. **`allowed_tools_for(stage, cfg) -> Optional[str]`** 신규 함수: `stage in STAGES_WITH_TEST_COMMANDS`가 아니면 `None`, 맞으면 `TEST_COMMAND_TOOLS + tuple(cfg.allow_tools)`를 중복 제거 후 `","`로 join. (`--disallowedTools`와 같은 표기법이고, 규칙 문자열에 쉼표가 없으므로 안전하다.)

3. **`PipelineConfig`에 `allow_tools: List[str] = field(default_factory=list)` 추가** (`pipeline.py:756-768`).

4. **`execute_stage`**에서 `RunConfig(...)` 생성 시 `allowed_tools=allowed_tools_for(stage, cfg)` 추가 (`pipeline.py:859-873`). 텔레메트리는 손대지 않는다 — 값은 `cfg.to_dict()`를 통해 이미 `run.json`에 남는다(`runner.py:78`).

5. **CLI**: `add_parser`에 `--allow-tool`(repeatable, dest=`allow_tools`) 추가, `_config`에서 `allow_tools=list(args.allow_tools or [])` 전달. help: "extra permission rule for implement/test, e.g. --allow-tool 'Bash(npx vitest:*)'".

6. **프롬프트 보강** — `build_prompt(stage, requirement, plan="", allowed: Sequence[str] = ())`로 시그니처 확장. implement/test 프롬프트 끝에 허용 목록을 렌더링하고, `_TEST_PROMPT`에 두 줄 추가:

```
- These Bash commands are pre-approved; prefer one of them:
  {allowed}
- Run the test command as ONE plain command. Do not chain with '&&' or 'cd x &&':
  a chained command is a different command and may not be approved.
- If a command is refused for permission reasons, say so verbatim and report FAIL.
```

허용 목록은 `TEST_COMMAND_TOOLS`에서 생성하므로 실제 전달 규칙과 절대 어긋나지 않는다. `run_stage`가 `build_prompt(..., allowed=allowed_tools_for(stage, cfg))`로 넘긴다.

### 검증

- 단위: `allowed_tools_for("plan", cfg) is None`, `"Bash(npm run test:*)" in allowed_tools_for("test", cfg)`, `--allow-tool`로 준 규칙이 뒤에 붙는지.
- argv 도달: `test_stage_policies_reach_the_claude_command`(`test_pipeline.py:143`)를 확장 — plan argv에 `--allowedTools` 없음, implement/test argv의 `--allowedTools` 값에 세 규칙이 그대로 들어 있음. **이 테스트는 Windows에서 `claude.cmd` shim(`test_pipeline.py:22-24`)을 거쳐 실제로 `cmd /c`를 통과하므로, 괄호·공백·`*`가 인용 과정에서 깨지지 않는다는 것까지 증명한다.**
- 시나리오(fake stub): 아래 § 테스트 참조.

---

## 결함 2 — `write_json_atomic`의 Windows PermissionError

### 변경 — `aidev/storage.py` (코어 수정, 승인된 1곳)

```python
# Windows: 리더가 파일을 연 순간 os.replace가 WinError 5로 죽는다. CPython의
# open()은 delete 공유를 주지 않으므로, 그저 읽고 있는 watcher 하나로도 발생한다.
# 지연이 아니라 순간 충돌이므로 짧게 여러 번 다시 시도한다. 상한 1초.
_REPLACE_RETRIES = 20
_REPLACE_RETRY_S = 0.05

_sleep = time.sleep  # pipeline._sleep / cli._sleep과 같은 test seam
```

`write_json_atomic`은 **본문 전체**(tmp 생성 → write → fsync → replace)를 재시도 루프로 감싼다. `os.replace`만이 아니라 `tmp.open("w")`도 같은 이유로 PermissionError를 낼 수 있고, 재시도 시 tmp를 다시 쓰는 편이 부분 기록 tmp를 남기는 것보다 안전하다. 마지막 시도까지 실패하면 원래대로 예외를 올린다 — 삼키지 않는다.

`write_text_atomic`(`pipeline.py:338-346`)은 **건드리지 않는다** — 요구사항이 코어 수정을 이 한 곳으로 한정했고, plan.md/승인 파일은 폴링 대상이 아니다.

### 기존 `state.json` 재시도와의 관계

`SliceRecord.write_state`(`pipeline.py:274-285`)의 재시도는 **남긴다.** 지우면 기존 회귀 테스트 `test_state_write_retries_a_transient_windows_replace_collision`(`test_pipeline.py:830`)이 깨지고, 요구사항은 "기존 회귀 테스트 전부 통과"를 증명 조건으로 걸었다. 결과적으로 state.json 쓰기는 20회 × (내부 1초) = 최악 20초 상한이 된다. 잠금이 영구히 안 풀리는 병적 상황에서만 도달하는 값이고 결국 예외로 끝나므로 수용한다. 코드에 그 중첩을 한 줄 주석으로 명시한다.

### 알려진 트레이드오프 (문서화)

`live.json`은 이벤트마다(0.3초 throttle) 쓰인다. 리더가 파일을 **영구히** 잡으면 이전에는 즉사했지만 이제는 쓰기마다 최대 1초 지연되어 run이 느려진다. 죽는 것보다는 낫고, `aidev watch`는 `read_json_tolerant`가 `read_text`로 열고 즉시 닫으므로 순간 점유만 한다. "live.json 쓰기 실패는 best-effort로 무시" 같은 확장은 이번 범위 밖으로 둔다.

### 검증 — `tests/test_storage.py` 신규

1. `test_write_json_atomic_retries_a_locked_replace` — `monkeypatch.setattr(storage.os, "replace", ...)`로 첫 2회 PermissionError 후 성공. 파일 내용이 정확히 기록되고, `_sleep` 호출 합이 1.0초 이하이며, `.tmp`가 남지 않음을 확인.
2. `test_write_json_atomic_gives_up_after_a_second` — 항상 PermissionError. `pytest.raises(PermissionError)`, `_sleep` 호출 횟수가 `_REPLACE_RETRIES - 1`, 합이 1.0초 이하.
3. `test_write_json_atomic_survives_a_real_windows_reader` — `@pytest.mark.skipif(os.name != "nt")`. live.json을 만든 뒤 `open(path)`로 실제 핸들을 잡고, 0.15초 뒤 닫는 스레드를 띄운 상태에서 `write_json_atomic`을 호출 → 예외 없이 새 내용이 기록됨. (monkeypatch 없이 진짜 WinError 5 경로를 통과하는 테스트. 1·2번이 결정적 회귀, 3번이 현실성 확인.)

---

## 결함 3 — resume 세션 정책

### 검토

지금 `run_stage`는 `entry["session_id"]`만 보고 무조건 `--resume`한다(`pipeline.py:956`, `971`). 그런데 재개 사유는 두 가지고 성격이 정반대다.

- **쿼터 대기 후 재시도** — 세션은 *중단*됐을 뿐 결론을 내지 않았다. resume이 정답이고, 기존 테스트(`test_quota_failure_waits_for_the_reset_and_resumes_the_session`)가 그걸 지킨다.
- **비쿼터 실패 후 재시도** — 세션은 이미 "막혔다 / FAIL"이라는 **결론**을 갖고 있다. guard-inspect에서 관측된 게 정확히 이 경로다: 권한 환경이 바뀌었는데도 낡은 결론을 근거로 41초 만에 FAIL을 재확정했다.

### 결정: 연속 **비쿼터** 실패 N회 이상이면 새 세션 (기본 N=1)

N=1로 두는 근거: 결함 1을 고친 뒤의 재검증 시나리오가 바로 "환경이 바뀐 뒤 첫 재시도"이고, 이때 낡은 세션을 물려주면 고친 의미가 없다. 첫 시도 중 프로세스가 죽은 경우(`failures == 0`, `session_id`는 있음)는 **진행 중 작업**이므로 그대로 resume한다 — 조건에 `failures > 0`을 넣어 이 경우를 보호한다. 옛 동작이 필요하면 `--session-reset-after`를 크게 주면 된다.

### 변경 — `aidev/pipeline.py`

1. 상수/설정: `DEFAULT_SESSION_RESET_AFTER = 1`, `PipelineConfig.session_reset_after: int = DEFAULT_SESSION_RESET_AFTER`, CLI `--session-reset-after`(help: "start a fresh session after N consecutive non-quota failures of the same stage"), `_config`에서 전달.

2. **`note_stage_failure(state, stage)`** 신규: `entry["failures"] = int(entry.get("failures") or 0) + 1`.

3. **`run_pipeline`의 실패 지점**(`pipeline.py:1180-1194`):
   - `not run.ok` 분기: `if not run.quota: note_stage_failure(...)` — **쿼터 소진으로 포기한 실패는 세지 않는다.** 그 세션은 오히려 resume해야 맞다.
   - `finish_stage`가 사유를 돌려준 분기: 무조건 `note_stage_failure(...)`. **test verdict FAIL이 여기로 온다 — 관측된 결함 3의 실제 경로.**
   - `PipelineError` 분기(`claude` 미발견 등): 세지 않는다. 세션이 만들어진 적조차 없다.

4. **`run_stage` 진입부**(`pipeline.py:953-958`):

```python
failures = int(entry.get("failures") or 0)
session = entry.get("session_id")
fresh = False
if session and failures and failures >= cfg.session_reset_after:
    # 낡은 세션은 "아까 막혔다"는 결론을 함께 갖고 온다. 환경이 바뀌었을 수 있으니
    # 기억이 아니라 지금의 repo를 보고 판단하게 한다.
    say("stage '{0}' failed {1}x - starting a new session instead of resuming {2}"
        .format(stage, failures, session))
    entry.pop("session_id", None)
    session = None
    fresh = True
```

프롬프트 조립부(`pipeline.py:966-968`)는 `if session: += _RESUME_NOTE` / `elif fresh: += _FRESH_SESSION_NOTE`로 분기. `_FRESH_SESSION_NOTE`:

```
---
A previous attempt at this stage failed and this is a NEW session with no memory
of it. Judge the current state of the repository and the tools you have access to
now, from scratch. Do not assume an earlier obstacle is still in place.
```

5. 성공 시 `entry["failures"] = 0`으로 리셋(`run.quota`가 아니고 `run.ok`인 반환 직전, 기존 quota 리셋 코드 옆).

6. `banner(...)`에 `fresh` 표시 추가: `(attempt 3, new session)`.

### 검증 (fake stub 시나리오)

- `test_a_failed_stage_retries_in_a_new_session`: `AIDEV_FAKE_MODE=fail`로 slice 실행 → exit 1, `stages.plan.failures == 1`, `session_id` 기록됨. 그 다음 `AIDEV_FAKE_MODE=ok`로 `--resume-slice last` → **2번째 invocation argv에 `--resume`이 없고**, 프롬프트에 `has been resumed`가 아니라 새 세션 문구가 들어 있으며, slice가 done으로 끝난다.
- `test_session_reset_after_can_be_raised_back_to_the_old_behaviour`: 같은 상황에서 `--session-reset-after 5` → argv에 `--resume pipe-session`이 그대로 있다.
- `test_a_test_verdict_fail_counts_as_a_stage_failure`: `AIDEV_FAKE_VERDICT=FAIL`로 완주 → `stages.test.failures == 1`. `VERDICT=PASS`로 resume → test가 `--resume` 없이 재실행되고 done. **guard-inspect 재검증과 동일한 형태의 시나리오.**
- 기존 `test_quota_failure_waits_for_the_reset_and_resumes_the_session`이 쿼터 경로의 resume 유지를 그대로 지킨다(수정 없음, 회귀 가드).

---

## 개선 — `--list` / `--resume-slice`의 repo 미지정 UX

`_dispatch`(`pipeline.py:1370-1382`)에서 `repo_given = args.repo is not None`을 계산해 `list_slices(repo, repo_given)` / `resume_slice(args, repo, data_dir, repo_given)` → `find_slice(repo, pattern, repo_given)`로 전달. 두 함수 모두 기본값 `True`로 두어 테스트의 직접 호출(`pipeline.find_slice(repo, "last")`, `test_pipeline.py:692`)은 그대로 통과한다.

```python
def _repo_hint(repo: Path, repo_given: bool) -> str:
    """cwd를 기본값으로 썼는데 .aidev가 없으면, 빈 결과가 아니라 왜 비었는지 말한다."""
    if repo_given or (Path(repo) / AIDEV_DIRNAME).is_dir():
        return ""
    return ("\n    no {0}/ here - this is probably not the target repository.\n"
            "    pass --repo <path>".format(AIDEV_DIRNAME))
```

- `list_slices`: `(no slices in ...)` 뒤에 힌트를 붙여 출력.
- `find_slice`: `no slices in {root}{hint}` 로 메시지 확장 — 기존 `test_usage_errors`의 `"no slices in"` 부분 문자열 단언은 유지된다.
- 신규 테스트: `--repo` 없이 `.aidev`가 없는 cwd에서 `--list` → 출력에 `--repo`가 포함. `--repo`를 명시하면 힌트가 **나오지 않음**(잡음 방지).

---

## 변경 파일 요약

| 파일 | 변경 |
| --- | --- |
| `aidev/storage.py` | `_REPLACE_RETRIES` / `_REPLACE_RETRY_S` / `_sleep` 추가, `write_json_atomic` 본문을 PermissionError 재시도로 감싼다. **다른 함수는 손대지 않는다.** |
| `aidev/pipeline.py` | `TEST_COMMAND_TOOLS` / `STAGES_WITH_TEST_COMMANDS` / `allowed_tools_for`; `PipelineConfig.allow_tools`·`session_reset_after`; `execute_stage`의 `allowed_tools=` 전달; `build_prompt`에 허용 목록 주입 + `_TEST_PROMPT` 지침 보강; `_FRESH_SESSION_NOTE`; `note_stage_failure`; `run_stage` 세션 정책; `run_pipeline` 실패 카운트 3지점; `banner` 표시; `--allow-tool` / `--session-reset-after`; `_repo_hint` + `list_slices` / `find_slice` / `_dispatch` / `resume_slice` 시그니처 |
| `tests/fake_pipeline_claude.py` | 모드 `requires_approval` 추가: 자기 `sys.argv`에서 `Bash(npm run test:*)`를 찾아, 없으면 `This command requires approval` + `TEST_RESULT: FAIL`, 있으면 `345 passing` + `TEST_RESULT: PASS`를 낸다. docstring의 모드 목록도 갱신 |
| `tests/test_pipeline.py` | 결함 1(argv 도달 + `requires_approval` end-to-end), 결함 3(새 세션 3종), repo 힌트 2종, `allowed_tools_for` 단위 테스트 |
| `tests/test_storage.py` | `write_json_atomic` 재시도 3종 (monkeypatch 2 + Windows 실제 핸들 1) |
| `README.md` | ① Slice Pipeline 안전핀에 "테스트 명령만 규칙 단위로 허용, 무제한 Bash 없음" 항목 + 규칙 목록 ② 189행 "같은 단계 복구는 기존 session을 resume한다"를 새 정책으로 수정 ③ 옵션 표에 `--allow-tool` / `--session-reset-after` ④ 저장소 섹션에 live.json 재시도 한 줄 |

---

## 검증 절차

1. `python -m pytest -q` — 기존 전량 + 신규 통과. (`pyproject.toml`의 `testpaths = ["tests"]`, `addopts = "-q"`)
2. `python -m pytest tests/test_storage.py tests/test_pipeline.py -q` — 이번에 손댄 두 축만 빠르게.
3. **Windows에서 반드시 한 번 전체 실행** — `test_windows_cmd_stub_reads_utf8_prompt`와 신규 Windows 핸들 테스트가 `skipif(os.name != "nt")`라서 다른 OS에서는 아무것도 증명하지 못한다.
4. 명령 조립 육안 확인:
   `python -m aidev.cli pipeline --repo . --requirement tasks/fix-pipeline-v0.2.1.md --dry-run`
   후 실제 run의 `data/runs/<id>/run.json`의 `config.allowed_tools` / `command` 확인.
5. **실전 재검증 (Done Criteria)** — guard-inspect worktree에서:
   - `python -m aidev.cli pipeline --repo <worktree> --list` 로 slice id와 stage 상태 확인
   - `... --resume-slice <id> --dry-run` 으로 `plan=done implement=done test=failed|pending` 확인
   - `... --resume-slice <id>` 실행 → test 단계만 돌고, 실제 `npm run test:*`가 실행되어 **345 pass**가 보고되며, `TEST_RESULT: PASS` → `state.json`이 `status: done`, `test_verdict: pass`로 끝나는지 확인
   - 같은 run의 `runs.json` → `run_dir` → `events.jsonl`에서 Bash tool_use가 실제로 성공했는지(승인 거부 문구가 없는지) 확인. 이게 결함 1이 실제로 고쳐졌다는 유일한 증거다.

---

## 실패 가능성과 대비

1. **`--allowedTools`가 additive가 아니라 restrictive인 CLI 버전** — 그렇다면 implement/test에서 Read/Edit까지 막혀 즉시 대량 실패한다. 5번 재검증에서 첫 turn부터 터지므로 바로 드러난다. 대비: 그 경우 규칙 목록에 `Read`, `Edit`, `Write`, `Glob`, `Grep`을 함께 넣거나 선택지 C(`--settings` 파일)로 전환. 계획을 A→C로 바꾸는 비용은 `allowed_tools_for`의 반환 형태만 바꾸는 수준으로 국지적이다.
2. **접두 규칙이 실제 명령과 어긋남** — 모델이 `npx vitest run`이나 `cd packages/x && npm test`를 고르면 여전히 막힌다. 프롬프트에서 체이닝 금지와 사전 승인 목록을 명시했고, 그래도 안 되면 `--allow-tool`로 개별 추가한다. **거부되면 FAIL로 정직하게 보고하라**는 지침 덕분에 조용한 통과로는 절대 끝나지 않는다.
3. **Windows `cmd /c` 인용** — `Bash(npm run test:*)`처럼 공백이 있는 인자는 `list2cmdline`이 따옴표로 감싸므로 괄호가 cmd에 노출되지 않는다. 공백 없는 규칙(`Bash(pytest:*)`)의 괄호는 cmd의 인자 위치에서 특수문자가 아니다. 어느 쪽이든 §결함1 검증의 argv 단언이 Windows에서 실제로 이 경로를 통과하므로 추측이 아니라 측정으로 확인된다.
4. **state.json 쓰기 지연의 이중 재시도** — 위 §결함2에 명시한 최악 20초. 기존 테스트를 깨지 않기 위한 의도적 선택이며 주석으로 남긴다.
5. **`live.json`을 잡은 리더로 인한 run 감속** — 즉사가 감속으로 바뀐다. 의도된 트레이드오프.
6. **재검증 slice의 `mutated` 플래그** — `run_pipeline`은 `state["mutated"]`가 없으면 stage 직전에 dirty 검사를 한다(`pipeline.py:1162-1165`). guard-inspect worktree에는 커밋 안 된 가드 테스트가 있으므로, 그 slice의 `state.json`에 `mutated: true`가 없으면 test 재실행이 "uncommitted changes"로 거부된다. **재검증 전에 해당 state.json을 먼저 읽어 확인**하고, 없으면 가드 테스트를 커밋한 뒤 재개한다. (코드로 우회하지 않는다 — 그 안전핀은 의도된 것이다.)
7. **`failures` 필드가 없는 기존 state.json** — 전부 `int(entry.get("failures") or 0)`로 읽으므로 0으로 취급되어 옛 동작(resume)과 같다. 스키마 버전을 올릴 필요 없다.
8. **프롬프트 변경이 stub의 stage 감지를 깨뜨리는지** — `detect_stage`는 프롬프트에서 `"PLAN STAGE"` 등 대문자 문자열을 찾는다(`fake_pipeline_claude.py:63-67`). 헤더는 그대로 두고 INSTRUCTIONS 하단에만 추가하므로 영향 없다. 신규 모드 추가 시 이 함수와 mode 분기 순서를 건드리지 않는다.
