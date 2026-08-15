---
approval: plan
---
# v0.3 — Workspace 격리

## 배경
v0.2 완주 실측(2026-08-15)에서 격리 부재의 비용이 전부 수동으로 지불되었다:
worktree 생성, 요구사항 커밋(dirty 순환), npm ci, merge 고민 — 전부 사람 손.
이번 slice는 그 수동 작업을 pipeline이 흡수한다.

## 핵심 원칙
> AI는 사용자의 체크아웃과 base 브랜치에 절대 직접 닿지 않는다.
> 모든 AI 작업은 pipeline이 만든 worktree + 전용 브랜치에서 일어나고,
> 사람의 merge만이 결과를 진짜 이력으로 만든다.

## 요구 동작

### 1. 시작 — pipeline이 격리 공간을 만든다
- `aidev pipeline --repo <본진> --requirement <파일>` 실행 시:
  worktree 생성 (`<본진과 분리된 경로>/<slice-id>`) + 전용 브랜치 `slice/<slice-id>`
- base 브랜치는 **main을 가정하지 않는다.** 기본 = 본진의 현재 HEAD.
  `--base <branch>` 옵션으로 명시 가능. (실측: 조커의 개발선은
  windows-handoff-20260808이며 main은 사용 중지 상태다)
- worktree 경로 충돌/잔해 처리: 이미 존재하면 실행 거부 + 사유 출력.
  (실측: 조커에 Orca 잔해 worktree 12개 존재 — 조용히 재사용하지 말 것)
- 요구사항 파일은 pipeline이 worktree 브랜치에 **첫 커밋으로 직접 커밋**한다.
  → v0.2의 "요구사항이 dirty를 만드는 순환" 해소.
  본진 dirty 검사는 worktree 생성에 필요한 수준으로 완화 가능 여부를 plan에서 검토
  (worktree add는 dirty 본진에서도 가능하다 — 임시 제한의 대체 조건을 정확히 정의할 것)

### 2. 진행 — 단계마다 커밋
- 각 stage 완료 시 pipeline이 worktree 브랜치에 자동 커밋 (에이전트에게 시키지 않는다)
- 커밋 메시지 형식 고정: `slice(<slice-id>): <stage>`
  ※ 이 커밋들은 향후 그래프 스냅샷의 기준점이다. slice 메타데이터
  (요구사항, 승인 기록, 토큰 소모)는 커밋에 연결 가능한 형태로 유지한다.
- .aidev/slices/<id>/ 상태 파일의 위치: worktree가 아닌 **본진** 기준인지
  worktree 기준인지 plan에서 결정할 것. 판단 기준: worktree 폐기 후에도
  slice 이력은 남아야 하고, 동시에 v0.2의 state.json 규약(단일 writer,
  authoritative source)을 깨지 않아야 한다.

### 3. 종료 — 승인 = merge, 반려 = 폐기
- slice done 후 최종 게이트: `aidev pipeline --repo <본진> --merge <slice-id>`
  → base 브랜치에 merge (충돌 시 중단하고 사람에게 보고, 자동 해결 금지)
- 반려: `--discard <slice-id>` → worktree 제거 + 브랜치 삭제 (본진 무흔적)
- 명령 이름/형태는 plan에서 대안 검토 가능 (별도 서브커맨드 등)

### 4. 의존성 설치
- worktree에는 node_modules 등 untracked 파생물이 없다 (실측: npm ci 수동 필요)
- pipeline이 임의 설치 명령을 실행하지 않는다. 대신 요구사항 front matter에
  선언하는 방식을 plan에서 설계할 것:
  예) `setup: npm ci --prefix backend`
  선언된 setup 명령은 해당 slice의 allowedTools에 추가되고 implement 전에
  1회 실행된다. 미선언 시 아무것도 하지 않는다.

## 하지 않는 것
- Planner (v0.4) / Codebase Memory (v0.5) / IDE 바인딩
- merge 충돌 자동 해결
- worktree 잔해 자동 청소 (감지·거부만, 청소는 사람)
- 본진 브랜치 생성·변경·push

## Done Criteria
- ☐ 한 명령으로: worktree+브랜치 생성 → requirement 첫 커밋 → plan → 승인
  → implement → test → 단계별 커밋이 브랜치에 존재 → done
- ☐ --merge로 base에 반영됨 / --discard로 본진 무흔적 확인
- ☐ base가 main이 아닌 브랜치(windows-handoff 형태)에서 동작
- ☐ worktree 잔해 존재 시 명확한 거부 메시지
- ☐ setup 선언 시 실행, 미선언 시 무동작
- ☐ 기존 pytest 전량 + fake_claude 기반 신규 시나리오 통과
- ☐ Windows 완주
- ☐ README 갱신 (v0.2 dirty 임시 제한의 대체 명시)
- ☐ 실전: 조커 본진에서 실제 요구사항 하나가 이 격리 흐름으로 완주

## 완료 보고
v0.2와 동일한 RESULT 형식.