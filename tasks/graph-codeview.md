---
approval: plan
max_turns: implement=180
test_commands: npx --prefix desktop tsc --noEmit -p desktop
---
# Graph 탭 ? 코드 뷰어 소환 (Monaco, readonly)

## 배경
"거주지는 그래프, 에디터는 소환" ? 노드에서 코드로 가는 배선.

## 요구사항
- 노드 더블클릭(또는 사이드 패널 [코드 보기] 버튼) → 코드 뷰어
  패널이 열리고 해당 파일 로드, 함수 시작 라인으로 스크롤,
  함수 범위(시작~끝 라인) 하이라이트
- 에디터: monaco-editor 사용, readonly 모드. 신택스 하이라이트
  (py/ts/tsx/js), 테마는 기존 다크 팔레트와 조화
- 파일 로드는 IPC로 (renderer에서 fs 직접 접근 금지 ? 기존
  Electron 보안 구조 준수)
- 뷰어 패널은 닫기 가능, 다른 노드 더블클릭 시 해당 위치로 전환
- callers/calls 목록에서 항목 클릭 시에도 같은 동작 (좌표 이동)

## 하지 않는 것
- 편집·저장 (readonly만) / 멀티 탭 / 검색(Monaco 내장 검색으로
  충분) / graph 갱신 연동

## Done Criteria
- run_pipeline 더블클릭 → pipeline.py 3310행 스크롤+범위 하이라이트
- callers 항목 클릭 → 해당 파일:라인 이동
- 대형 파일(pipeline.py 5000행+)에서 로드 지연 허용 범위
- tsc·pytest 통과
