# ai-dev-orchestrator

Claude Code 실행을 감싸서 **어디서 컨텍스트와 돈이 새는지** 측정하는 러너.
특정 프로젝트(Joker 등)에 종속되지 않는 독립 도구다. 다른 게임이든 MiniGPT든
`--repo`만 바꾸면 그대로 쓴다.

**v0.1 = Telemetry Runner.** 여기까지만 한다. 오케스트레이션/자동 분해는 v0.2부터.
현재 v0.1.2 (별도 터미널 실시간 관찰 `aidev watch` 포함).

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

## 데스크톱 IDE 셸 (desktop/, v0.0.1)

Electron + React + TypeScript 기반 AI Dev IDE의 첫 셸. Python 코어와 완전히 분리돼
있고, CLI 경로는 그대로 남는다.

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
v0.1  Telemetry Runner        ← 지금 여기
v0.2  Epic → Slice Planner
v0.3  Sequential Slice Runner
v0.4  PASS / FAIL / Retry / Resume
v0.5  Codebase Memory
v0.6  HTML Dashboard
```

v0.4에서 세션 정책: **같은 slice 실패 수정은 기존 session resume, 다음 slice는
새 session.** `--resume` 배선은 v0.1에 이미 들어가 있다.
