# ai-dev-orchestrator

Claude Code 실행을 감싸서 **어디서 컨텍스트와 돈이 새는지** 측정하는 러너.
특정 프로젝트(Joker 등)에 종속되지 않는 독립 도구다. 다른 게임이든 MiniGPT든
`--repo`만 바꾸면 그대로 쓴다.

**v0.1 = Telemetry Runner**(`aidev run`) **+ v0.2 = Slice Pipeline**(`aidev pipeline`).
현재 v0.2.0. 요구사항 .md 하나를 주면 plan → 승인 → implement → test를 사람 없이
진행하고, 정해둔 게이트에서만 승인을 기다린다. 에픽 자동 분해는 v0.4다.

```
aidev run
    ↓
claude -p --output-format stream-json --verbose --max-turns 80
    ↓
stream-json 한 줄씩 실시간 파싱
    ↓
raw event 원본 보존 (events.jsonl)
    ↓
tool / file access 집계
    ↓
session / cost / turn / time 기록 (telemetry.json + SQLite)
    ↓
terminal report
    ↓
aidev report <run-id>

(실행 중에는 다른 터미널에서 aidev watch)
```

## 설치

```bash
pip install -e ".[dev]"
```

의존성 없음(표준 라이브러리만). `claude` CLI가 PATH에 있어야 한다.

## 사용

```bash
aidev run \
  --phase implement \
  --repo ~/jokertest \
  --prompt tasks/doctor.md
```

`--prompt`는 현재 디렉터리 기준으로 먼저 찾고, 없으면 `--repo` 기준으로 찾는다.

주요 옵션:

| 옵션 | 설명 |
| --- | --- |
| `--phase` | `plan` / `explore` / `implement` / `review` / `test` / `repair` (기본 `implement`) |
| `--project`, `--task` | 라벨. 기본값은 repo 디렉터리명, 프롬프트 파일명 |
| `--max-turns` | 기본 80 |
| `--model` | claude에 그대로 전달 |
| `--permission-mode` | headless 실행 시 필요할 수 있음 (`acceptEdits` 등) |
| `--allowed-tools` | `--allowedTools`로 전달 |
| `--disallowed-tools` | `--disallowedTools`로 전달 (쉼표 구분) |
| `--safety` | `default` / `readonly`. readonly는 변경 계열 툴 전부 차단 |
| `--resume` | 기존 session id 이어받기 (v0.4 대비 배선만 해둠) |
| `--claude-bin`, `--claude-arg` | 실행 파일 교체 / 원시 인자 추가 |
| `--dry-run` | 실행할 커맨드만 출력 |
| `--no-live` | 라이브 패널 끄기 |
| `--data-dir` | 저장 위치 (기본 `$AIDEV_DATA_DIR` 또는 프로젝트의 `data/`) |

리포트:

```bash
aidev report last          # 마지막 실행
aidev report 20260813-19   # prefix / 부분 일치 가능
aidev report <run-id> --json
aidev list                 # 최근 실행 목록
aidev stats -n 20          # 최근 20회 phase 분포
```

## Slice Pipeline (v0.2)

요구사항 .md 하나를 단계 루프로 완주시킨다. 각 단계는 `execute()` 한 번이라
v0.1이 재던 것(events.jsonl / telemetry.json / live.json / SQLite)이 **단계마다
그대로** 남는다.

```
Requirement (.md)
    ↓
[plan]       safety readonly — 파일 변경 금지
    ↓        모델의 최종 메시지를 plan.md로 저장
게이트        approvals/plan.md에 "approved" / "rejected: 사유"를 쓸 때까지 대기
    ↓
[implement]  permission-mode acceptEdits, 프롬프트 = 요구사항 + 승인 시점의 plan.md
    ↓
[test]       프로젝트 테스트 실행 후 결과 보고 (고치지는 않는다)
    ↓          마지막 줄에 TEST_RESULT: PASS / FAIL 을 요구하고 그걸로 판정한다
단계별 토큰 / 비용 / 변경 파일 요약
```

```bash
aidev pipeline --repo ~/jokertest --requirement tasks/doctor.md
aidev pipeline --repo ~/jokertest --resume-slice last    # 중단된 slice 이어가기
aidev pipeline --repo ~/jokertest --list                 # slice 목록 / 상태
```

`--repo` 기본값은 현재 디렉터리다. run 단위 세션 재개인 `aidev run --resume`과
이름이 겹치지 않게 `--resume-slice`로 분리했다. 인자는 정확한 id → 유일한 prefix →
유일한 부분일치 순으로 찾고, **여러 개에 걸리면 고르지 않고 에러를 낸다** (남의
repo에서 엉뚱한 slice를 재개하는 게 더 나쁘다). `last`는 이름 사전순이 아니라
`updated_at` 기준 **가장 최근에 움직인** slice다.

### 승인 게이트

승인은 특정 단계의 기능이 아니라 **단계 사이마다 있을 수 있는 게이트**다. 루프는
매 단계가 끝날 때마다 "이 게이트가 켜져 있나"만 묻는다. 어느 게이트를 켤지는
요구사항 front matter가 정한다.

```markdown
---
approval: plan, implement    # 게이트 추가
# approval: none             # 완전 무인
---
# 밤 페이즈 의사 보호 로직
...
```

미지정이면 **plan 뒤 한 곳**이다. 오타(`approval: pan`)나 값이 빈 `approval:`은
무시하지 않고 에러를 낸다 — 게이트가 조용히 꺼진 채 무인 실행되는 쪽이 훨씬
위험하기 때문이다. 무인으로 돌리려면 `none`이라고 명시해야 한다. 값 뒤의
`# 주석`은 허용한다.

게이트에 걸리면 `state.json`이 `waiting_approval:<단계>`가 되고, 승인 파일이
없으면 설명이 들어간 템플릿을 만들어 둔다. 사람은 그 파일 아무 줄에나 쓴다:

```text
approved              → 다음 단계 진행
rejected: 사유         → slice 중단, 사유를 state.json에 기록
```

`#`으로 시작하는 줄은 무시하므로 템플릿 자체가 결정으로 읽히지 않는다.
승인 전에 `plan.md`를 직접 고쳐도 된다 — implement는 **승인 시점의** plan.md를 읽는다.
대기 중 프로세스를 죽여도 승인 파일이 곧 기록이라, `--resume-slice`로 이어가면
그 답을 그대로 읽고 계속한다.

### 저장 위치

run 상세는 도구의 `data/`에, slice 상태는 **대상 repo를 따라다녀야 하므로**
프로젝트 안에 둔다.

```
<대상repo>/.aidev/slices/20260815-doctor/
├── requirement.md   원본 사본
├── plan.md          plan 산출물 (승인 전 사람이 고쳐도 된다)
├── state.json       진행 상태의 유일한 원천 (단일 writer = pipeline 프로세스)
├── approvals/
│   └── plan.md      사람이 쓰는 승인 파일
└── runs.json        단계 → run_id (상세는 data/runs/로 연결)
```

`state.json`은 live.json에서 검증된 규칙을 그대로 쓴다: 단일 writer,
`.tmp` → `os.replace` atomic 쓰기, reader는 깨진 파일에 관용적. slice status는
`running:<단계>` / `waiting_approval:<단계>` / `quota_wait` / `done` / `rejected` /
`failed`, stage status는 `pending` / `running` / `done` / `failed`다. **stages의 키
집합은 고정이 아니다** — 나중에 단계가 늘어도 reader는 모르는 키를 그대로 표시해야 한다.

Windows에서는 reader가 파일을 열고 있는 것만으로 `os.replace`가 WinError 5로 죽는다
(CPython의 `open()`이 delete 공유를 주지 않는다). 지연이 아니라 순간 충돌이므로
`write_json_atomic`이 최대 1초(20회 × 0.05초) 다시 시도하고, 그래도 안 되면 예외를
그대로 올린다 — 삼키지 않는다. `state.json`, `runs.json`, `live.json` 전부에 적용된다.

### 테스트 판정

`claude` 프로세스는 테스트가 빨갛게 떠도 "정상 종료"한다. 그래서 exit code로는
성공을 판정할 수 없다. test 단계에는 마지막 줄에 `TEST_RESULT: PASS` 또는
`TEST_RESULT: FAIL`을 쓰라고 요구하고, 그 값을 `state.json`의
`test_verdict`(+ `stages.test.verdict`)에 남긴다.

- `FAIL` → slice는 **failed**로 끝난다 (exit 1).
- 줄이 아예 없으면 `unknown`으로 기록하고 요약에 표시한다. 아무 주장도 없는 걸
  실패로 단정하지는 않지만, **절대 통과로도 읽히지 않는다.**

### 실패와 쿼터

```text
쿼터류 실패 → state = quota_wait → 대기 후 같은 session으로 --resume 재시도
일반 실패   → state = failed, slice 중단 (자동 repair 루프는 이후 버전)
```

대기 시간은 2순위다. 에러에서 reset 시각을 파싱할 수 있으면 그때까지 기다리고,
못 읽으면 고정 간격(기본 15분)으로 재시도한다. 상한은 기본 20회다.
**쿼터 감지 조건은 잠정값이다** — 실제 한도에 걸렸을 때 events.jsonl / stderr.log에
오는 문구를 실측한 뒤 `QUOTA_MARKERS`를 갱신한다. 오탐을 줄이려고 스트림 전체가
아니라 **마지막 result 이벤트와 stderr만** 본다 (모델이 rate limit을 *말하는* 것과
실제로 걸리는 것은 다르다).

세션 정책은 **왜 멈췄는지**로 갈린다. 쿼터 대기는 세션이 *중단*된 것뿐이라 같은
session을 resume한다. 반면 일반 실패는 세션이 이미 "막혔다 / FAIL"이라는 **결론**을
갖고 있어서, 그 세션을 물려주면 환경이 바뀌어도 낡은 기억으로 몇 턴 만에 같은
결론을 재확정한다(실측). 그래서 같은 단계가 비쿼터 실패를 `--session-reset-after`회
(기본 1회) 연속하면 재시도는 **새 session**으로 시작하고, 프롬프트에 "이전 시도의
기억이 없으니 지금의 repo를 보고 판단하라"고 명시한다. 다음 단계는 언제나 새 session이다.

첫 시도 도중 프로세스가 죽은 경우(`failures`가 0인데 session_id는 있음)는 *진행 중인
작업*이므로 그대로 resume한다. 옛 동작이 필요하면 `--session-reset-after`를 크게 준다.

### 안전핀

- 대상 repo가 dirty면 거부한다. 시작할 때만이 아니라 **이 slice가 아직 아무것도
  고치지 않았다면 매 단계 직전에 다시 확인한다.** 승인 게이트는 몇 시간씩 열려
  있을 수 있고, 그 사이 사람이 편집한 작업 위에 implement가 올라가면 안 된다.
  일단 implement가 시작된 뒤로는 repo가 더러운 게 정상이므로 검사하지 않는다.
  **v0.3 worktree 격리 전까지의 임시 제한**이다.
- plan 단계는 `readonly` 프로파일로 돌고, 끝난 뒤 `git status`로 **실제로** 변경이
  없었는지 사후 검증한다. 이때 `.aidev/` 전체를 면제하지 않는다 — 면제하면 plan이
  **자기 승인 파일(`approvals/plan.md`)을 위조**해도 못 잡는다. 파이프라인이
  단계 중에 직접 쓰는 `state.json` / `runs.json` / `.lock`만 제외한다.
  (untracked 디렉터리가 한 줄로 접히지 않도록 `git status -uall`을 쓴다)
- **slice 하나당 프로세스 하나.** `.aidev/slices/<id>/.lock`을 O_EXCL로 잡는다.
  같은 slice를 두 번 돌리면 두 번째는 거부된다 (exit 2). state.json의 단일 writer
  규약이 규약이 아니라 강제가 된다. 프로세스가 강제 종료돼 lock이 남으면 지우라고
  경로를 알려준다.
- `--permission-mode`는 implement/test에만 먹는다. plan은 무슨 값을 줘도 readonly다.
- **테스트 명령은 규칙 단위로만 허용한다.** `acceptEdits`는 편집만 자동 승인하고
  Bash는 여전히 승인 대상이라, 규칙이 없으면 test 단계가 테스트를 **한 번도 못 돌린
  채** 끝난다(v0.2 실측). 그래서 implement/test에는 `--allowedTools`로 아래 규칙만
  얹는다. `--dangerously-skip-permissions` 같은 무제한 Bash는 쓰지 않는다.

  ```text
  Bash(npm test)   Bash(npm test:*)   Bash(npm run test:*)
  Bash(node --test)   Bash(node --test:*)
  Bash(pytest)   Bash(pytest:*)   Bash(python -m pytest:*)
  ```

  대상 repo의 `.claude/settings.json`은 **건드리지 않는다** — 파이프라인이 tracked
  파일을 만들면 dirty 검사와 정면으로 충돌한다. 사람이 영속 규칙을 원하면
  `.claude/settings.local.json`을 직접 두면 되고, 위 규칙은 거기에 *더해진다*.
  다른 명령이 필요하면 `--allow-tool 'Bash(npx vitest:*)'`처럼 추가한다.
  같은 목록이 프롬프트에도 그대로 들어가므로, 모델이 아는 명령과 실제로 허용된
  명령이 어긋날 수 없다.
- 단계별 `--max-turns` 기본 80.

주요 옵션:

| 옵션 | 설명 |
| --- | --- |
| `--requirement` | 요구사항 .md (cwd → repo 순으로 찾는다) |
| `--resume-slice` | 중단된 slice 이어가기 (id / prefix / `last`) |
| `--list` | slice 목록과 상태 |
| `--max-turns` | 단계별 상한 (기본 80) |
| `--permission-mode` | implement/test용 (plan은 항상 readonly) |
| `--allow-tool` | implement/test에 추가할 권한 규칙 (반복 가능) |
| `--session-reset-after` | 같은 단계가 비쿼터 실패 N회면 새 session (기본 1) |
| `--poll-interval` | 승인 파일 polling 간격 (기본 3초) |
| `--approval-timeout` | 승인 대기 포기 시간 (기본 0 = 무한 대기) |
| `--quota-wait` | reset 시각을 못 읽을 때의 재시도 간격 (기본 900초) |
| `--quota-max-retries` | 쿼터 재시도 상한 (기본 20) |
| `--dry-run` | slice id / 게이트 / 경로만 출력하고 종료 |

종료 코드: `0` 완주, `1` 실패, `2` 사용법·전제조건 위반, `3` 거부, `127` claude 없음.

## 다른 터미널에서 관찰 (v0.1.2)

```bash
aidev watch              # 현재 RUNNING인 최신 run에 자동으로 붙는다
aidev watch last         # 위와 동일
aidev watch 20260813-22  # 특정 run (prefix 가능)
aidev watch <run-id> --once      # 한 프레임만 찍고 종료
aidev watch --interval 0.5       # 0.3~0.5로 clamp
```

```
AI DEV WATCH   20260813-221956-doctor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Project   jokertest
Task      doctor
Phase     IMPLEMENT
Status    RUNNING

Session   slow-session
Turn      2
Elapsed   00:02
Cost      -

Tokens  EXACT, PROVISIONAL
 in                  40
 cache w            200
 cache r          2,500
 out                 80
 peak ctx         1,620

Tools
 Read           2

Largest reads
 src/gameSession.js                  19 KB

Repeated reads
 src/gameSession.js                     ×2

Attribution (estimated)
 Code exploration     100%    ~4.8k
 Other                  0%      ~18
 attributable                ~4.8k

> Read src/gameSession.js

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
updated  0.2s ago
Ctrl+C stops watching only - the run keeps going
```

**단일 writer 원칙.** telemetry 계산은 runner만 한다. watcher는 `live.json`을 읽어
그리기만 하고, run 디렉터리에 아무것도 쓰지 않는다.

```
runner  ──write──>  live.json  <──read──  watcher (N개 가능)
```

- **PROVISIONAL** — result event 도착 전 토큰 값은 지금까지 온 assistant 메시지
  누적치일 뿐이다. 헤더에 명시한다. result가 도착하면 reconcile 후
  **FINAL EXACT**로 바뀌고, run 종료 시 마지막 프레임에 이어 최종 리포트를 찍고
  watcher가 스스로 종료한다.
- **깨진 파일에 죽지 않는다** — watcher가 읽는 순간 runner가 파일을 교체 중일 수
  있다. 파일 없음/잠김/잘림/JSON 아님 전부 `None`으로 처리하고 직전 프레임을
  유지한다. 반대편도 방어한다: 모든 JSON은 `.tmp`에 쓰고 `os.replace`로 바꿔서
  reader는 이전 완전본 아니면 다음 완전본만 본다.
- **Ctrl+C는 watcher만 멈춘다** — 별도 프로세스이므로 runner는 영향이 없다.
  watcher를 `kill -9` 해도 runner는 끝까지 돈다.
- **STALE 감지** — runner가 죽어서 `live.json`이 RUNNING인 채로 멈추면 10초 후
  `STALE: no update for Ns`로 표시한다.
- run 종료 시 `live.json`은 **telemetry.json을 쓴 다음에** 마지막으로 갱신한다.
  watcher가 terminal status를 본 시점엔 최종 리포트가 반드시 디스크에 있다.

## 실행 중 화면

```
AI DEV RUNNER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Project   joker
Task      doctor
Phase     IMPLEMENT
Status    RUNNING

Session   9df1c2ab3f…
Turn      6
Elapsed   02:48
Cost      -

Tokens  EXACT, PROVISIONAL
 in                 1,204
 cache w           18,300
 cache r          412,900
 out                9,120
 peak ctx          63,240

Tools
 Read          19
 Bash           7
 Edit           4

Largest reads
 …/src/game/gameSession.js         42 KB
 …/src/game/gameSession.test.js    37 KB

Repeated reads
 …/src/game/gameSession.js         ×5

Attribution (estimated)
 Code exploration      38%   ~52.1k
 Implementation        27%   ~37.0k
 Test output           17%   ~23.3k
 attributable               ~137.1k
```

TTY가 아니면 자동으로 한 줄짜리 로그 모드로 떨어진다. result event가 도착하면
헤더가 `Tokens  FINAL EXACT`로 바뀐다 — runner 패널과 watcher가 같은 규칙을 쓴다.

## 실행 후 리포트

```
TOKEN / CONTEXT HOTSPOTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Code exploration        38%   ~52.1k  ███████·············
2. Implementation          27%   ~37.0k  █████···············
3. Test output             17%   ~23.3k  ███·················
4. Model output            11%   ~15.1k  ██··················
5. Other                    7%    ~9.6k  █···················

Estimated attributable tool context   ~137.1k

EXACT vs ESTIMATED

 Peak context (exact)            63.2k
 Attributable tools (est.)      ~52.3k
 Unattributed gap                10.9k   (17%)
 Billed input (cumulative)      432.4k

 note: gap = system prompt, tool schemas, CLAUDE.md, conversation scaffolding

Suspected waste

 src/game/gameSession.js
   read × 7   91 KB returned   (~19.5k repeated)
 npm test
   run × 3   184 KB output   (~31.2k repeated)

Estimated avoidable context
  ~31k tokens
```

## 세 가지 신뢰 등급

숫자를 절대 섞지 않는다. `telemetry.json`도 이 구조 그대로 저장된다.

| 등급 | 내용 | 출처 |
| --- | --- | --- |
| **EXACT** | session_id, num_turns, duration_ms, duration_api_ms, total_cost_usd, exit_code, **token usage** | Claude Code가 직접 보고 |
| **OBSERVED** | 툴별 호출 횟수, 읽은 파일, tool result 바이트, 테스트 실행 횟수, 에러 result, 중복 이벤트 | 이벤트 스트림에서 카운트 |
| **ESTIMATED** | 버킷별 귀속 컨텍스트, 반복 read 낭비, 회피 가능 토큰 | 바이트 → 토큰 환산 (≈4 chars/token) |

파일별 토큰은 **추정**이다. Claude Code 이벤트가 파일 단위 토큰 귀속을 주지
않으므로, 반환된 바이트 수로 환산한다. 그래서 리포트에 `~`를 붙인다.

### EXACT token usage

assistant 메시지마다 오는 `usage`를 필드별로 실시간 누적한다.

- `input_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` / `output_tokens`
- **peak context** = 한 요청의 `input + cache creation + cache read` 중 최대값.
  실제로 모델에 들어간 컨텍스트 크기.
- **billed input** = 턴마다 재전송된 것까지 포함한 누적 입력. peak과 다르다.

**request_id 중복 제거** — 같은 assistant 메시지가 두 번 오면(재시도, 세션 재개,
transcript replay) usage도 turn도 tool 호출도 두 번 세면 안 된다. `request_id` →
`message.id` → `uuid` 순으로 키를 잡아 이미 본 메시지는 통째로 버리고
`duplicate_messages`로만 카운트한다. 셋 다 없으면 판단 불가이므로 **버리지 않는다**
(누락보다 중복이 낫다). tool_use id 중복도 따로 막는다.

**reconciliation** — 실행이 끝나면 누적값을 최종 `result.usage`와 대조한다.
result가 있으면 그쪽이 authoritative, 없으면 누적값이 authoritative. 차이가 나면
필드별 delta를 `telemetry.json`에 남기고 리포트 하단에 note를 찍는다.

### EXACT vs ESTIMATED gap

```
Peak context (exact)        63.2k     ← 진짜 컨텍스트 크기
Attributable tools (est.)  ~52.3k     ← 우리가 툴 트래픽으로 설명 가능한 몫
Unattributed gap            10.9k     ← 나머지 (17%)
```

gap은 system prompt, tool schema, CLAUDE.md, 대화 스캐폴딩처럼 이벤트 스트림에서
귀속시킬 수 없는 실제 컨텍스트다. 양쪽 다 **1회 컨텍스트 크기**라 비교가 성립한다
(누적 billed input과 비교하면 안 된다). 추정이 peak을 넘으면 `Estimate overshoot`
으로 뒤집어 표시한다 — 바이트→토큰 휴리스틱이 과대평가했다는 신호다.

hotspot 버킷 정의 (중복 집계 없음):

- **exploration** — Read/Grep/Glob/WebFetch 결과 바이트
- **implementation** — Edit/Write 툴 입력 + 결과 바이트
- **test output** — 테스트로 판정된 Bash 명령의 출력
- **model output** — 모델의 text/thinking 출력 (tool_use 입력은 제외)
- **other** — 나머지 Bash 출력과 기타 툴

## Safety profile

```bash
aidev run --safety readonly --phase review --repo ~/jokertest --prompt tasks/audit.md
```

`readonly`는 `--disallowedTools`로 `Bash, BashOutput, KillBash, KillShell, Edit,
MultiEdit, Write, NotebookEdit, Task, SlashCommand`를 막는다. Task까지 막는 이유는
서브에이전트가 Bash/Edit 권한을 그대로 물려받기 때문이다. `--disallowed-tools`로
준 목록은 프로파일 목록 뒤에 중복 없이 합쳐진다.

차단은 Claude Code가 하는 것이고 우리가 하는 게 아니다. 그래서 실행이 끝나면
**실제로 변경 계열 툴이 돌았는지 사후 검증**해서, 걸리면 리포트 최상단에
`SAFETY VIOLATION`을 띄우고 `observed.safety_violations`에 남긴다.

## 저장 구조

```
data/
├── aidev.db
└── runs/
    └── 20260813-194500-doctor/
        ├── run.json        실행 설정 + 커맨드 + 상태
        ├── events.jsonl    stream-json 원본 (파싱 전에 먼저 기록)
        ├── telemetry.json  EXACT / OBSERVED / ESTIMATED
        ├── live.json       실행 중 실시간 스냅샷 (watcher용, atomic replace)
        ├── prompt.md       사용한 프롬프트 사본
        └── stderr.log
```

`events.jsonl`은 **파싱하기 전에** 원본 그대로 기록한다. 파서에 버그가 있어도
나중에 raw event로 다시 분석할 수 있다. JSON이 아닌 줄도 그대로 남고
`malformed_lines`로 카운트된다.

pipeline이 만든 slice 상태는 여기가 아니라 **대상 repo의 `.aidev/slices/`** 에
들어간다 (위 [Slice Pipeline](#slice-pipeline-v02) 참고). 도구의 `data/`는 run
상세, 대상 repo의 `.aidev/`는 프로젝트를 따라다녀야 하는 진행 상태다.

SQLite 테이블(전체 실행 비교용): `runs`, `tool_calls`, `file_accesses`, `phases`.
`runs`에는 실행별 exact token(input / cache creation / cache read / output /
peak context)과 attributable / gap 토큰도 같이 들어간다. 예전 스키마로 만들어진
db는 열 때 자동으로 컬럼이 추가된다.

## 테스트

```bash
pytest
```

`tests/fake_claude.py`가 실제 `claude` 대신 stream-json을 뱉는 스텁이라, CLI
전 구간을 API 비용 없이 검증한다. 스텁은 일부러 **파싱 불가능한 줄 하나와
request_id가 같은 중복 메시지 하나**를 섞어 보낸다 — 둘 다 회귀 테스트 대상이다.

pipeline은 `tests/fake_pipeline_claude.py`를 쓴다. 환경변수로 단계별 응답과
실패 모드(쿼터 / 일반 실패 / readonly 위반)를 지시할 수 있고, 호출마다 argv와
프롬프트를 로그로 남겨서 **어느 단계에 어떤 플래그와 프롬프트가 갔는지**까지
검증한다. 승인 대기·거부·중단 후 재개·쿼터 재시도가 전부 테스트에 있다.

## Pluto IDE — 데스크톱 셸 (desktop/, v0.0.1)

Electron + React + TypeScript 기반 첫 셸. Python 코어와 완전히 분리돼 있고,
CLI 경로는 그대로 남는다. 앱 아이콘은 `png/pluto.png`에서 정사각 크롭해
`desktop/resources/icon.png`와 `desktop/build/icon.{ico,icns,png}`로 만든다.

```bash
cd desktop
npm install
npm run dev        # Electron 개발 실행
npm run build      # typecheck + electron-vite build
```

`@quick-start/create-electron`의 `react-ts` 템플릿으로 생성했다. renderer는 OS 권한을
직접 갖지 않는다 — `sandbox: true` / `contextIsolation: true`에서 preload가 노출하는
읽기 전용 capability API(`window.aidev`)만 쓴다.

**이 단계의 데이터는 전부 main 프로세스가 소유한 mock이다.** IPC 경계의 모양만
확정했고 `aidev` 코어 연동은 아직 없다. Code Graph도 좌표가 손으로 적힌 mock이며,
레이아웃 엔진은 v0.0.4에서 만든다.

> Windows에서 VS Code 내장 터미널로 실행하면 `ELECTRON_RUN_AS_NODE=1`이 상속돼
> Electron이 plain Node로 떠서 죽는다. 그 터미널에서는 변수를 지우고 실행한다.

## 로드맵

```
v0.1  Telemetry Runner            ← 완료
v0.2  Slice Pipeline              ← 지금 여기
v0.3  Workspace 격리               (worktree / 브랜치 / 단계별 커밋)
v0.4  Epic → Slice Planner
v0.5  Codebase Memory
v0.6  Pluto IDE 바인딩 (state.json / live.json → window.aidev)
```

Planner(에픽→slice 자동 분해)를 뒤로 미룬 이유: 그건 지금 사람이 직접 해도
진행이 되지만, Pipeline(무인 실행 루프)이 없으면 사람이 자리를 비우는 순간 모든
작업이 멈춘다. 병목부터 풀었다. 구 로드맵의 "HTML Dashboard"는 Pluto IDE가
같은 화면을 담당하므로 제거했다.

세션 정책: **쿼터 대기는 기존 session resume, 결론을 낸 실패 뒤의 재시도는 새 session,
다음 단계는 언제나 새 session.**
v0.2의 쿼터 재시도가 이 정책을 그대로 쓴다.
