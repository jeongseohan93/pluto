---
approval: plan
max_turns: implement=160
test_commands: python -m pytest -q
---
# 소형 수리 3건 — 계기판 오보·추적 범위·watch 잔가시

## 배경
실측 노트 청산 + 브리핑 생성기의 첫 실전 가동 검증을 겸한다.

## 요구사항
1. verify 보고의 passed 카운트 오보 수정
   실측: "python -m pytest -q  exit 0  (0 passed)" — 실제는 470여 개
   통과인데 0으로 표기. pytest -q 출력 파싱이 개수를 못 읽는 것으로
   추정. 정확한 개수 표기로 수정 (파싱 실패 시 "n/a"로, 0 오보 금지)
2. 변경 파일 추적의 repo 밖 경로 제외
   실측: ~/.claude/projects/.../MEMORY.md 가 Changed files에 2회 등장.
   워크트리 밖 경로는 목록에서 제외 (또는 별도 표시로 분리)
3. watch가 attempt 재시도 후에도 최신 run을 물도록 잔가시 정리
   (gen2 B의 STALE 제외가 들어갔으나, resume 직후 새 run 감지가
   한 박자 늦는 현상 — 재현되면 수정, 재현 안 되면 조사 결과만 보고)

## 하지 않는 것
- 신기능 / 브리핑·그래프 코드 변경 / UI

## Done Criteria
- verify 카운트가 실제 개수 표기 (fake로 검증)
- repo 밖 파일이 목록에서 제외되는 테스트
- 기존 pytest 전량 통과
