---
approval: plan
max_turns: implement=120
setup: npm ci --prefix desktop
test_commands: npx --prefix desktop tsc --noEmit -p desktop
---
# Graph 탭 — 엣지 색·스타일 복구 (회귀 수정)

## 배경 (실측 증상)
graph-pan slice의 회귀 수정(amend1) 이후, 노드 선택 시 엣지
하이라이트가 렌더는 되나 색 구분이 유실됐다.
- 이전 정상 동작 (graph-visual slice가 정의): calls = 파란색 계열
  곡선, callers = 구분색(점선), 방향 화살표, 범례와 일치
- 현재: 무색/기본색 선으로만 렌더

## 요구사항
- graph-visual이 정의한 엣지 색·스타일 매핑(theme.css 정의 및
  EdgeLayer/렌더 코드의 클래스 부여)이 어디서 끊겼는지 찾아 복구
- 범례(legend)와 실제 렌더 색이 일치해야 한다
- 팬·노드 드래그·선택·디밍·줌은 그대로 유지 (재회귀 금지)
- 다른 변경 금지

## Done Criteria
- 노드 선택: calls 파란 곡선 + callers 구분색 + 화살표, 범례 일치
- 팬/드래그/줌/디밍 전부 기존 동작 유지
- tsc·pytest 통과
