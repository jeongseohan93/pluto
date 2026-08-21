\# NIGHT 개인 조사 결과 오버레이 (GUARD / WITCH\_HUNTER)



\## 배경

backend는 이미 완료: prepareNightResolution이 GUARD/WITCH\_HUNTER의 개인 결과를

privateResults로 계산하고, socket 계층이 night\_action\_result 이벤트로 본인에게만 emit한다.

frontend는 useInGameResolveNight에서 night\_action\_result를 수신해 로컬 state에 저장하지만,

night\_result\_applied(DAY 전이) 수신 시 invalidate()로 즉시 null이 되어 화면에 표시될 틈이 없다.

이번 작업은 frontend만 다룬다. backend는 수정하지 않는다.



\## night\_action\_result payload

\- GUARD:        { gameId, dayIndex, actionType: "INVESTIGATE", targetId, team }   // team: "JOKER" | "CITIZEN"

\- WITCH\_HUNTER: { gameId, dayIndex, actionType: "CONFIRM",     targetId, role }   // role: "JOKER"|"CITIZEN"|"DOCTOR"|"GUARD"|"WITCH\_HUNTER"



\## 요구사항

1\. 결과 보존: nightPrivateResult를 ingameStore의 canonical state로 옮긴다.

&#x20;  night\_result\_applied 수신 시 지우지 않는다. clear 시점은 (a) 개인 결과 오버레이 확인 버튼,

&#x20;  (b) phase가 NIGHT로 재진입할 때, (c) gameId 변경 시 세 곳뿐이다.

2\. 표시 정규화: utils/reduceInGameNightPrivateResult.js (순수 함수).

&#x20;  payload + players → { kind: "INVESTIGATE"|"CONFIRM", targetNickname, label } 형태.

&#x20;  label 문구는 ingameRoleRevealData.js의 getInGameRoleRevealDisplay를 재사용한다:

&#x20;  - INVESTIGATE: `${nickname} 님은 ${teamLabel}입니다`  (teamLabel = JOKER→"광대 진영", CITIZEN→"시민 진영")

&#x20;  - CONFIRM:     `${nickname} 님의 역할은 ${name}입니다`

&#x20;  targetId가 players에 없으면 null을 반환한다(표시하지 않음).

3\. 오버레이: components/nightPrivateResult/InGameNightPrivateResultOverlay.jsx.

&#x20;  기존 InGamePhaseEntranceOverlay와 동일한 parchment 스타일·확인 버튼·Escape 닫기 계약을 따른다.

4\. 우선순위: useInGameOverlayStack의 2번(killReveal)과 3번(phaseEntrance) 사이에 삽입한다.

&#x20;  - nightPrivateResult는 hold: roleReveal.open || killReveal.open

&#x20;  - phaseEntrance의 hold에 nightPrivateResult.open을 추가한다

&#x20;  - nightTurn의 hold에도 nightPrivateResult.open을 추가한다

&#x20;  - interactionBlocked에 nightPrivateResult.open을 포함한다

&#x20;  주석의 우선순위 목록도 갱신한다.

5\. useInGameResolveNight의 로컬 nightActionResult state와 handleResult는 store 갱신으로 대체한다.

&#x20;  기존 dayIndex 단조 증가 폐기 규칙(appliedNightDayIndexRef 비교)은 유지한다.



\## 수정 금지

\- backend/\*\* 전체

\- DAY Enter-to-send 채팅 관련 코드

\- 밤 턴 안내 문구("경호원의 시간입니다" 등) — 역할명 불일치는 별도 작업



\## 검증

\- reduceInGameNightPrivateResult 단위 테스트: INVESTIGATE/CONFIRM 각 진영·역할, 미존재 targetId → null

\- ingameStore 테스트: night\_result\_applied 후에도 nightPrivateResult 유지, NIGHT 재진입·gameId 변경 시 clear

\- useInGameOverlayStack 테스트: killReveal 열림 → privateResult 대기, privateResult 열림 → phaseEntrance·nightTurn 대기

\- 기존 frontend 전체 테스트 PASS, frontend build PASS

