---
approval: plan
max_turns: implement=200
test_commands: python -m pytest -q
---
# 소탕 2호 ? 임시파일 가드 + 실측 수리 4건

## 배경 (전부 실측)
잔재 커밋 3회(.tsscratch, reindent_tmp.py, reindent.py) ? 규약
문구로 안 막힘. 상향 미반영 2회(140 상향 후 80으로 발사, trace·
gen2b). 폴백 test FAIL 시 failure.md 미생성 1회. requirement
인코딩 오류 시 traceback 노출 1회.

## 요구사항
1. 임시파일 기계 검출: verify(엔진) 단계에서 이번 slice가 신규
   추가한 파일 중 임시 냄새 이름(tmp, scratch, reindent, debug,
   _bak 등 ? 정확한 패턴 목록은 plan에서 확정) 검출 시
   test FAIL 처리 + 파일 목록을 사유로 표시
2. 상향 미반영 수정: requirement front matter의 max_turns 변경이
   커밋된 후 발사되는 stage가 항상 최신값을 읽도록. 원인 조사
   포함 (state.json에 발사 시점 값이 캐싱되는지, worktree/본진
   어느 쪽 tasks 파일을 읽는지) ? 재현 테스트 필수
3. 폴백 test(세션 경로)가 FAIL 판정 시에도 failure.md 생성 ?
   verify 엔진 경로와 동일 규격(사유·좌표)
4. requirement 파일이 utf-8이 아닐 때 traceback 대신 한 줄 에러:
   경로 + "utf-8 인코딩 확인" 안내 (exit code 비정상 유지)
5. tasks/functiondb-implement-context-ab.md를 tasks/specs/ 로
   이동하고 git mv로 커밋 ? 우산 명세(발사 금지 문서)를 발사용
   폴더에서 분리. 이동 후 파일 첫 줄에 "우산 명세 ? 직접 발사
   금지, 2-1~2-4로 분해 발사" 주석 추가

## 하지 않는 것
- UI / 브리핑·그래프 변경 / shell I/O 계측(2-3 몫) / 새 기능

## Done Criteria
- fake: 임시파일 포함 slice가 FAIL + 목록 표시
- 상향 재현 테스트: 커밋된 상향값으로 발사됨을 증명
- 폴백 FAIL 시 failure.md 존재 / 비utf8 시 친절 에러 한 줄
- specs/ 이동 완료 + 발사 금지 주석
- 기존 pytest 전량 통과
