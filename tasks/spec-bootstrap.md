---
approval: plan
max_turns: implement=140
test_commands: python -m pytest -q
---
# 명세 부트스트랩 — 핫 패스 함수 초벌 명세

## 배경
그래프 엔진 가동 (1189 함수, 커버리지 55%). 상차림(2단계) 전에
핫 패스 중심으로 커버리지를 올린다. 코드 동작 변경 절대 금지 — 주석만.

## 요구사항
1. 대상 선정: graph DB 조회로 — 명세 없는 305개 중
   callers 수 상위 + aidev/ 핵심 모듈(pipeline·workspace·storage·
   runner·cli·verify·specs·graph)의 public 함수 우선.
   구체 목록·개수는 plan에서 DB 근거로 확정 (예상 80~120개)
2. 각 함수에 gen2 명세 형식 초벌: 기능 한 줄 / @param /
   주요 내부 변수 / @flow(해당 시) — 함수명 재서술 금지,
   본문을 읽고 실제 동작을 서술
3. 코드 변경 0: 주석 외 어떤 라인도 수정 금지
4. 완료 후 graph build 재실행 → 커버리지 before(55%)→after 보고

## 하지 않는 것
- 테스트 파일 명세 / 리팩토링 / 자잘한 private 헬퍼 / desktop(JS) 쪽

## Done Criteria
- diff에 주석 외 변경 0 (기계 확인 가능한 방식으로 검증)
- pytest 전량 통과 / 커버리지 상승 보고 / README 언급 불필요
