---
approval: plan
max_turns: implement=180
test_commands: python -m pytest -q
---
# 작업 지시서 — plan 산출물의 3동사 구조화 + 기계 검증

## 배경
구현 왕복의 최대 원인은 모호함 — plan의 산문 지시가 해석 여지를
남긴다. plan 산출물에 파일·함수 단위 지시서를 강제하고, 엔진이
기계 검증한다. (우산 명세 tasks/specs/ 참조 — §1 해당분만)

## 요구사항

### 1. 작업 지시서 절 (plan 산출물에 강제)
- 고정 열 마크다운 표. 항목마다: 동사(CREATE|MODIFY|REFERENCE) /
  대상 경로 / symbol(선택) / 한 줄 책임
- MODIFY의 symbol은 선택적이다.
  - symbol이 있으면 Function DB에서 정확히 하나로 해석되는지 검증
  - symbol이 없으면 파일 단위 MODIFY — 파일 실존만 검증
  - symbol 없는 항목에서 엔진이 수정 함수나 주변 함수를 추정하지
    않는다
- REFERENCE도 동일: symbol 있으면 유일 해석 검증, 없으면 실존만
- CREATE 대상은 미존재 확인 (충돌 검사)
- 모든 target과 사유 표 경로는 정규화된 repo-relative 경로여야
  하며, 경로 탈출·symlink/junction 탈출·중복·동사 충돌은 FAIL한다

### 2. 엔진 기계 검증 (plan 커밋 직전, 세션 0)
- 위 §1 검증 전부 수행 — 실패 시 plan FAIL + 사유 목록
  (승인 게이트 전에 걸러짐)
- 지시서 파싱은 결정론 (고정 열 표 파싱, LLM 파싱 금지)

### 3. implement 동봉
- 지시서를 구조화 형태로 프롬프트에 동봉
- 규약 문구: "지시서에 없는 파일 수정 금지, 부득이한 수정은
  사유 표로 선언"

### 4. 사후 대조 (파일을 변경한 모든 실행 — implement·repair·amend
     — 의 stage commit 직전마다 수행, 최종 diff 기준)
- CREATE에 없는 신규 파일 → 즉시 FAIL
- MODIFY에 없는 파일 삭제 → 즉시 FAIL (삭제하려면 해당 경로를
  MODIFY로 미리 선언)
- MODIFY에 없는 기존 파일 수정 → FAIL 아님, unplanned_modified로
  분류. 에이전트는 고정 표(| 경로 | 사유 |)로 항목별 한 줄 선언
- 사유 표는 해당 실행 최종 응답의 "## 범위 밖 수정 사유" 절에서만
  고정 형식으로 파싱한다. unplanned_modified가 없으면 생략 가능
- 엔진은 실제 unplanned_modified 경로 집합과 사유 표 경로 집합의
  완전 일치를 검증 — 누락·중복·미존재 경로·여분 선언 전부 FAIL
- 사유 선언은 지시서를 확장하지 않는다 — 끝까지 unplanned로
  보존, 사람 승인 화면과 리포트에 표시
- amend의 경우 amend 지시문의 대상을 어떻게 지시서와 대조할지는
  plan에서 제안 (amend 지시 자체가 범위 정의를 겸하는 방향)

### 5. 하위 호환
- 기존에 승인된 legacy slice의 resume에만 하위 호환 적용
- 새 plan과 재계획(replan)은 작업 지시서가 없으면 FAIL한다

## 하지 않는 것
- 컨텍스트 트레이(2-2 몫) / 빈 파일 준비(2-4 몫) / plan 산문 전면
  금지 (지시서 절 추가지 양식 전체 개편 아님)
- REFERENCE를 읽기 allowlist로 쓰거나 지정 외 읽기를 실패 처리 —
  REFERENCE는 트레이의 입력 목록. 트레이 밖 읽기는 telemetry
  계측만

## Done Criteria
- fake: 지시서 포함 plan 통과 / 미존재 MODIFY 대상 → plan FAIL /
  지시서 밖 신규 파일 → verify FAIL / unplanned 수정 + 사유 표
  일치 → 통과, 불일치·누락·여분 → FAIL / 경로 탈출·동사 충돌 →
  FAIL / 지시서 없는 새 plan → FAIL
- legacy resume 하위 호환 동작 확인
- 기존 pytest 전량 통과
- agent test가 범위 밖 파일 수정 → FAIL
- test_commands가 tracked/untracked 파일 생성 → FAIL
- scope FAIL 후 resume → 같은 stage 재실행·재검증
- unplanned 파일 원복 → 현재 unplanned에서 제거 + 감사 이력 유지
- 신규 slice의 --no-worktree → 거부
- plan 수정 → amend 중단 → 재승인 → 같은 amend 재개 (데드락 없음)


### 6. 리뷰 반영 사항 (기계 차단 강건성 — 필수)
- diff_status는 git 실패·기준 commit 없음·미지 status를 예외로
  올리고 scope check를 FAIL시킨다 (fail-closed — 차단 장치에
  fail-open 금지)
- .aidev/ 통째 제외 금지: 엔진이 이번 slice에서 생성한 정확한
  파일만 제외. 그 외 .aidev 신규 파일은 신규 파일 FAIL로 잡는다
- scope check는 파일을 변경하는 모든 실행에 적용: implement,
  repair implement, amend implement, agent test, engine verify
  command 실행 후
- scope FAIL 시 해당 stage를 failed로 전환하고 commit하지 않는다.
  resume에서도 최종 scope check가 재실행되어야 한다
- 대조용 effective order = plan 지시서 + 지금까지 저장된 모든
  amends[].work_order의 누적 합산. state의 원본 지시서는 불변
- symbol 행이 하나라도 있으면 graph build/open/freshness 실패는
  plan FAIL. --no-graph는 symbol 없는 파일 단위 지시서에서만 허용
- 승인 직후 plan.md를 재파싱·재검증하고 digest를 저장한다.
  implement/resume/repair 직전 digest 불일치 시 재승인까지 중단
- 인식 가능한 작업 지시서·사유 절이 2개 이상이면 FAIL.
  fenced code block 안의 제목은 절로 인식하지 않는다
- git 파싱은 --name-status -z 및 status --porcelain=v1 -z 사용
  (공백·한글·인용 경로 안전)
- 경로 검증에 ADS 콜론, 제어문자, Windows 예약 장치명,
  segment 후행 점·공백 포함
- 테스트: 같은 파일 내 동명 bare name 2개 fixture로 ambiguous
  검증 / symbol 해석 0개 → plan FAIL 테스트

### 7. 2차 리뷰 반영 (상태 전이·보증 범위 — 필수)
- --no-worktree 우회 차단: legacy 기존 slice의 resume만 사후
  대조 skip 허용. 신규 work order slice에서 workspace가 None이면
  즉시 PipelineError로 거부한다
- amend 중 재승인 데드락 방지: digest 불일치로 plan 승인이
  stale 처리되면 plan_reapproval_pending 상태를 두고, amend
  필터보다 먼저 plan 게이트를 직접 처리한 뒤 같은 amend를 재개
- 보증 범위 명시: 사후 대조의 보증은 "Git이 관찰하는 worktree
  내부 변경"이다. worktree 밖 쓰기 차단은 범위 밖 — README에
  별도 안전 게이트 항목으로 등록만. "모든 신규 파일 즉시 FAIL"
  표현은 문서 전체에서 위 보증 범위로 한정해 서술
- unplanned 재계산: unplanned는 영구 누적하지 않는다.
  scope_checks는 감사 이력으로 누적하되, unplanned는 기준 커밋
  대비 현재 최종 diff와 effective_scope로 매번 재계산 (원복·정식
  MODIFY 전환된 경로는 현재 목록에서 제거, 감사 이력엔 유지)

### 8. 3차 리뷰 반영 (중복 기준·최종 diff·제외 목록 — 필수)
- 중복 기준 교정: 같은 (경로, symbol) 조합 중복 FAIL / 같은
  경로의 서로 다른 동사 FAIL / 같은 경로·같은 동사·서로 다른
  non-empty symbol은 허용 (함수 단위 지시서의 정상 형태) /
  symbol 없는 파일 단위 행과 같은 경로의 symbol 행 병용은 FAIL
- 최종 diff 산출 교정: base..HEAD에 porcelain을 덮어쓰지 않는다.
  임시 Git index에 HEAD를 read-tree하고 git add -A 후
  git diff --cached --name-status -z --no-renames <base>로
  실제 working tree 스냅샷을 base와 비교한다. 원복·삭제 후
  복원·추가 후 삭제 fixture 필수
- 제외 목록 재교정: .aidev/history/** , .aidev/graph/** 등
  디렉터리 단위 제외 금지. mirror_history가 실제 덮어쓸 정확한
  파일만 제외 (requirement.md, plan.md, slice.json, 본진 source가
  실존하는 failure/diagnosis/progress.md, 정확한 amend 번호).
  .aidev/graph는 Git ignore이므로 제외 목록에 불포함.
  .aidev/history/<slice>/evil.py 통합 테스트 추가
- 엔진 verify는 무관용: verify 명령 실행 직전 HEAD를 기준으로,
  직후 Git-observable 변경이 하나라도 있으면 즉시 FAIL (지시서
  선언 여부 무관 — 엔진 실행은 사유 주체가 없다). "선언된
  MODIFY 파일을 verify가 수정" 테스트 추가
- approval: none 경로: digest 불일치 시 사람 게이트를 만들지
  않는다 — 재검증·자동 재봉인만 수행. plan_reapproval_pending은
  미봉인 검사보다 먼저 처리하고, 재봉인 전에는 resume·amend
  모두 차단. digest 빈 값이 미봉인 우회로가 되지 않게 한다

### 9. 4차 리뷰 반영 (자기참조·digest 이원화·setup 지문·verify 2단)
- 신규 symbol 선언 금지: 기존 파일에 새 함수를 추가하는 경우
  지시서에 symbol을 비우고 파일 단위 MODIFY로 선언한다 (plan
  검증 시점에 존재하지 않는 symbol은 해석 0개로 FAIL하므로).
  plan 프롬프트 규약에 이 규칙을 명시한다
- digest 이원화: document_digest = sha256(plan.md 전체 바이트,
  재승인 판단용) / scope_digest = canonical work-order digest
  (범위 비교·계측용). ensure_plan_sealed와 scope_check의 재승인
  트리거는 document_digest 불일치 기준. approval: none만 자동
  재봉인
- setup 산출물 fingerprint: setup이 생성한 경로는 이름이 아니라
  setup 직후의 mode + blob hash를 저장한다. 현재 내용이 그
  fingerprint와 정확히 같을 때만 setup 산출물로 제외하고,
  달라지거나 삭제되면 정상 scope 대상으로 복귀. 이미 CREATE/
  MODIFY로 선언된 경로는 setup 제외를 적용하지 않는다
- 엔진 verify 2단 검사: verify 명령 실행 전 diff_status가 비어
  있지 않으면 "시작 전 작업트리 dirty"로 FAIL, 실행 후 변경이
  있으면 "verify가 저장소를 바꿨다"로 FAIL (상태 문자 비교가
  아니라 실행 전후 각각의 독립 검사). 테스트: verify가 파일을
  수정해 실패 → resume → 명령 재실행 없이 dirty로 재차 FAIL

