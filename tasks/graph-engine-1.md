---
approval: plan
max_turns: implement=160
test_commands: python -m pytest -q
---
# 그래프 엔진 1단계 — 파서 코어 + Function DB

## 배경 (설계 원칙)
"그래프 엔진은 사람에겐 시선(view), AI에겐 DB(query)다."
이 slice는 그 엔진의 데이터층: 코드+명세주석을 파싱해 Function DB를
만든다. 진실은 코드, DB는 파생물(소모품 캐시). 목표는 에이전트의
'탐색'을 '조회'로 대체하는 기반 — "찾지 않는다, 좌표로 요청한다."

## 요구사항

### 1. 파서 코어
- 대상 언어: Python + JavaScript/TypeScript(jsx/tsx 포함)
  (Pluto 자신과 조커를 커버 — 이 두 repo가 첫 고객)
- 추출 단위: 함수/메서드마다 —
  이름(qualname), 파일:라인 범위, 시그니처(파라미터),
  명세 주석, 호출 관계(같은 repo 내 해석 가능한 것만, best effort,
  미해석은 미해석으로 기록)
- 명세 주석 태그 규약 — "닫힌 코어, 열린 주변":
  · 코어 태그(고정, 도구가 의미를 해석): 기능 한 줄 / @param /
    @flow / @why — gen2 명세 검사 형식과 동일
  · 미지의 @태그: 해석 없이 수집·보존, DB에 저장하고 show 출력에
    그대로 표시 (front matter 미지 키 처리와 같은 원칙)
- 파싱 실패 파일은 건너뛰고 목록에 기록 (전체를 죽이지 않는다)
- 구현 기술(tree-sitter vs ast/정규식 혼합)은 plan에서 결정하되,
  의존성 추가 시 근거 명시

### 2. Function DB
- 산출물: .aidev/graph/ 하위 (형식은 plan에서 — SQLite 권장하나
  json이 단순하면 가. 단 쿼리 성능 근거 제시)
- 캐시 규약: 파일 해시(또는 mtime+size) 기반 — 코드가 진실,
  DB는 언제든 버리고 재생성 가능
- 전체 빌드: `aidev graph build --repo <r>`
- 증분 갱신: `aidev graph update --repo <r>` (변경 파일만 재파싱)
- 상태: `aidev graph status` (기준 커밋/파일 수/함수 수/
  명세 커버리지 %/파싱 실패 목록/미지 태그 통계)

### 3. 조회 동사 (최소 4개)
- `aidev graph show <함수명>` — 명세+시그니처+위치+호출/피호출
  (+미지 태그 표시)
- `aidev graph callers <함수명>` — 부르는 곳들 (파일:라인)
- `aidev graph calls <함수명>` — 부르는 것들
- `aidev graph summaries [--dir <경로>]` — 범위 내 함수
  이름+기능 한 줄 목록
- 출력은 토큰 효율 우선: 항목당 1~2줄, 좌표 필수
- 동명 함수 복수 매치 시 전부 나열 (경로로 구분)

### 4. 파이프라인 훅 (연결만, 소비는 2단계)
- stage 커밋 후 자동 증분 갱신 — 실패해도 파이프라인 계속 (경고 1줄)

## 하지 않는 것
- 브리핑 생성기 / plan 프롬프트 주입 (2단계)
- 그래프 화면 UI (3단계) / Graph Notes / codebase-map 스킬
- 커스텀 태그에 검사 규칙 붙이기 (미래 엔진 블록의 일 — 태그는
  데이터, 검사는 블록)
- 도메인 추론 / @origin 자동 / ERD / 고급 흐름 쿼리
- 조커 repo 대상 실행 (검증은 Pluto 자신으로)

## Done Criteria
- Pluto repo 전체 빌드 완주, status가 함수 수/커버리지 보고
- show/callers/calls/summaries가 실제 함수(예: run_pipeline,
  merge_slice)에 정확한 좌표로 응답
- 미지 태그(@custom 등) 포함 fixture: 보존·표시 확인
- 파일 수정 → update → 해당 함수만 갱신 (증분 증명)
- JS/TS 파싱: jsx 샘플 fixture로 함수 추출 검증
- 기존 pytest 전량 통과 / README 갱신

## 완료 보고: RESULT 형식, 임시 파일 잔재 없이
