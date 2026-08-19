---
approval: plan
max_turns: implement=160
test_commands: python -m pytest -q
---
# Auto-Recovery — 사망 대응 자동화

## 배경 (실측 근거)
24시간 내 usage limit 사망 2회 (8/18 밤, 8/19 새벽) — 매회 사람이
리셋 시각을 계산해 수동 resume. 턴 소진 사망도 매회 수동 진단
(로그 판독→상향 결정→재발사)을 사람이 함. 다투척/에픽 야간 운영의
전제는 "멈춤이 사람을 기다리지 않는 것" — 멈춤 자체가 비용이다.
실측 단서: 한도 사망 시 events.jsonl 마지막에
"usage limit reached|<unix타임스탬프>" 형태로 리셋 시각이 남는다.

## 요구사항

### 1. 사망 분류기 (엔진, 세션 0)
- stage 실패 시 엔진이 events.jsonl 꼬리를 판독해 사인 분류:
  a. usage limit — 위 패턴 매칭, 타임스탬프 추출
  b. 턴 소진 — max_turns 도달
  c. 그 외 에러 — 기존 failure 경로로 (변경 없음)

### 2. usage limit → 자동 대기·재개
- 리셋 타임스탬프 + 여유(60초)까지 대기 후 자동 resume
- 대기 중 로그 1줄: "한도 도달 — HH:MM 자동 재개 예정"
- 프로세스 유지 방식(sleep)이 기본. 프로세스가 죽어 있던 경우를
  위한 보완책(다음 명령 시 안내 등)은 plan에서 제안
- front matter `auto_resume: off`로 비활성 가능 (기본 on)

### 3. 턴 소진 → 건강 판정 + 턴 견적 + 자동 연장
- 엔진이 최근 이벤트 패턴으로 판정:
  건강 = 새 파일 순회·Edit 다양·progress Done이 자람
  병리 = 같은 파일 Edit 반복 / 같은 테스트 실패 반복 / 재독 폭주
  (판정 기준·임계값은 plan에서 구체화)
- 건강 → 턴 견적: progress.md의 Remaining 개수 × 이번 세션의
  실측 소화 속도(턴/파일) + 여유 20% = 연장량 산출 → 자동 연장 resume
  로그: "정직한 소진 — Remaining N건, +M턴 연장 재개"
- 병리 → Observer 소환 (§4)

### 4. Observer — 외부 눈 (병리 시에만, readonly)
- 별도 세션: 코드 수정 불가, 로그(events/progress/failure)만 입력
- 산출: observation.md — 반복 중인 행동 / 막힌 지점 추정 원인 /
  제안 (접근 전환·scope 재검토·사람 호출 중 택1)
- 재시도 프롬프트에 observation.md 주입 후 1회 재개
- model 지정 가능 (모델 믹스 문법 재사용 — 다른 모델이 다른 눈)

### 5. 안전핀 (무한 과금 방지)
- 자동 연장: slice당 최대 2회, 총 턴 상한(기본 300) 초과 금지
- Observer 경유 재시도: slice당 최대 1회
- 상한 도달 → 자동화 중지, 사람 보고 (기존 failed 상태로)
- 모든 자동 조치는 state에 기록 (시각·사유·연장량 — Ledger 원천)
- front matter `auto_extend: aggressive|conservative|off`
  (기본 conservative: 연장 1회·Observer 없이 / aggressive: 위 상한까지)

## 하지 않는 것
- 새 발사의 자동화 (재개만 자동 — 시작은 항상 사람)
- 크레딧 구간 자동 진입 결정 / UI / 브리핑·그래프 변경

## Done Criteria
- fake 기반: usage limit 사망 → 타임스탬프 파싱 → 대기 → 자동 재개
- 턴 소진(건강) → 견적 산출 → 연장 재개 / (병리) → Observer →
  observation.md → 주입 재시도
- 상한 도달 시 정지·보고 / off 스위치들 동작
- 자동 조치의 state 기록 확인 / 기존 pytest 전량 통과 / README 갱신

## 완료 보고: RESULT 형식, 임시 파일 잔재 없이
