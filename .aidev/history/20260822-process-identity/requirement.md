\---

approval: plan

max\_turns: implement=100

test\_commands: python -m pytest -q

\---

\# process identity — lock 신원 강화와 안전 종료 (2-1.5)



\## 배경 (리뷰 실측)

현재 SliceLock은 pid만 기록 — pid는 재사용되므로 "죽은 pid"

판정이 재사용된 무관 프로세스를 오인할 수 있고, 자동 종료를

붙이면 남의 프로세스를 죽일 위험이 있다 (Codex 리뷰 지적).

2-1b(리뷰 루프 자동화)의 안전 종료가 이 신원 정보에 의존한다.



\## 요구사항

1\. lock 기록 확장: pid + 프로세스 생성 시각 + slice\_id + repo

&#x20;  경로. POSIX는 추가로 process group id (독립 그룹으로 발사된

&#x20;  경우). Windows는 프로세스 생성 시각 조회로 동일성 확인

2\. 생존·동일성 판정: pid 생존만이 아니라 생성 시각 일치까지

&#x20;  확인해야 "같은 프로세스"로 판정한다. pid는 살아 있는데 생성

&#x20;  시각이 다르면 재사용된 pid로 간주 — stale로 취급 (자동 인수

&#x20;  가능)

3\. 안전 종료(--stop 강화): 신원 요소(pid·생성시각·slice\_id·repo)

&#x20;  가 모두 일치할 때만 종료를 실행한다. 구형 lock(신원 정보 없는

&#x20;  형식)은 자동 종료를 거부하고 수동 정리 안내 메시지를 낸다

4\. POSIX: 독립 process group으로 발사된 경우에만 killpg를

&#x20;  사용한다. 부모 pid 단독 SIGTERM/SIGKILL 금지

5\. 하위 호환: 구형 lock을 만나도 crash 없이 보수적으로 동작

&#x20;  (자동 인수·자동 종료 모두 거부, 안내만). 기존 detach·stale

&#x20;  인수·--stop 경로의 기존 테스트는 전부 통과 유지



\## 하지 않는 것

\- --apply-review 본체 (2-1b 몫) / lock 파일 위치·이름 변경 / UI



\## Done Criteria

\- fake: 신원 4요소 일치 → 종료 성공

\- fake: pid 같고 생성 시각 다름(재사용 모사) → 종료 거부 +

&#x20; stale로 인수 가능

\- fake: 구형 lock → 자동 조치 거부 + 안내 메시지

\- 기존 detach·stop·인수 테스트 전량 통과 + pytest 전량 통과

