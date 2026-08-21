---
approval: plan
max_turns: implement=220
test_commands: npx --prefix desktop tsc --noEmit -p desktop
---
# req 에디터 ? requirement 작성을 IDE에서

## 배경
프로토 정의 "IDE에서 다 된다"의 잔여 조각. 현재 requirement는
IDE 밖(메모장/터미널 heredoc)에서 작성 ? 이 동선을 IDE 안으로.

## 요구사항
- Pipeline 탭에 [새 요구사항] 버튼 → Monaco 편집기(이번엔
  editable)로 tasks/ 아래 새 md 작성 화면
- 템플릿 자동 삽입: front matter 골격(approval: plan /
  max_turns: implement=120 / test_commands 주석 예시) + 섹션
  뼈대(배경/요구사항/하지 않는 것/Done Criteria)
- 파일명 입력(자동 .md, tasks/ 고정 ? 경로 탈출 금지) → 저장 =
  git add + commit (메시지 자동: "task: <파일명>")
- 저장 후 [발사] 버튼 → 기존 발사 배선 재사용
- 기존 tasks/*.md 열어서 수정도 가능 (같은 편집기, 저장 = 커밋)
- 편집 범위 가드: tasks/ 밖 파일은 이 편집기로 열 수 없음
  (코드 편집 개방 아님 ? requirement 전용)

## 하지 않는 것
- 일반 코드 편집 개방 / tasks/specs/ (우산 명세) 편집 ?
  목록에서 제외 / 발사 로직 변경

## Done Criteria
- 새 요구사항 작성→저장(커밋)→발사가 IDE 안에서 완결
- 템플릿 자동 삽입 / tasks/ 경로 가드 동작
- specs/ 하위는 목록·편집 대상에서 제외
- 기존 기능 재회귀 없음 / tsc·pytest 통과
