---
approval: plan
---
# Pipeline 결함 수정 — 첫 실전(guard-inspect)에서 발견된 3건

## 배경
2026-08-15 첫 실전 slice에서 파이프라인 자체의 결함 3건이 실측되었다.
산출물(가드 테스트 20종)은 사람 손 실행으로 345 pass 확인 — 코드 문제 아님.

## 결함 1 (치명) — test 단계가 테스트 명령을 실행할 수 없다
- 증상: `npm run test:*`, `node --test` 전부 "This command requires approval".
  implement/test 두 단계 모두 검증 명령을 한 번도 실행 못 함.
- 원인: --permission-mode acceptEdits는 편집만 자동 승인, Bash는 승인 대상.
- 요구: pipeline이 test 단계 실행 전에 대상 repo의 .claude/settings.json에
  테스트 명령 allow 규칙(npm test, npm run test:*, node --test)이 보장되도록 한다.
  구현 방식(설정 병합 / --allowedTools 전달 등)은 plan에서 선택지 비교 후 제안.
  무제한 Bash 허용(--dangerously-skip-permissions)은 금지.

## 결함 2 — live.json 쓰기의 Windows PermissionError
- 증상: reader가 파일을 잡은 순간 os.replace가 WinError 5로 크래시, run 전체 사망.
- state.json에는 이미 재시도가 있으나 live.json은 코어 storage.py 경로라 없음.
- 요구: write_json_atomic에 PermissionError 재시도(최대 1초) 추가.
  ※ 코어 수정 예외를 승인한다 — 동작 변경이 아닌 Windows 잠복 버그 수정이므로.
  기존 회귀 테스트 전부 통과 + Windows 재시도 회귀 테스트 추가로 증명할 것.

## 결함 3 — resume 세션이 낡은 실패 기억으로 재시도를 포기
- 증상: 권한 환경이 바뀌어도 resumed session이 "아까 막혔다"는 기억으로
  두어 번 만에 FAIL 결론 (attempt 3, 41초).
- 요구: 같은 stage 재시도 시 세션 정책 재검토 — 최소한 N회 실패 후 재시도는
  새 세션으로 시작하는 선택지를 plan에서 검토·제안할 것.

## 개선 (여유 시) — --list/--resume-slice의 repo 미지정 UX
- cwd에 .aidev가 없으면 빈 결과 대신 "--repo를 지정하세요" 힌트 출력.

## 하지 않는 것
- v0.3(worktree 자동화), Planner, IDE 바인딩
- 코어 수정은 결함 2의 write_json_atomic 한 곳으로 한정

## Done Criteria
- 기존 pytest 143개 + 신규 회귀 통과
- fake_claude 기반으로 결함 1·3의 시나리오 테스트 존재
- 실전 재검증: guard-inspect worktree에서 test 단계만 재실행하여
  345 pass가 파이프라인을 통해 확인되고 slice가 done으로 끝남