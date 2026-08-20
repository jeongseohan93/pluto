---
approval: plan
max_turns: implement=140
test_commands: npx --prefix desktop tsc --noEmit -p desktop
---
# UI 정리 — 탭 정돈 + 그래프/코드 스플릿 뷰

## 배경 (실측)
탭바에 mock 탭 5개(Graph·Code·Test·Diff·Browser, 전부 DEMO)가
실기능 탭과 섞여 혼란. Monaco 코드뷰어가 열리는 창 배치도 불편.

## 요구사항
### 1. 탭 정돈
- mock 탭 5종(Graph[DEMO]·Code[DEMO]·Test[DEMO]·Diff[DEMO]·
  Browser[DEMO])을 탭바에서 제거 — 파일 삭제 금지, 라우팅 분리만
- 남는 탭: Plan / Function graph (이름을 'Graph'로 개명 — 이제
  유일한 그래프니까) / 이후 실기능 탭이 생기면 이 줄에 합류
### 2. 그래프/코드 스플릿 뷰
- 노드 더블클릭 시: 화면이 좌(그래프)/우(Monaco) 스플릿으로 전환
  — 코드가 그래프를 덮지 않는다
- 사이 경계 드래그로 리사이즈, 비율 기억(세션 내)
- 코드 패널 [닫기] → 그래프 전체 화면 복귀
- 다른 노드 더블클릭 → 우측 Monaco만 갱신 (스플릿 유지)
- 인스펙터(우측 사이드)와의 관계: 스플릿 시 인스펙터는 자동
  접힘(공간 확보), 수동으로 다시 펼 수 있음

## 하지 않는 것
- mock 화면 기능화 (Test·Diff 등은 미래 몫) / 멀티 탭 에디터 /
  별도 창 분리

## Done Criteria
- 탭바: Plan / Graph 둘만 (DEMO 탭 소멸)
- 더블클릭 → 스플릿, 리사이즈·닫기·노드 전환 동작
- 기존 기능(팬·선택·trace·미니맵) 재회귀 없음
- tsc·pytest 통과
