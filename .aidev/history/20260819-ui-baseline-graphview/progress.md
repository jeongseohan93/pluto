# PROGRESS - 20260819-ui-baseline-graphview · plan
2026-08-19T17:58:09+09:00

attempt 1   turn 86/80   (turn-budget 90%)

## Done
- (no file was edited)

## Remaining
- (nothing the plan names is untouched - check the plan itself)

## Last words

# UI 기초선 + Graph View v0 — 구현 계획

엔진(`aidev/*.py`)은 **읽기 전용**이다. 이 slice가 쓰는 코드는 전부
`desktop/` 안이고, 모든 버튼은 이미 존재하는 CLI 호출 또는 이미 존재하는
파일 읽기의 배선이다.

---

## 0. 읽고 확인한 계약 (이 계획이 지켜야 하는 것들)

| 사실 | 출처 |
|---|---|
| `read_decision`은 첫 비주석 줄만 본다. `approved: <조건>`의 뒷문장이 `Decision.reason`으로 살아 남는다 | `aidev/pipeline.py:891` |
| 현재 앱의 `parseDecision`은 approved의 뒷문장을 **버린다** (`reason: ''`) — A2에서 고쳐야 할 결함 | `desktop/src/main/aidev-store.ts:97` |
| `--merge`는 실패해도(체크아웃이 base가 아님, 더티, 충돌) `--discard`를 막지 않는다. 충돌만 `state["merge"]={"status":"conflict"}`로 남고, 그 외 거절은 **디스크에 아무 흔적도 남기지 않는다** | `pipeline.py:5953, 5974, 6491` |
| `discard_slice`는 merge 여부를 전혀 보지 않는다 → 가드는 UI 레벨이 유일하다 (요구사항 ★와 일치) | `pipeline.py:6491` |
| 사인 분류는 `quota` 우선 → 턴사 → 기타. `quota`는 엔진이 이미 판정해 `runs.json`의 `quota: true`로 저장한다. 턴사 표식은 `telemetry.json`의 `exact.result_subtype ∈ {error_max_turns, max_turns}` 또는 텍스트의 `TURN_MARKERS` | `recovery.py:24-30, 67, 85`, `pipeline.py:3168` |
| 한도 대기: `state["quota"] = {waiting, resume_at, retries, source}`, 이력은 `state["recovery"]["waits"]`, 자동복구 중단은 `state["recovery"]["stopped"] = {pin, detail}` | `pipeline.py:3125, 2811, 2845` |
| 진행 노트는 `<slice>/progress.md` | `pipeline.py:1077` |
| gra...

## Resume

    aidev pipeline --repo /Users/jeongseohan/pluto/ai-dev-orchestrator --resume-slice 20260819-ui-baseline-graphview
