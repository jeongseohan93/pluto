---
approval: plan
max_turns: implement=140
---
# Pipeline 자기수리 2차 — 실측 결함 4건 + 신규 채널 1건 + 빈칸 5개

## 배경
2026-08-15~16 실전(slice 5개 완주)에서 축적된 실측 노트의 청산.
모든 항목은 실제 발생 사례에 근거한다. 추측성 개선 금지.

## 결함 1 (최우선) — max-turns 80이 대형 slice를 3연속 죽였다
- 실측: implement 81턴 사망 3회 (자기수리1차/v0.3/v0.4), 매회 resume
  비용 $3~4 추가. 사인은 전부 "일 다 못 끝내고 강제 종료".
- 요구:
  a. stage별 max-turns 설정 가능 — requirement front matter
     (예: `max_turns: implement=140`) 및 CLI 옵션. 기본값은 현행 유지.
  b. plan 산출물의 변경 규모(파일 수·신규 테스트 수)가 임계 초과 시
     승인 화면/로그에 경고 한 줄 ("이 plan은 기본 턴 예산 초과 가능").
     임계값과 문구는 plan에서 제안.

## 결함 2 — allowedTools가 setup 경로 문자열에 과결합
- 실측: setup이 `npm ci --prefix backend`면 frontend 테스트 명령
  전부 거부 → 에이전트가 검증 불가 상태로 계획 이탈(교착버그 slice).
- 요구: 검증 명령을 setup에서 파생하지 말고 별도 선언으로 분리:
  front matter `test_commands:` (복수 허용). 선언된 명령만 allow.
  미선언 시 현행 동작 유지. 문법은 plan에서 확정.

## 결함 3 — --list/--resume-slice의 repo 미지정 UX
- 실측: cwd에 .aidev 없으면 빈 결과만 출력, 사용자가 원인 파악 못 함.
- 요구: "--repo를 지정하세요" 힌트 + 최근 사용 repo를 도구 data에
  기억하여 후보 제시. 자동 적용은 금지(명시가 원칙), 제시만.

## 결함 4 — --merge 후 원격 백업이 수동
- 실측: merge 후 push를 매번 손으로. 잊으면 로컬 유일본.
- 요구: `--merge <id> --push` opt-in 옵션 — base 브랜치만 push.
  slice 브랜치는 절대 push하지 않는다. 기본값은 push 안 함.

## 신규 채널 — --amend (다투척 모델의 주력 동선)
- 배경: 완주한 slice에 사후 수정 지시를 던질 방법이 "새 requirement
  작성"뿐. 사후 감독 모델에선 이게 최빈 동작이 된다.
- 요구: `aidev pipeline --repo <r> --amend <slice-id> "<지시문>"`
  - 대상 slice의 기존 worktree/브랜치 위에 수정 사이클 실행
  - 지시문 + 원 requirement + 직전 RESULT를 컨텍스트로 전달
  - 기존 stage 모델과의 관계(amend가 stage인가 새 attempt인가),
    state.json 표현, 커밋 메시지 형식(