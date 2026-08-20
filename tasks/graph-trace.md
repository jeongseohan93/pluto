---
approval: plan
max_turns: implement=140
test_commands: npx --prefix desktop tsc --noEmit -p desktop
---
# Graph 탭 ? Trace 모드 (호출 흐름 추적)

## 요구사항
- 노드 선택 상태에서 [Trace] 토글(버튼 또는 우클릭 메뉴):
  해당 함수에서 calls 방향으로 깊이 3까지 호출 사슬 하이라이트,
  무관 노드·엣지 디밍 (기존 디밍 인프라 재사용)
- 깊이는 UI에서 1~5 조절 가능 (기본 3)
- 역방향 토글: callers 방향 추적 ("누가 여기까지 오나")
- 사슬 계산은 DB 재귀 조회 (미해석 엣지는 점선 등으로 "여기서
  추적 끊김" 표시 ? 정직하게)
- Trace 해제 시 원상복귀 (선택 하이라이트 상태로)

## 하지 않는 것
- 의미 흐름(@flow 기반) / 런타임 추적 / 애니메이션 과다

## Done Criteria
- run_pipeline Trace: 깊이 3 사슬 하이라이트, 끊김 지점 표시
- 역방향 동작 / 깊이 조절 / 해제 복귀
- 기존 기능(팬·드래그·선택·디밍) 재회귀 없음
- tsc·pytest 통과
