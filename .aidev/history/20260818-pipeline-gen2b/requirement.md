---
approval: plan
max_turns: implement=120
---
# 파이프라인 2세대 B — 규격 마무리 + 실측 수리

## 배경
gen2 A에서 §4(Write차단)·§7(명세검사) 선구현 완료. 잔여 규격 2건과
A 실전(8/17~18) 및 맥 이식 검증에서 실측된 결함 5건을 청산한다.

## 요구사항

### 1. 피드백 문서 규격
- 반려 확장: approvals/rejected-detail.md (대상 좌표/문제/요구/범위,
  "부분 반려(기본)/전면 반려" 등급) — 기존 한 줄 형식 하위 호환
- 승인 코멘트 보존: "approved: <문구>"의 문구를 state에 기록하고
  implement 및 이후 모든 재시도/repair 프롬프트에 전달
- plan 재실행 시 직전 plan 전문 + 반려 문서 주입, 지시는
  "반려 항목만 수정, 나머지 유지" (차분 재계획)

### 2. 모델 믹스
- front matter `model: <stage>=<model>` (max_turns와 같은 문법)
- CLI 오버라이드 옵션. 기본값 현행 유지. diagnose 단계 별도 지정 가능

### 3. 실측 수리 5건
- a. front matter 미지 키: 조용한 무시 금지 — 무시하되 경고 1줄
  (실측: model 키가 max_turns 적용을 깨뜨림, 8/17)
- b. watch last: STALE(무갱신) run 제외 + --repo 스코프 존중
  (실측: 이틀 전 죽은 run을 물음, 8/18)
- c. 승인 조건 승계: approved 뒤 문구가 재시도 세션에 전달 안 됨
  (실측: scope=A 조건이 attempt 2에서 무시됨 → 선구현 사태)
  — 1번의 승인 코멘트 보존과 같은 뿌리, 통합 구현 가능
- d. discard 원자성: worktree 제거 실패 시 git 등록 해제만 되고
  고아 폴더가 남아 resume 불가 (실측: 8/18) — 제거 가능 여부 선검사
  또는 실패 시 등록 복원, 에러에 복구 명령 안내
- e. requires-python >= 3.10 명시 (pyproject + README)
  (실측: 맥 py3.9에서 verify.py 사망, 8/18)

## 하지 않는 것
- Write차단·명세검사 재작업 / UI / v0.5 계열(인덱스·브리핑)

## Done Criteria
- fake: 반려상세→차분재계획 동작 / 승인 코멘트가 attempt 2+ 프롬프트에 존재
- model 지정이 실행 인자 반영 / 미지 키 경고 출력
- watch가 살아있는 run만 선택 / discard 실패 시 재등록 상태 보존
- 기존 pytest 전량(403+) 통과 / README 갱신

## 완료 보고: RESULT 형식, 임시 파일 잔재 없이
