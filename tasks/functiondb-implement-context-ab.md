---
approval: plan
max_turns: implement=220
test_commands: python -m pytest -q
---
# Function DB 구현 컨텍스트 트레이 + 재현 가능한 A/B 계측

## 실행 범위 — Pluto 마스터 큐 v2(8/21 동결) 2번의 우산 명세

이 문서는 하나의 slice로 발사하는 요구사항이 아니다. 마스터 큐의 2-1~2-4를 위한
공통 설계·위협·계측 명세다. 반드시 아래 다섯 실행 단위로 나눠 순서대로 실행한다.
하나의 slice가 다음 slice 책임이나 보류 항목을 선행 구현하지 않는다.

| 큐 | 이번 구현 범위 | 이 문서의 대응 절 |
|---|---|---|
| 2-1 작업 지시서 | CREATE/MODIFY/REFERENCE 양식, 경로·symbol 기계 검증, 승인 전 실패 | §1, §11의 target 경로 검증 |
| 2-1b Plan Reviewer | 2-1 산출물을 타 모델 체크리스트로 검토. 별도 기존 F2 요구사항 사용 | 이 문서에서 구현하지 않음 |
| 2-3 계측+JS/TS fixture | shell/MCP I/O 분류, unclassified_io, JS/TS parser 회귀 fixture | §7, §10의 fixture 항목 |
| 2-2 컨텍스트 트레이 | 대상별 결정론적 트레이, token cap, graph 신선도, context escape 계측 | §3~§5, §10의 runtime eligibility |
| 2-4 A/B runner | 빈 파일 준비, component/product 모드 분리, 자동 품질 원자료, freeze, 격리 lock, 누적 cap | §2, §6, §8의 자동 품질, §9, §11~§13 |

각 child requirement는 직전 slice의 실제 API·파일·테스트를 다시 확인해 구체화한다.
실행 순서는 `2-1 → 2-1b → 2-3 → 2-2 → 2-4`로 고정한다. 2-3 parser fixture가
통과하기 전에는 2-2의 JS/TS/TSX treatment와 조커 graph build를 발사하지 않는다.
파이썬 treatment는 이 게이트와 별개로 유효하다. 미검증 parser 데이터를 정상 treatment로 쓰지 않는다.
2-4 runner 구현은 fake/smoke까지만 완료 조건으로 삼고 실제 유료 실험을 자동 발사하지 않는다.

다음 항목은 마스터 큐의 **보류 — 소환 조건부**이며 이번 4개 slice에서 구현하지 않는다.

- blind review·사람 review A/B: 조커 reviewer 2인 확보 뒤
- 7일/30일 후행 결함 추적: 파일럿 시작 뒤
- 실제 end-to-end 유료 실행: 조커 안정화 에픽에서
- W0/C0~W1/C1 2x2 원인 분해 실험: component/product 1차 결과 뒤
- bootstrap CI·power analysis 등 통계 완전판: 유효 표본이 쌓인 뒤
- language별 수동 gold set: 현재는 고정 parser fixture로 대체
- hotfix 경로, Observer 실전, 멀티 프로바이더, One-File 팀 스케일, Fleet

아래 상세 명세에 보류 항목의 최종 측정 기준이 남아 있어도 현재 child slice의 구현
승인으로 해석하지 않는다. 현재 구현 여부는 이 절의 큐 매핑이 항상 우선한다.

## 배경 (확인된 사실과 이번 가설)

현재 Function DB 브리핑은 plan 단계에서 효과가 확인됐다.
`small-fixes-1` plan은 Read 2회, exploration 6.5%, 반복 읽기 0을 기록했다.
반면 implement는 아직 Function DB를 적극적으로 사용하는 구조가 아니며,
이전 implement 결과를 이 기능의 성과로 간주하지 않는다.

이번 가설은 다음 한 문장이다.

> 승인된 단순 작업 지시서를 기준으로 새 대상은 빈 코드 파일로 준비하고,
> Function DB가 대상별 앞뒤 함수 맥락을 결정론적으로 차려주면,
> 동일 품질의 구현에 필요한 탐색·재탐색·전체 토큰·비용이 감소한다.

토큰 절감만으로 성공 판정하지 않는다. 더 적은 맥락 때문에 에이전트가
자신 있게 틀리는 경우를 막고, 품질·범위 준수·재작업까지 함께 계측한다.

## 설계 원칙

- 규약 강도는 기계 차단 > 고정 양식 > 프롬프트 지시 순서다.
- 임베딩·시맨틱 검색·LLM 분류를 사용하지 않는다. 동일 입력은 동일한
  함수 목록과 동일한 정렬을 만들어야 한다.
- plan은 거대한 함수 계약서로 만들지 않는다. 사람이 읽는 단순 작업
  지시서를 유지하고, Function DB가 plan과 implement 사이의 맥락을 조립한다.
- 컨텍스트 기본 범위는 닫혀 있지만, 누락을 복구할 조회 탈출구는 남긴다.
  범위 밖 쓰기는 기존 write guard와 승인 절차 없이 허용하지 않는다.
- 실험 실패·재시도·중단도 결과다. 성공한 실행만 골라 집계하지 않는다.

## 용어

- **작업 지시서**: plan.md 안의 짧고 기계 파싱 가능한 구현 항목 목록.
- **대상 파일**: 작업 지시서에서 CREATE 또는 MODIFY로 지정한 파일.
- **빈 대상 파일**: CREATE 대상으로 선언되어 implement 발사 전에 엔진이
  생성한 0 byte 코드 파일. 함수 골격이나 가짜 구현을 넣지 않는다.
- **컨텍스트 트레이**: 대상 파일 하나에 연결된 관련 함수·타입·테스트의
  좌표와 요약 목록. 원본 코드 파일과 분리된 slice 산출물이다.
- **context escape**: 트레이에 없던 파일·함수의 본문을 세션이 추가 조회한 것.
- **A(control)**: 동일 plan·동일 빈 대상 파일을 사용하되 implement 컨텍스트
  트레이를 프롬프트에 넣지 않는 실행.
- **B(treatment)**: 동일 조건에서 Function DB 컨텍스트 트레이를 넣는 실행.

## 요구사항

### 1. plan의 단순 작업 지시서

- plan 프롬프트가 기존 자유 서술 계획에 아래 절을 추가하도록 한다.

```md
## 작업 지시서
- CREATE `path/to/new_file.py` — 이 파일이 맡을 책임
- MODIFY `path/to/existing.py::qualified_symbol` — 바꿀 책임
- REFERENCE `path/to/context.py::qualified_symbol` — 참조 이유
```

- 허용 동사는 `CREATE`, `MODIFY`, `REFERENCE` 세 개뿐이다.
- 한 항목은 경로/선택적 symbol/책임 한 줄만 가진다. MODIFY에서 symbol을
  생략하면 기존 파일 안에 새 함수를 추가하는 작업으로 해석한다. YAML 계약, 함수 몸체,
  상세 의사코드, 장문 설명을 요구하지 않는다.
- plan 승인 전에 엔진이 다음을 기계 검증한다.
  - 모든 경로가 repo 내부의 정규화된 상대 경로다.
  - CREATE 대상은 존재하지 않고 MODIFY/REFERENCE 대상은 존재한다.
  - 같은 경로·symbol에 충돌하는 동사가 없다.
  - 명시된 MODIFY/REFERENCE symbol은 Function DB에서 정확히 하나로 해석된다.
    symbol이 생략된 MODIFY는 파일 좌표와 같은 모듈의 기존 함수를 맥락 기준으로 쓴다.
  - 최소 하나의 CREATE 또는 MODIFY 항목이 있다.
- 검증 실패는 implement를 발사하지 않고 좌표가 포함된 명시적 오류로
  plan 게이트에 돌려보낸다. 추정으로 보정하지 않는다.

### 2. 빈 대상 파일 준비

- plan 승인 후, implement 세션 발사 전에 CREATE 대상만 실제 worktree에
  0 byte 파일로 생성한다. 부모 디렉터리는 repo 내부에서만 생성한다.
- 이미 존재하는 파일을 비우거나 덮어쓰지 않는다. 승인 이후 경합으로
  대상이 생겼다면 중단하고 사람에게 보고한다.
- A와 B 모두 동일한 빈 대상 파일을 받아야 한다. 빈 파일 효과와 Function DB
  컨텍스트 효과를 섞지 않는다.
- 준비 작업은 idempotent해야 한다. 크래시 후 resume 시 `prepared_targets`
  ledger와 파일 hash를 대조하여 같은 빈 파일만 재사용하고, 내용이 생겼거나
  hash가 다르면 덮어쓰지 않고 중단한다.
- 엔진이 만든 빈 파일 경로·시각·hash를 state와 실험 산출물에 기록한다.

### 3. 대상별 Function DB 컨텍스트 트레이

- 작업 지시서 항목마다 별도 트레이를 만들고 다음 순서로 채운다.
  1. 지시서에서 직접 명명한 MODIFY/REFERENCE 함수
  2. 직접 함수의 resolved caller/callee 1 hop
  3. 직접 함수 시그니처에 등장하는 타입·파라미터·주요 변수·상수
  4. 직접 함수 또는 대상 파일과 연결된 테스트
  5. 같은 모듈의 진입점과 export 좌표
- 각 항목에는 최소한 `repository`, `commit`, `file:line`, qualified name,
  signature, 기능 설명, 관계 종류, source hash를 포함한다.
- 함수 본문은 기본 트레이에 통째로 넣지 않는다. 좌표·시그니처·설명·관계를
  먼저 제공하고 에이전트가 필요할 때 `aidev graph show/callers/calls`로 조회한다.
- unresolved/ambiguous call edge는 사실처럼 포함하지 않는다. 별도
  `UNRESOLVED` 절에 원문과 이유만 표시한다.
- 정렬은 `직접 명명 > 관계 종류 > hop > 경로 > line > qualified name`으로
  고정한다. 집합 순회나 SQLite 반환 순서에 기대지 않는다.
- 대상별 토큰 상한과 전체 implement 트레이 상한을 둔다. 상한 초과 시
  줄 중간을 자르지 않고 우선순위가 낮은 항목을 제외하며, 전체 후보 수,
  포함 수, 제외 수와 제외 이유를 기록한다.
- 저장 위치는 slice 내부의 별도 산출물로 한다. 예:
  `.aidev/slices/<slice>/context/implement/<target-id>.md`.
  함수 목록을 원본 빈 코드 파일의 주석으로 넣어 소스 코드를 오염시키지 않는다.
- implement 프롬프트에는 해당 트레이와 다음 규약을 넣는다.

> 트레이의 좌표와 기존 함수를 먼저 사용한다. 원본 파일 통읽기 전에 함수
> 조회를 사용한다. 트레이 밖 읽기는 허용되지만 계측된다. 작업 지시서 밖
> 쓰기는 승인된 scope 변경 없이는 허용되지 않는다.

### 4. 그래프 신선도와 근거

- 트레이 생성 직전에 Function DB의 base commit, worktree HEAD, dirty marker를
  비교한다. 불일치 또는 dirty면 증분 갱신 후 생성한다.
- 갱신 뒤에도 현재 worktree를 증명할 수 없으면 B implement를 발사하지 않는다.
  정상 실행에서 조용히 오래된 트레이로 fallback하지 않는다.
- 실험 모드에서 트레이 생성 실패는 B를 A처럼 실행하지 않는다. 해당 pair를
  `invalid_treatment`로 기록하여 효과 집계에서 분리한다.
- 트레이 sidecar에 graph base commit, worktree HEAD, dirty 상태, DB schema
  version, 생성기 version, 생성 시각을 기록한다.
- cache key에는 기존 stage/commit/requirement/plan 외에 작업 지시서 digest,
  graph schema version, 생성기 version, 대상 파일 준비 상태를 포함한다.

### 5. 누락 맥락·오염·범위 이탈 방어

- context escape를 완전히 차단하지 않는다. 차단하면 누락된 맥락 때문에
  더 적은 토큰으로 더 자신 있게 틀릴 수 있다.
- 기존 telemetry의 file access와 graph query를 이용해 다음을 기록한다.
  - 트레이 안/밖 source Read 수와 반환 bytes
  - 추가 graph show/callers/calls/summaries 횟수
  - 최초 context escape가 발생한 turn
  - escape 대상 파일·symbol과 최종 변경 여부
- write guard는 CREATE/MODIFY 대상 밖 변경을 차단한다. 추가 쓰기가 필요하면
  기존 amend/approval 흐름으로 작업 지시서를 갱신한 뒤 재개한다.
- implement 종료 시 계획 대상과 실제 변경 파일·함수를 비교한다. 여기서
  `planned`는 CREATE/MODIFY 쓰기 대상이며 REFERENCE는 포함하지 않는다.
  `planned`, `changed`, `planned_and_changed`, `unplanned_changed`,
  `planned_but_unchanged` 집합을 모두 보존한다.
- 트레이가 커져 자체 오염원이 되는 것을 막기 위해 prompt 전체에서 트레이가
  차지한 tokens와 각 절 tokens를 별도로 기록한다.

### 6. 재현 가능한 implement A/B 실행

- CLI에 다음과 동등한 headless 진입점을 제공한다. 정확한 내부 모듈 분리는
  plan에서 결정하되, A/B orchestration을 `pipeline.py`에 다시 크게 넣지 않는다.

```text
aidev pipeline --repo <repo> --requirement <task.md> --ab implement --repeats 3
```

- **구성요소 실험(component A/B)**은 plan을 한 번만 실행·승인하고 plan.md와 작업 지시서를
  freeze한다. 각 A/B 실행은 같은 base commit과 같은 frozen plan에서 만든
  별도 worktree에서 시작한다.
- 고정 조건:
  - stage별 model(Fable 5/Opus 5 등)과 effort
  - max_turns, timeout, auto-recovery/auto-extend 정책
  - system prompt, CLAUDE.md, 허용/차단 tool, test_commands
  - OS·의존성 lockfile·환경 변수 이름 목록(값은 저장 금지)
  - base commit, requirement, frozen plan, 빈 대상 파일
- 구성요소 실험의 유일한 treatment 차이는 implement 컨텍스트 트레이 주입 여부다.
  A/B 모두 동일한 작업 지시서와 동일한 빈 대상 파일을 받는다. 이 결과는
  "컨텍스트 트레이의 효과"로만 표기하고 Pluto 전체 효과로 표기하지 않는다.
- 실행 순서는 repeat마다 결정론적으로 A/B 또는 B/A를 교차하여 시간대·캐시
  편향을 줄이고, 실제 순서를 기록한다.
- 각 arm은 반드시 별도 worktree와 별도 Claude 세션을 사용한다. 대화 history,
  briefing cache, 생성 파일을 서로 재사용하지 않는다.
- 모든 arm의 성공·실패·usage limit·max turns·resume·repair를 그대로 집계한다.
  실패 실행을 자동 재실행해 성공 결과로 대체하지 않는다. 재실행은 새 attempt다.
- 기존 `briefing: off`의 byte-for-byte 대조군 성질을 보존한다. implement만
  격리하기 위한 front matter/실행 옵션 `implement_context: on|off`를 추가하고,
  기존 requirement에서 생략 시 현재 동작을 깨지 않는 기본값을 plan에서 명시한다.
- runner는 **제품 실험(end-to-end A/B)** 모드를 구성요소 실험과 별도로 표현한다.
  A는 현재 출시 기준 pipeline,
  B는 작업 지시서 생성 + 빈 대상 파일 준비 + Function DB 트레이를 모두 켠 pipeline이다.
  requirement 입력부터 승인 가능한 최종 결과까지의 plan/implement/repair 비용과
  인덱스·트레이 생성 시간을 전부 포함한다. 2-4에서는 fake provider로 모드 분리와 집계
  경계만 검증하고 실제 유료 실행은 발사하지 않는다. 실제 결과만 "Pluto 전체 순효과"로 표기한다.
- 다음 2x2 진단 실험은 보류 명세다. 1차 component/product 결과 뒤 별도 slice에서 지원하며
  제품 실험의 대표 수치와 합치지 않는다.
  - W0/C0: 현재 작업 지시서, 현재 implement context
  - W1/C0: 새 작업 지시서·빈 파일만 사용
  - W0/C1: 현재 계획에 트레이만 주입
  - W1/C1: 새 작업 지시서·빈 파일·트레이 전부 사용
- 실험 시작 전에 task set, 제외 기준, 반복 수, primary metric, 품질 비열등 한계,
  중단 규칙을 `experiment.json`에 freeze한다. 중간 결과가 좋아도 조기 종료하지 않는다.
- Claude Code/파이프라인/그래프 생성기 버전과 model identifier, prompt/template digest,
  의존성 lockfile digest를 고정·기록한다. 실행 중 하나라도 바뀌면 새 experiment로 분리한다.

### 7. 반드시 계측할 원자료

기존 `Telemetry.to_dict()`의 exact/observed/estimated 구분을 유지하고 최소
확장한다. 추정치를 exact 필드에 넣지 않는다.

#### exact — Claude 결과 이벤트와 실행 환경

- experiment_id, pair_id, arm(A/B), repeat, 실행 순서
- slice/run/session id, stage, attempt, resume/repair 횟수와 원인
- model, effort, max_turns, timeout, result subtype, exit code
- input tokens, output tokens, cache creation tokens, cache read tokens,
  billed input tokens, peak context, total tokens
- total_cost_usd(API 단가 환산), duration, API duration, turns
- usage_state와 streamed/final reconciliation; final usage가 없거나 불일치하면
  aggregate에서 별도 `invalid_usage`로 분류
- requirement/plan/prompt/graph/context tray/source tree의 digest

#### observed — 실제 행동

- tool별 호출 수·입력 bytes·결과 bytes·duration·error
- Read/Edit/Write/Bash/graph query 수
- 파일별 read/edit/write count와 bytes, repo 밖 접근
- Bash/PowerShell을 통한 `cat`, `sed`, `rg`, `grep`, `Get-Content`, redirect 등 파일 I/O와
  MCP/graph source 조회를 별도 분류한다. `Read` tool 횟수만으로 전체 파일 읽기라고
  보고하지 않는다. 분류하지 못한 I/O는 `unclassified_io`로 남기고 0으로 간주하지 않는다.
- repeated reads/commands와 첫 실행 이후 낭비 bytes
- context tray 안/밖 조회, 최초 escape turn, graph query 종류
- 변경 파일·함수 집합과 작업 지시서 대비 scope 집합
- test command별 실행 횟수·exit code·통과/실패 개수
- usage limit 대기 시간, 자동 연장 turns, observer/repair 진입
- session compaction, resume, retry 전 attempt의 누적 사용량. 마지막 성공 session의
  usage만 남기지 않는다.

#### derived/estimated — 계산식과 함께 저장

- briefing/context tray tokens와 생성 시간
- exploration tokens/share, repeated-read avoidable tokens
- net token delta와 reduction percent
- API-equivalent cost delta와 reduction percent
- scope precision = planned_and_changed / changed
- scope recall = planned_and_changed / planned
- context escape rate와 escape 후 실제 변경으로 이어진 비율
- accepted slice당 tokens/cost, 성공 1건당 tokens/cost
- 모든 비율은 분모와 원시 count를 함께 기록하고 0 분모를 0으로 꾸미지 않는다.

#### Max 플랜 사용량 — 별도 보조 자료

- Max의 내부 token-to-quota 공식은 공개·관측되지 않으므로 exact token/cost와
  합치지 않는다.
- 사용자가 실행 전후 Usage 화면 또는 `/status`에서 기록한 값이 있을 때만
  `subscription_observed`에 원문·시각·reset window와 함께 저장한다.
- 이 값으로 API 비용이나 token을 역산하지 않고, 대표 절감률의 근거로 자동
  승격하지 않는다.

### 8. 코드 품질·사람 비용 계측

- A/B 양쪽에 동일한 선언 test_commands를 실행한다. 종료 전 새 테스트만
  통과하고 기존 테스트가 깨진 경우 성공으로 보지 않는다.
- 자동 품질 결과:
  - compile/typecheck/lint/test 결과
  - 변경 줄 수와 파일 수
  - 계획 밖 변경, write guard 위반, rollback/repair 여부
  - 최종 diff에 빈 구현, TODO, 임시 우회, 테스트 삭제가 남았는지
  - 첫 유효 patch까지 turns/time/tokens, 최초 test pass까지 attempts, 승인 전 repair 수
  - 존재하지 않는 symbol/path/API를 사용한 횟수와 graph의 ambiguous 사실을 확정 사실처럼
    사용한 횟수
- agent가 볼 수 없는 holdout test를 별도 작업 유형에 대해 실행한다. 선언 test_commands,
  기존 test, holdout test 파일과 명령의 변경·삭제를 write guard로 차단한다.
- 테스트 통과만으로 품질을 판정하지 않는다. task acceptance 항목별 충족 여부,
  type/lint 결과, 중복 증가, public API 호환성, 성능 회귀가 관련된 task는 해당 지표를
  `quality.json`에 저장한다.
#### 보류 측정 프로토콜 — 이번 4개 slice에서 구현하지 않음

- 제품 주장용 실행에는 사람 평가를 반드시 붙인다.
  - arm을 숨긴 blind review
  - correctness, maintainability, scope fit 각 1~5
  - 승인/반려, 검토 시간, 재작업 요청 수
- 제품 주장용 실행은 blind reviewer를 2명 이상 배정하고 reviewer별 원점수와 일치도를
  보존한다. 합의 결과만 저장하지 않는다.
- 사람 통제 효과를 별도 review A/B로 잰다. 동일 diff에 대해 diff-only와
  Function DB 영향 그래프 + 함수별 diff를 교차 배정하고 다음을 기록한다.
  - 변경 목적을 정확히 설명하기까지 시간
  - 영향 함수·누락 파일·결함 탐지 precision/recall
  - 승인/반려 정확도, 검토 중 source 추가 열람 수, 주관적 확신도와 인지 부하
  이 결과를 agent token A/B와 합쳐 하나의 절감률로 만들지 않는다.
- 대표 지표는 단순 total tokens가 아니라 `accepted slice당 tokens/cost`다.
  품질이 낮아 통과 작업 수가 줄면 절감으로 판정하지 않는다.
- 실제 pilot에서는 merge 뒤 7일/30일의 revert, hotfix, reopened task, 관련 defect를
  후행 품질로 연결한다. 이 값이 없는 실험은 장기 품질을 증명했다고 표기하지 않는다.

### 9. 실험 산출물과 집계 규칙

- `.aidev/experiments/<experiment-id>/`에 다음을 보존한다.
  - `experiment.json`: 고정 조건, task 목록, 실행 순서, 생성기 version
  - `pairs/<pair-id>/A.json`, `B.json`: 원자료 경로와 digest
  - 각 arm의 prompt, context tray(B만), telemetry, diff, test logs, commit hash
  - `report.json`: 계산 가능한 기계 형식
  - `report.md`: 사람이 읽는 paired 비교
- report는 stage별·task 유형별로 A/B를 분리하고 평균만 내지 않는다.
  2-4 fake/smoke report는 최소한 pair별 원시 값, 중앙값, 평균, 성공률,
  invalid/failed 수와 paired delta를 표시한다. bootstrap 95% 신뢰구간은
  통계 완전판 slice 전까지 계산하거나 제품 주장에 사용하지 않는다.
- `up to` 최대 절감률을 대표값으로 사용하지 않는다. 대표값은 성공한 pair의
  중앙값이고, 실패율과 accepted-slice 지표를 바로 옆에 표시한다.
- 초기 smoke는 task 유형별 1 pair로 runner와 계측만 검증한다.
- 아래 제품 주장용 실행 규격은 보류한다. Python 소형 수리, Python cross-file, Desktop/UI,
  장기·resume 작업을 포함한 사전 고정 task set에서 유형별 최소 5개,
  task당 최소 3 repeats를 요구한다. task 선택과 제외 사유를 report에 남긴다.

### 10. Function DB 정확성·적격성 게이트

- Function DB가 "파싱 성공"을 반환했다는 이유만으로 treatment 입력 자격을 주지 않는다.
  target/reference별로 parser 종류, source hash, 추출 symbol 수, resolved/ambiguous/unresolved
  edge 수, parse warning을 `graph_quality.json`에 기록한다.
- JS/TS 정규식 parser의 조용한 오파싱을 별도 위험으로 취급한다. 최소한 다음 구문 fixture를
  추가하고, 틀린 symbol/line/signature/edge를 정상 데이터로 넣지 못하게 한다.
  - 중첩 함수·콜백·화살표 함수·고차 함수
  - overload signature와 구현체, generic, decorator
  - 객체 method·class field·computed property
  - import alias·re-export·barrel export·default export
  - 문자열/template literal/comment 안의 함수 모양 텍스트
  - JSX/TSX 중괄호, React hook callback, dynamic import
- parser가 확정할 수 없는 관계는 resolved edge로 승격하지 않는다. provenance와 confidence는
  규칙 기반 enum으로 저장하고, ambiguous/unresolved는 트레이의 확정 사실 절에 넣지 않는다.
- 실험 시작 전에 graph eligibility threshold를 freeze한다. 직접 지정 symbol이 누락되거나,
  source hash가 다르거나, 필수 parser warning이 있으면 해당 B arm을 `invalid_graph`로
  중단한다. 조용히 A prompt로 fallback하거나 task를 표본에서 삭제하지 않는다.
- 0 byte CREATE 파일은 아직 graph 사실이 아니다. 트레이는 frozen plan의 REFERENCE/MODIFY
  좌표와 base commit graph에서 먼저 생성하고, 빈 파일 생성 뒤의 dirty 상태를 새 관계의
  근거로 사용하지 않는다.
- 함수 그래프만으로 표현되지 않는 직접 의존을 누락하지 않는다. 작업 지시서가 명명했거나
  직접 import된 config/schema/migration/CSS/asset/package manifest/env 계약은
  `non_function_context` 절에 provenance와 token cap을 붙여 제공한다.
- generated/vendor/build output, fixture, test helper는 각 항목에 origin을 표시한다.
  기본 제외 규칙과 포함 예외를 고정하고 arm별로 동일하게 적용한다.
- 사람이 라벨링한 language별 graph gold set은 보류한다. 이번 2-3에서는 고정 JS/TS parser
  fixture의 기대 symbol/edge/line-range와 실제 DB row를 exact match하는 회귀 테스트로 대체한다.
- treatment run 뒤 최종 changed symbol과 실제 source escape를 post-hoc dependency set으로
  만들고, 트레이의 `context_coverage`, `unused_context`, `wrong_context`를 계산한다.
  `graph query 0`만으로 트레이가 충분했다고 판정하지 않는다.

### 11. 보안·승인 무결성·쓰기 차단

- 작업 지시서의 CREATE/MODIFY/REFERENCE는 정규화한 repo-relative 경로만 허용한다.
  `..`, 절대 경로, drive/UNC 경로, alternate data stream, symlink/junction을 따라 repo 밖으로
  나가는 경로, Windows 대소문자·separator 변형을 canonical path 검사로 차단한다.
- CREATE는 승인된 allowlist 경로에 새 0 byte 파일을 만드는 동작만 허용한다. 기존 파일,
  symlink, directory, device name과 충돌하면 fail closed한다.
- write guard는 `Write` tool 하나만 검사해서는 안 된다. Edit/NotebookEdit/Bash 및 shell
  redirect·스크립트·formatter가 만든 쓰기까지 최종 filesystem diff와 실시간 guard로
  검사한다. 감시할 수 없는 쓰기 수단은 experiment 실행에서 금지한다.
- plan 승인 시 requirement, plan, 작업 지시서, approval artifact, base commit, target set의
  digest를 하나의 승인 manifest로 묶는다. implement launch, resume, repair 직전에 다시
  검증하고 하나라도 바뀌면 재승인 전까지 중단한다.
- repository의 comment/docstring/README와 graph description은 신뢰하지 않는 데이터다.
  generator가 이를 고정 schema의 data field로 직렬화하고 delimiter·control sequence를
  escape한다. raw free-text나 source body를 지시문 위치에 붙이지 않는다. 트레이 안의 문장이
  system prompt·작업 지시서·승인을 덮어쓰지 못하는 adversarial fixture를 통과해야 한다.
- graph/tray는 현재 실행 주체가 읽을 수 있는 repo 경로만 포함한다. `.env`, key, token,
  credential, repo 밖 source를 prompt·telemetry·experiment artifact에 저장하지 않는다.
  artifact redaction 전후 count와 redaction reason만 기록한다.
- A/B runner와 test_commands는 shell string 재조합 없이 기존 검증된 argv 실행 경로를
  사용한다. requirement/plan의 문자열을 command, branch, worktree path에 그대로 삽입하지 않는다.

### 12. 동시 실행·크래시 복구·격리

- 기존 slice 단위 lock 외에 experiment lock과 pair/arm lock을 둔다. 같은 experiment,
  pair, worktree, context cache를 두 프로세스가 동시에 생성·실행하지 못한다.
- lock에는 pid, host, process start identity, 생성 시각을 기록한다. pid 재사용만으로 stale
  lock을 제거하지 않고, 살아 있는 실행의 lock은 자동 탈취하지 않는다.
- experiment manifest, prepared-target ledger, telemetry aggregate, report는 temp 파일에
  쓴 뒤 atomic replace한다. SQLite 조회는 동일 snapshot/read transaction에서 수행하고,
  graph rebuild와 tray 생성이 겹치면 대기 후 새 digest를 재검증한다.
- resume 시 state.json만 신뢰하지 않는다. git HEAD/status, worktree identity, approval digest,
  prepared file hash, graph/context digest, 이전 attempt 종료 상태를 대조해 다음 stage를 결정한다.
- A/B 결과 branch는 자동 merge하지 않는다. 각 arm을 `experiment-only`로 표시하고 사람이
  선택한 한 결과만 별도 승인 뒤 merge할 수 있게 한다.
- usage-limit 대기 중 global experiment lock으로 무관한 slice를 막지 않는다. pair의 소유권은
  유지하되 다른 pair/slice 실행 가능한 범위의 lock으로 분리한다.

### 13. 비용 폭주와 자동 복구 상한

- experiment 전체와 pair별로 `max_attempts`, `max_total_turns`, `max_total_tokens`,
  `max_cost_usd`, `max_wall_time`, `max_usage_wait`를 사전 고정한다. arm별 cap만 두지 않는다.
- cap은 resume/repair/observer/auto-extend를 포함한 누적값에 적용한다. 새 session을 열어도
  초기화하지 않으며 하나라도 초과하면 hard stop하고 자동 재개하지 않는다.
- usage-limit, timeout, malformed usage event, graph rebuild 실패는 attempt로 기록한다.
  비용이 없는 실패라고 임의 판정하지 않고 관측된 사용량을 누적한다.
- end-to-end 제품 실험의 `cost_per_accepted_slice`에는 실패 attempt, plan, implement,
  repair, graph build, tray build를 전부 포함한다. 구성요소 실험은 frozen plan 비용을
  별도 `shared_setup_cost`로 보고하고 implement 절감률 안에 숨기지 않는다.
- API 단가표 version과 계산 시각을 저장한다. 가격이 바뀐 결과끼리는 raw tokens와
  당시 비용, 동일 단가로 재계산한 normalized cost를 함께 표시한다.

### 14. 보류 명세 — 통계적 판정과 제품 주장 기준

이 절은 표본이 쌓인 뒤 통계 완전판 slice에서 구현한다. 2-4는 아래 계산을 나중에
재현할 수 있도록 원시 attempt·pair 데이터와 digest를 손실 없이 보존하는 데까지만 한다.

- 동일 task·repeat의 A/B를 paired sample로 계산한다. 독립 표본처럼 합쳐 표본 수를
  부풀리지 않는다. task와 repeat를 모두 표시하고 task-level 중앙값도 함께 낸다.
- seed/temperature를 고정할 수 있으면 고정·기록한다. 고정할 수 없는 backend 변동은
  숨기지 않고 repeat 분산에 포함한다.
- 제품 실험과 겹치지 않는 pilot task로 분산을 추정해 필요한 표본 수를 사전 결정한다.
  유형별 5 task × 3 repeats는 최저선일 뿐 통계적 충분성을 뜻하지 않는다.
- task 선택 후 결과가 나쁜 task를 제외하지 않는다. 사전 제외 기준에 해당한 invalid만
  제외하고, 실패와 budget stop은 성공률·accepted-slice 비용에 남긴다.
- quality non-inferiority 한계를 사전 고정한다. 기본값은 test/acceptance 성공률 -5%p,
  blind correctness 중앙값 -0.25 이내이며 프로젝트가 다른 값을 쓰면 실행 전에 기록한다.
- 기술 성공 기준은 end-to-end에서 품질 비열등을 만족하면서 accepted slice당 total token
  중앙값 30% 이상 감소, out-of-scope change와 재작업률 비증가로 둔다.
- "40~50% 절감" 제품 문구는 end-to-end 중앙값 40% 이상이고 95% 신뢰구간 하한이 30%를
  넘으며 품질 비열등을 만족할 때만 사용한다. 구성요소 실험, 최댓값, 단일 사용자 Max
  사용률로 이 문구를 만들지 않는다.
- `±5%` 정확도는 반복 수만으로 선언하지 않는다. 해당 신뢰구간 폭이 실제로 ±5%p 이하인
  경우에만 표기하고, 아니면 관측치와 넓은 구간을 그대로 보고한다.
- primary metric은 하나만 지정한다. 나머지는 secondary로 표시하고 여러 지표 중 가장 좋은
  값만 대표 결과로 선택하지 않는다.

## 위협 모델과 필수 대응

| 위협 | 실패 형태 | 필수 대응 |
|---|---|---|
| stale Function DB | 오래된 signature/edge를 사실로 공급 | HEAD·dirty·schema 검증 후 갱신, 증명 불가 시 B 중단 |
| parser의 조용한 오파싱 | 틀린 symbol/edge가 정상 데이터로 DB에 저장 | 구문 fixture, provenance, eligibility gate, invalid_graph 분리 |
| graph query 0 오해 | 트레이 충분과 기능 미사용을 같은 성공으로 해석 | gold set + context coverage/unused/wrong context 계측 |
| 맥락 누락 | 적은 토큰으로 자신 있게 잘못 구현 | context escape 허용·계측, test/approval gate 유지 |
| 함수 밖 계약 누락 | config/schema/CSS/env 변경을 놓침 | 명시·직접 import 기반 non_function_context 절과 cap |
| 잘못 해결된 edge | 관련 없는 함수를 직접 관계로 제시 | resolved만 본 절에 포함, ambiguous/unresolved 분리 |
| 저장소 prompt injection | 주석 속 명령이 작업 규약을 덮음 | graph 내용을 untrusted data로 delimiter 처리하고 우선순위 강제 |
| 트레이 팽창 | 브리핑 자체가 context pollution | 대상/전체 token cap, 결정론적 우선순위·제외 기록 |
| plan 형식 과대화 | 계획 토큰이 구현 절감을 상쇄 | 세 동사의 한 줄 작업 지시서만 추가 |
| 원본 소스 오염 | 함수 목록 주석이 제품 코드에 남음 | 트레이를 slice sidecar로 저장, 빈 대상 파일은 0 byte |
| 범위 과잉 차단 | 필요한 의존 수정 불가로 실패 | 읽기 escape 허용, 쓰기는 amend/approval로 확장 |
| A/B plan 변동 | plan 품질 차이를 implement 효과로 오인 | plan 1회 승인·freeze 후 양 arm에서 재사용 |
| component/product 혼동 | 트레이 절감률을 전체 제품 절감률로 주장 | 구성요소 실험과 end-to-end 제품 실험 분리 |
| 모델/effort 혼합 | Fable/Opus 차이를 Pluto 효과로 오인 | stage routing과 effort 고정·기록 |
| 모델·CLI drift | 실행 날짜의 backend/version 차이를 treatment로 오인 | version/digest 고정, 변경 시 experiment 분리 |
| 캐시·순서 편향 | 먼저 실행한 arm만 불리/유리 | 별도 cache/worktree/session, A/B·B/A 교차 |
| 성공작 선택 | 실패를 버려 절감률 과장 | 모든 attempt 보존, 실패율·accepted-slice 비용 표시 |
| 조기 종료·task 선별 | 좋은 중간 결과만 제품 수치로 사용 | task/exclusion/repeat/stopping rule 사전 등록 |
| 품질 저하 은폐 | token은 감소하지만 코드가 나쁨 | 동일 테스트 + scope + blind review를 함께 판정 |
| 테스트 조작 | agent가 테스트를 약화해 성공 처리 | 기존/holdout test와 명령 변경 차단, 원본 digest 검증 |
| 장기 결함 은폐 | 당일 통과 뒤 revert/hotfix 발생 | pilot 7/30일 후행 품질 연결, 없으면 장기 품질 주장 금지 |
| 사람 가치 미측정 | token만 줄고 이해·통제가 개선됐다고 주장 | diff-only 대 영향 그래프 blind review A/B를 별도 수행 |
| 추정/실측 혼합 | exploration 추정을 실제 청구액으로 표현 | exact/observed/estimated 스키마 분리 |
| Read tool만 계측 | shell/MCP 읽기를 놓쳐 탐색 0으로 과장 | shell I/O 분류와 unclassified_io 보존 |
| Max/API 혼동 | 구독 사용률을 API 비용으로 주장 | subscription_observed 별도 저장, 역산 금지 |
| 크래시 중간 상태 | state는 준비 완료이나 git/file은 다름 | prepared target ledger+hash, resume 시 대조 후 fail closed |
| approval TOCTOU | 승인 뒤 plan/target이 바뀐 채 implement | 승인 manifest digest를 launch/resume/repair마다 재검증 |
| 경로·symlink 탈출 | 승인 target처럼 보이며 repo 밖 쓰기 | canonical path와 symlink/junction 검사, 모든 쓰기 수단 감시 |
| 동시 experiment | 같은 worktree/cache/report를 두 프로세스가 훼손 | experiment/pair/arm lock, atomic write, SQLite snapshot |
| 자동 복구 비용 폭주 | session 교체로 cap이 초기화되어 무한 과금 | experiment 누적 hard cap, 초과 뒤 자동 재개 금지 |

## 하지 않는 것

- 임베딩·벡터 DB·LLM 기반 관련 함수 판정
- 다중 코드베이스 함수 복사·재사용, COPIED_FROM 계보
- 함수 분류 카테고리 UI, 그래프 이력 UI, desktop 대시보드
- 모델 자동 라우팅 최적화
- Max 플랜 내부 크레딧 환산 공식 추정
- FunctionGraphSurface 분리 또는 일반 IDE 기능 추가
- 품질을 무시한 토큰 최소화, 트레이 밖 읽기의 전면 차단

## Done Criteria

- fake 기반으로 작업 지시서 파싱·검증과 CREATE 빈 파일 준비가 동작한다.
- 기존 파일 덮어쓰기·repo 밖 경로·충돌 동사는 implement 전에 차단된다.
- 동일 plan에서 A/B가 별도 worktree/session으로 실행되고 prompt 차이가
  implement context tray 하나뿐임을 fixture가 증명한다.
- fake provider에서 현재 pipeline 대 full treatment의 end-to-end 모드가 구성요소 실험과
  별도 실행·집계되며 plan/실패 attempt/graph 준비 비용 필드가 분리된다.
- B 트레이가 직접 함수, 1-hop caller/callee, 타입/파라미터/변수, 테스트를
  결정론적으로 포함하고 token cap 초과분을 기록한다.
- JS/TS 오파싱 fixture와 graph eligibility gate가 틀린 symbol/edge의 정상 주입을
  차단하고 invalid_graph를 report에 남긴다.
- JS/TS parser fixture의 기대 symbol/edge/line-range가 실제 DB row와 일치하고 treatment의
  context coverage/unused/wrong context 원자료가 report에 포함된다.
- stale/dirty graph가 갱신되고 증명 실패 시 treatment가 조용히 A로
  fallback하지 않는다.
- repo 탈출·symlink/junction·shell redirect·approval digest 변조가 launch 또는 쓰기
  시점에 fail closed되는 테스트가 있다.
- 동일 experiment/pair의 동시 실행, report atomicity, crash 뒤 state/git/file/digest
  불일치를 검증하는 multiprocessing fixture가 있다.
- context escape, scope 집합, exact token 4종, cost, turns, retries,
  tests, repeated reads, shell/MCP I/O와 unclassified_io가 arm별 telemetry에 기록된다.
- final usage 누락, 실패, usage limit, invalid treatment가 report에서 사라지지 않는다.
- experiment 누적 token/cost/turn/attempt/wall-time cap이 session resume 뒤에도 유지되고
  초과 시 자동 복구가 재개되지 않는다.
- fake/smoke report가 pair별/중앙값/평균/성공률/accepted-slice 비용을 재계산 가능한
  원자료와 paired delta를 출력하고, 아직 계산하지 않은 신뢰구간을 0으로 꾸미지 않는다.
- `implement_context: off` 대조군이 기존 implement prompt를 불필요하게
  변경하지 않고, 기존 `briefing: off` 동작도 유지된다.
- 기존 pytest 전량 통과, 새 fake A/B smoke 통과, README에 실행법과 지표
  정의 추가, 임시 worktree/파일의 무단 삭제 없음.

## 완료 보고

RESULT 형식으로 보고한다. 구현 파일, 새 스키마, CLI 예시, fake A/B 결과,
남은 실제 유료 실험 항목을 구분하고 임시 파일 잔재를 남기지 않는다.
