---
approval: plan
setup: npm ci --prefix desktop
---
# v0.2.5 — Pluto 최소 바인딩 (mock 제거 1단계)

## 배경
파이프라인(v0.2~0.4)은 완성됐고 운영은 전부 터미널+메모장이다.
실측된 마찰(2026-08-15): 승인마다 메모장으로 긴 경로 열기, approvals 파일을
놓고 polling과 사람이 경합(Windows 권한 팝업), plan을 폴더 뒤져 열기,
상태 확인에 --list 반복. 이번 slice는 그 마찰만 제거한다.

## 범위 — 딱 세 기능
1. **상태 표시**: 선택한 repo의 .aidev/slices/*/state.json을 읽어
   slice 목록과 현재 상태(stage 진행, waiting_approval 강조) 표시.
   epic state(.aidev/epics/)가 있으면 함께 표시.
2. **plan 뷰어**: waiting_approval인 slice 선택 시 해당 stage 산출물
   (plan.md 등)을 텍스트로 표시. 렌더링 품질은 요구하지 않는다.
3. **승인/반려**: 버튼 → approvals/<stage>.md에 v0.2 규약 그대로 기록
   ("approved" / "rejected: <사유>", 사유는 입력창). 
   preload에 write capability는 이것 하나만 추가한다.

## 대상 repo 선택
- 최소로: 폴더 선택 다이얼로그 + 최근 목록 기억. main process가 소유.

## 구조 원칙 (기존 desktop 설계 준수)
- 판단의 주인은 Python Core. Pluto는 파일을 읽고, 승인 파일 하나만 쓴다.
- 파일 읽기는 read_json_tolerant와 동일한 관용(깨진 JSON에 죽지 않기,
  polling 주기 갱신, STALE 감지 규약 존중).
- sandbox/contextIsolation 유지, preload는 capability 기반
  (기존 window.aidev 패턴). renderer에 Node API 노출 금지.
- 기존 mock 중 이번 세 기능에 해당하는 부분만 실데이터로 교체.
  나머지 mock 화면은 건드리지 않는다.

## 하지 않는 것
- 실행/발사 버튼 (발사는 터미널 유지)
- 토큰 3분할 UI, 연료계, telemetry 시각화 (v0.6)
- 그래프/에디터/diff 뷰
- 스타일 개편 (기존 룩 유지, 새 화면도 기존 톤)

## 테스트 / 검증
- main process의 파일 읽기·쓰기 로직은 단위 테스트
  (깨진 state.json, 없는 폴더, 승인 파일 기록 형식)
- 수동 검증 절차를 README(또는 desktop/README)에 명시:
  실제 slice 하나를 UI로 승인해 파이프라인이 진행되는 것 확인
- 기존 Python pytest 전량 무손상 (Python 쪽 무변경 증명)

## Done Criteria
- ☐ 실제 repo의 slice 목록·상태가 UI에 뜬다
- ☐ waiting_approval slice의 plan을 UI에서 읽을 수 있다
- ☐ UI 버튼 승인으로 대기 중인 파이프라인이 실제로 진행된다 (수동 확인)
- ☐ 반려+사유가 규약 형식으로 기록된다
- ☐ preload 신규 API는 writeApproval 하나뿐이다
- ☐ 단위 테스트 통과 + Python 테스트 전량 무손상