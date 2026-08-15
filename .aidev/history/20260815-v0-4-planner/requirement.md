---
approval: plan
---
# v0.4 — Epic → Slice Planner + 순차 큐

## 배경
v0.2~v0.3으로 slice 1개의 무인 실행은 완성됐다. 남은 병목은 사람이
slice마다 요구사항을 쓰고 발사 명령을 치는 것이다. 이번 slice로
"에픽 하나 → slice 목록 승인 → 순차 무인 실행"의 루프를 닫는다.

실측 근거 (2026-08-15):
- 대형 slice 2건이 max-turns 80 소진으로 사망 (81턴) — 분해 크기 판단 필요
- slice 4개 순차 실행에서 사람의 역할은 발사 명령과 승인뿐이었다 —
  발사는 자동화 가능, 승인은 게이트로 유지

## 요구 동작

### 1. Epic 입력과 분해
- `aidev pipeline --repo <본진> --epic <에픽.md> [--base <branch>]`
- 새 stage `decompose` (readonly): 에픽을 읽고 slice 목록을 생성
  → `.aidev/epics/<epic-id>/slices.md` 저장
- slices.md의 각 항목은 실행 가능한 requirement여야 한다:
  제목 / 요구 동작 / scope / 테스트 / 하지 않는 것 / (필요 시 setup, approval)
  — 즉 지금까지 사람이 쓰던 형식 그대로. 형식은 plan에서 확정하되
  기존 requirement front matter 규약과 호환일 것.
- 분해 기준에 **턴 예산**을 포함한다: slice 하나가 기본 max-turns 안에
  끝나도록 자르는 것이 분해의 품질 기준이다. 판단이 어려우면 더 작게.
- 의존 순서 명시: slice 간 선후 관계(B는 A의 결과 위에)를 목록 순서로 표현.

### 2. 목록 승인 게이트
- 분해 결과는 무조건 사람 승인 대상 (approval: none으로도 끌 수 없음)
- 사람은 slices.md를 수정 후 승인할 수 있다 (기존 plan 수정-승인과 동일 규약)

### 3. 순차 큐 실행
- 승인된 목록의 slice들을 순서대로 실행: 각 slice = 기존 v0.3 전체 흐름
  (worktree 생성 → requirement 커밋 → plan → 게이트 → implement → test → 단계커밋)
- slice N이 done이어야 N+1 시작. **N+1의 base는 plan에서 결정할 것**:
  (a) 모두 동일 base에서 분기 (병렬적, merge 순서 문제 발생)
  (b) N의 브랜치 위에 N+1 (직렬적, 중간 merge 불필요하나 사슬이 김)
  (c) N을 base에 auto-merge 후 N+1 (merge가 무인화됨 — 사람 merge 원칙과 충돌)
  각각의 트레이드오프를 비교하고 추천할 것. 단 (c)를 고르려면
  "사람의 merge만이 진짜 이력" 원칙과의 조화 방안이 함께 있어야 한다.
- slice 하나가 failed면 큐 중단, 이유와 재개 방법 출력.
  --resume-epic <epic-id>로 실패 지점부터 재개.
- **쿼터 대기는 큐를 중단시키지 않는다**: 해당 slice가 quota_wait로
  대기 후 이어가고, 큐는 그 slice의 완료를 기다린다.
- slice 내부의 승인 게이트(plan 등)는 그대로 작동한다 — 큐가 승인 대기에서
  멈춰 있는 것은 정상 상태다.

### 4. 상태와 관측
- epic 상태 파일: `.aidev/epics/<epic-id>/state.json`
  (전체 목록, 각 slice의 참조와 상태, 현재 위치) — slice state.json 규약
  (단일 writer, atomic, authoritative source)을 상속
- `--list`가 epic 진행 상황도 보여줄 것

## 하지 않는 것
- 병렬 slice 실행 (직렬만)
- 분해의 자동 재조정 (실패 시 사람이 slices.md 수정 후 재개)
- Cross-model review (v0.9) / IDE 연동 / 브라우저 검증
- 에픽 중첩 (에픽 안의 에픽)

## Done Criteria
- ☐ 에픽 1개 → decompose → 목록 승인 → slice 2개 이상 순차 완주 (fake 기반)
- ☐ 중간 slice 실패 시 큐 중단 + --resume-epic으로 재개
- ☐ slice 내 승인 게이트가 큐 안에서 정상 작동
- ☐ 기존 단일 --requirement 경로 무손상 (기존 테스트 전량 통과)
- ☐ Windows 완주
- ☐ 실전: 조커의 실제 에픽 하나(2~3 slice 분량)가 분해→승인→순차 완주
- ☐ README 갱신

## 완료 보고
RESULT 형식. 임시 파일 잔재 없이.