---
approval: plan
max_turns: implement=140
---
# Rollback Engine — 의미 단위 되돌리기

## 배경
철학 0조: "일단 만든다, 언제든 롤백된다, 그래서 거침없다."
현재 되돌리기는 사람의 git 지식에 의존한다(reset? revert? 어느 해시?).
커밋에 의미(slice/stage)가 박혀 있으므로 되돌리기도 의미 단위로 한다.
다투척 모델(사후 감독)의 안전망이자, 도장 성적표(번복률)의 데이터 원천.

## 요구 동작

### 1. stage 롤백 (진행 중/실패 slice)
- `aidev pipeline --repo <r> --rollback <slice-id> --to <stage>`
- slice 브랜치를 해당 stage 커밋으로 reset (로컬 전용이므로 reset 허용)
- state.json 동기화: 해당 stage 이후를 미실행 상태로 되감기
  (attempts/커밋 기록은 이력으로 보존 — 지우지 않고 "되감았음"을 기록)
- 이후 --resume-slice로 그 지점부터 재진행 가능해야 함

### 2. slice 철회 (merge된 slice)
- `aidev pipeline --repo <r> --revert-merge <slice-id>`
- base 브랜치에 merge revert 커밋 생성 (push됐을 수 있으므로 revert만,
  reset 금지)
- slice status → "reverted" (열린 스키마 — reader 하위 호환 확인)
- 충돌 시 자동 해결 금지, 중단 후 사람에게 보고

### 3. 공통 규칙
- 모든 롤백은 실행 전 현재 지점 기록 + 복구 명령 출력 (discard 스타일)
- 번복 기록: .aidev/slices/<id>/에 rollback 이력 (시각·대상·사유(옵션))
  → 미래 Ledger의 번복률 원천
- 마이그레이션 파일이 대상 범위에 있으면 경고:
  "이 롤백 범위에 마이그레이션 N개 — DB 적용 상태는 되돌려지지 않음"
- epic 되감기는 이번 범위 제외 (설계만 plan에서 스케치, 구현 금지)

## 하지 않는 것
- epic 단위 되감기 구현 / UI (v0.6.2 Fleet의 딸깍이 나중에 이 명령을 호출)
- DB 마이그레이션 자동 롤백 (경고만)
- push된 slice 브랜치 처리 (slice 브랜치는 로컬 전용이 원칙)

## Done Criteria
- fake 기반: stage 롤백 → 재진행 완주 시나리오
- fake 기반: merge된 slice revert → base에 revert 커밋 + status 갱신
- 복구 명령 출력 확인 / 번복 기록 파일 생성 확인
- 마이그레이션 경고 시나리오 (더미 마이그레이션 파일로)
- 기존 pytest 전량 통과 / Windows 완주 / README 갱신

## 완료 보고
RESULT 형식. 임시 파일 잔재 없이.