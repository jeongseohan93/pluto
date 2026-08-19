---
approval: plan
max_turns: implement=160
---
# UI 기초선 + Graph View v0 — 터미널 은퇴 1차

## 배경
파이프라인 엔진은 완성(2세대+상차림+auto-recovery). 이제 일상
동선(발사→승인→관전→마감)을 앱으로 옮기고, Function DB를 화면에
비춘다 — "사람에겐 시선, AI에겐 DB"의 시선 절반. 모든 버튼은
기존 CLI 명령의 배선이다. 신규 비즈니스 로직 작성 금지 —
CLI/엔진이 이미 하는 일을 호출만 할 것.

## A. UI 기초선 (Pipeline 탭 확장)

### A1. 발사
- repo의 tasks/*.md 목록에서 requirement 선택 → [발사] 버튼
  → aidev pipeline --requirement 실행 (child_process, 출력 스트리밍)
- 실행 중 로그를 패널에 실시간 표시 (기존 관전 화면 재사용)

### A2. 승인 코멘트
- 기존 [Approve]/[Reject] 버튼 옆에 코멘트 입력칸 —
  approved: <문구> / rejected: <문구> 형식으로 approvals 파일에 기록
  (B가 만든 조건부 승인 규약 그대로)

### A3. 재개·마감 버튼
- failed slice에 [Resume] 버튼 (+선택적 턴 상향 입력
  → --max-turns-stage implement=N)
- done slice에 [Merge+Push] [Discard] 버튼
- ★가드: Merge 실패 시 Discard 버튼 비활성 + 경고 표시
  (8/18 gen2b 사고 — merge 안 된 slice의 discard 방지를 UI 레벨에서)

### A4. 죽음 표시
- failed slice 선택 시: 사인 분류(usage limit/턴 소진/기타 —
  auto-recovery의 분류 재사용) + progress.md 내용 + 리셋 대기 중이면
  남은 시간 표시

### A5. [DEMO] 워터마크
- 모든 mock/데모 데이터 화면에 [DEMO] 배지 표시 (혼동 3회 실측)

## B. Graph View v0 (새 탭)

### B1. 데이터
- .aidev/graph/graph.db 읽기 (readonly — 화면은 비추기만, 3조)
- graph 미빌드 repo면 [Build Graph] 버튼 → aidev graph build 실행

### B2. 화면
- 파일 단위 그룹핑 + 함수 노드 (이름 표시), 호출 관계는 선택한
  노드의 이웃만 엣지 표시 (전체 엣지 8669개 동시 렌더 금지 —
  스파게티 방지, LOD 원칙)
- 노드 클릭 → 사이드 패널: 기능 한 줄·시그니처·@param·@flow·
  callers/calls 목록(클릭 시 해당 노드로 이동)
- 검색창: 함수명 부분 일치 → 노드 포커스
- 명세 없는 함수는 시각 구분 (회색 등) — 커버리지가 눈에 보이게
- 렌더 라이브러리 선택은 plan에서 (기존 의존성 우선, 신규 추가 시
  근거 — 무거운 그래프 프레임워크 금지)

### B3. 신선도
- 헤더에 그래프 기준 커밋 + built 시각 표시, [Rebuild] 버튼

## 하지 않는 것
- Graph Notes(메모) / 도메인 구획 / 드래그 이동(graph move) —
  전부 후속. 계기판 탭 — 스트레치, 이번 범위 밖
- 파이프라인 엔진 코드 변경 (aidev/*.py는 읽기만)

## Done Criteria
- 발사→승인(코멘트)→관전→resume→merge+push→discard가 전부
  앱 안에서 완결 (터미널 0회) — Pluto repo 실물로 검증
- Graph View: Pluto의 1252 함수가 뜨고, run_pipeline 클릭 시
  명세·관계가 패널에 표시, 검색 동작
- merge 실패→discard 방지 가드 동작 확인
- 기존 pytest 전량 통과 (엔진 무변경 증명)

## 완료 보고: RESULT 형식, 임시 파일 잔재 없이
