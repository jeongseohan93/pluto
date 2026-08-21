\---

approval: plan

max\_turns: implement=160

test\_commands: python -m pytest -q

\---

\# 리뷰 루프 반자동화 (2-1b-lite) — 리뷰 패킷 생성 + 반영·재발사 원큐



\## 배경 (실측)

work-order slice에서 외부 모델(Codex) 리뷰 5라운드를 돌며 매 라운드

사람이 수행한 노동: plan 복사 → 리뷰 답변 복사 → discard →

메모장으로 requirement 수정 → 커밋 → 재발사. 라운드당 \~15분,

하루가 소모됐다. 리뷰 품질은 높았으므로(§H 21건 발견) 왕복을

기계화한다. 또한 리뷰어가 상차림 없이 repo 전체를 읽어 외부 모델

사용량이 과도했다 — 리뷰 패킷에 브리핑을 동봉해 해소한다.



\## 요구사항



\### 1. 리뷰 패킷 자동 생성 (plan 완성 시)

\- plan stage가 done이 되면 엔진이 slice 디렉터리에

&#x20; review-packet.md를 자동 생성한다. 내용:

&#x20; - 리뷰 프롬프트 헤더 (고정 템플릿: 심각도 표기 규칙,

&#x20;   "구체 수정안 형식으로", "칭찬 생략" 등 — 별도 템플릿 파일로

&#x20;   두어 사람이 편집 가능)

&#x20; - plan.md 전문

&#x20; - 지시서에 등장하는 대상 파일·symbol의 브리핑 발췌

&#x20;   (기존 briefing 생성 코드 재사용 — 함수 명세·시그니처·

&#x20;   호출관계. 리뷰어가 repo 전체를 읽지 않아도 되게)

&#x20; - 발췌 말미에 고정 문구: "발췌에 없는 정보가 필요하면 필요한

&#x20;   함수·파일 목록을 요구하라"

\- work order가 없는 legacy plan에서는 패킷 생성을 건너뛴다



\### 2. 리뷰 반영·재발사 원큐 명령

\- 사람이 리뷰 답변을 slice 디렉터리의 review-N.md로 저장한 뒤:

&#x20; aidev pipeline --repo <repo> --apply-review <slice-id>

\- 명령이 수행하는 것 (순서대로, 원자적으로):

&#x20; 1. slice의 requirement 원본(tasks/\*.md)에

&#x20;    "### 리뷰 반영 N차 (자동 편입)" 절을 추가하고 review-N.md

&#x20;    본문을 그 아래에 그대로 삽입 (해석·요약하지 않는다 —

&#x20;    원문 보존, 사람이 발사 전 편집 가능)

&#x20; 2. git add + commit (메시지 자동: "task: <이름> 리뷰 N차 반영")

&#x20; 3. 기존 slice discard (running이면 프로세스 종료 후 lock 정리

&#x20;    포함 — background-run의 pid 기록·정지 배선 재사용)

&#x20; 4. 같은 requirement로 재발사 (새 slice)

\- 재발사 없이 반영만 하는 --apply-review-only 변형 제공

\- rejected 상태 전이 경로는 사용하지 않는다 (알려진 버그 회피 —

&#x20; discard+재발사 방식만)



\### 3. 안전 가드

\- --apply-review는 대상 slice가 merge된 적 없을 때만 동작

&#x20; (merge된 slice의 requirement 소급 수정 방지)

\- review-N.md가 비어 있거나 없으면 명령 거부 + 안내

\- requirement 파일 인코딩은 utf-8로 기록 (기존 인코딩 가드 준수)



\## 하지 않는 것

\- 리뷰어 모델 직접 호출 / CLI 어댑터 (2-1b-full — 에픽 후)

\- rejected/재계획 경로 수리 (별도 소탕 몫)

\- 리뷰 답변의 자동 해석·요약·분류 (원문 삽입만)

\- UI (명령줄 우선 — 앱 버튼은 후속)



\## Done Criteria

\- fake plan 완성 → review-packet.md 생성, 지시서 대상 함수의

&#x20; 브리핑 발췌 포함 확인

\- legacy plan(지시서 없음) → 패킷 생성 안 함

\- review-1.md 저장 → --apply-review → requirement에 절 추가·

&#x20; 커밋·구 slice 정리·재발사까지 자동 수행 확인

\- running 상태 slice에 --apply-review → 프로세스 종료·lock 정리

&#x20; 포함 정상 동작

\- merge된 slice에 --apply-review → 거부

\- 기존 pytest 전량 통과

