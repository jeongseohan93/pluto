---
approval: plan
max_turns: implement=40
---
# 탭 정리 — Function graph를 Graph 탭으로 승격

## 요구사항
- 신규 Function graph 뷰를 'Graph' 탭 자리로 승격: 탭 이름은
  'Graph' 하나만 남기고 그 내용이 Function graph 뷰가 된다
- 구 mock Graph 화면은 라우팅/탭에서 제거 (파일 삭제는 금지 —
  연결만 끊는다)
- 다른 UI·로직 변경 금지

## Done Criteria
- 탭 목록에 Graph 단일 탭, 내용 = Function graph (DB 데이터)
- 구 mock 그래프는 어느 탭에서도 접근 불가
- 기존 pytest 전량 통과 (엔진 무변경)
