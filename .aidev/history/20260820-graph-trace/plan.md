# Graph 탭 — Trace 모드 (호출 흐름 추적) — 구현 계획

## 0. 먼저 확인한 사실 (실측, 이 워크트리 기준)

| 확인 대상 | 결과 |
|---|---|
| `tasks/graph-trace.md` front matter | `approval: plan`, `max_turns: implement=80`, `test_commands: npx --prefix desktop tsc --noEmit -p desktop`. **`setup:` 없음** |
| `desktop/node_modules` | **없다** (`typescript`, `.package-lock.json` 모두 부재) |
| `desktop/tsconfig.json` | `{"files": [], "references": [...]}` — 솔루션 파일. `--build` 없이 `-p desktop`은 **0개 파일을 검사하고 통과**한다 |
| implement 단계 Bash 권한 | `pipeline.py:2372` — `cfg.test_commands`가 선언되어 있으므로 `declared`는 **선언된 명령만**이다. 즉 `Bash(npx --prefix desktop tsc --noEmit -p desktop)`, `Bash(npx --prefix:*)` + `SPEC_CHECK_TOOLS`(`Bash(python -m aidev.specs)`, `Bash(python -m aidev.specs:*)`). **`pytest`는 허용 목록에 없다** (`TEST_COMMAND_TOOLS`는 `else` 가지에서만 붙는다) |
| `desktop/package-lock.json` | `monaco-editor` 항목 있음 — 직전 slice의 후속 조치는 끝났다. lock은 손대지 않는다 |
| `GraphDB` 스키마 | `calls(caller_id, callee_name, callee_raw, lineno, resolved_id)`, 인덱스는 `idx_calls_callee(callee_name)`, `idx_calls_caller(caller_id)`, `idx_func_name(name)`. **`resolved_id`에는 인덱스가 없다** (`db.py:91-96`) |
| `readGraphIndex`의 `edges` | `resolved_id IS NOT NULL` + `JOIN functions dst`로 걸러진 목록. **미해석 호출은 렌더러에 아예 도달하지 않는다** (`graph-store.ts:176-185`) |
| 기존 디밍 인프라 | `focusMarks` → `focusBoxes` → 박스 `opacity BOX_DIM(0.3)`, `FileLinks opacity AGGREGATE_DIM(0.18)` (`FunctionGraphSurface.tsx:504-562, 1028, 1062`) |
| `run_pipeline` 실제 좌표 | `aidev/pipeline.py:4277` |
| `--color-warn` | `theme.css:26` `#c39331` — 존재한다. `layout.test.ts:1158-1168`의 토큰 검사에 그대로 쓸 수 있다 |

**이 계획이 서 있는 전제(정직하게):** `node_modules`가 없고 `setup:`도 없으므로, 선언된 `test_command`는 **아무것도 검사하지 않고 통과**한다. `npm test --prefix desktop`(진짜 typecheck + `node --test`)은 이 워크트리에서 실행할 수 없다. §6이 이를 우회하지 않고 다룬다. 그래서 이 계획은 **로직을 최대한 `ui/` 밖의 순수 함수로 밀어 넣는다** — 지금 못 돌려도 나중에 `node --test`가 실제로 검증할 수 있는 자리에 두기 위해서다.

---

## 1. 설계 요지

### 1.1 왜 IPC 왕복인가 — 메모리 인접행렬로는 "끊김"을 말할 수 없다

렌더러에는 이미 `buildAdjacency(index.edges)`가 있고, 이것으로 깊이 3 BFS를 도는 것은 IPC 없이 즉시 가능하다. **그런데도 DB 재귀 조회를 쓴다.** 이유는 성능이 아니라 정직성이다:

`readGraphIndex`의 `edges`는 `JOIN functions dst ON dst.id = c.resolved_id` 로 만들어진다(`graph-store.ts:176-185`). 즉 **미해석 호출(`resolved_id IS NULL`)과 사라진 id를 가리키는 호출은 렌더러에 존재조차 하지 않는다.** 요구사항이 요구하는 "여기서 추적 끊김"은 정확히 그 버려진 행들이다. 메모리 그래프로 계산한 사슬은 끊김을 **구조적으로 표시할 수 없다**. 그래서 사슬도 끊김도 한 번의 DB 조회에서 같이 나온다 — 요구사항의 "사슬 계산은 DB 재귀 조회"는 이 사실의 다른 표현이다.

### 1.2 끊김은 두 종류이고, 둘을 구분한다

`calls` 방향에서 따라갈 수 없는 호출부를 전부 "끊김"이라 부르면 `run_pipeline` 깊이 3에 수백 개의 마커가 찍힌다 — `len`, `print`, `str` 같은 저장소 밖 호출이 전부 섞이기 때문이다. 그것은 정직이 아니라 소음이다.

`db.py:548-556`의 `stats()`가 이미 옳은 구분을 하고 있다: 미해석 호출 중 **그 이름을 가진 함수가 저장소에 있는가**로 `calls_ambiguous`를 센다. 그대로 따른다.

| 상황 | 판정 | 화면 |
|---|---|---|
| `resolved_id IS NULL` 또는 가리키는 id가 없는데, **그 이름의 함수가 그래프에 있다** | **끊김(break)** — 엔진이 어느 것인지 고르기를 거부했다. 사슬은 여기서 진짜로 끊긴다 | 점선 스텁 + 툴팁 |
| 미해석인데 **그 이름의 함수가 그래프에 없다** | 저장소 밖 호출 — 끊김이 아니라 저장소의 경계 | 그리지 않고 **범례에 개수만** |
| 사슬 노드가 최대 깊이에 있다 | 끊김이 아니라 **깊이 제한** | 범례가 "depth N — 이 고리 너머는 따라가지 않았다"라고 말한다 |

`callers` 방향에서는 저장소 밖 호출이라는 것이 존재할 수 없다(미해석 유입 호출부는 정의상 우리가 아는 이름을 부른다). 그래서 그쪽의 `external`은 항상 0이고, 그 사실 자체가 정직한 결과다.

### 1.3 기존 인프라를 얼마나 재사용하는가 — `FileBox`는 건드리지 않는다

이 slice에서 가장 중요한 회귀 방지 결정이다. **`FileBox`(`FunctionGraphSurface.tsx:1358`)의 props도 본문도 바꾸지 않는다.** 105개 박스 / 1435개 행의 `memo`가 이 컴포넌트의 성능 전부이고, prop 하나를 늘리는 순간 그 계약을 다시 검증해야 한다.

대신 이렇게 붙인다:

- **행 하이라이트** = 기존 `marks` 경로 그대로. 사슬 노드는 방향에 따라 `'calls'`(파랑) 또는 `'callers'`(초록), 루트는 `'selected'`. `RowMark`에 새 값을 추가하지 않는다 → `markColour`·`MARK_RANK`·`FileBox` 모두 무변경.
- **디밍** = 기존 `focusMarks → focusBoxes` 경로 그대로. `focusMarks`의 출처만 "선택의 직접 엣지"에서 "사슬"로 바뀐다. 박스 디밍·`FileLinks` 디밍 코드는 한 줄도 바뀌지 않는다.
- **끊김 표시** = 캔버스 레이어에 그리는 **점선 스텁**(노드 옆으로 뻗다가 십자 틱에서 끝나는 짧은 선). 행 안이 아니라 행 밖에 그리므로 `FileBox`와 무관하다.

### 1.4 Trace는 선택의 한 겹 위다

`trace === null`이면 화면은 지금과 **바이트 단위로 같아야 한다**. 해제 = 상태를 null로 두는 것뿐이고, 복구 코드는 존재하지 않는다. 이것이 "해제 시 원상복귀"를 코드로 보장하는 유일하게 믿을 수 있는 방법이다.

---

## 2. 변경 파일 목록 (9개, 신규 0개)

> `FileBox` / `FunctionDetail.tsx` / `Minimap.tsx` / `App.tsx` / `SurfacePane.tsx` / Python 전부 **무변경**.

---

### 2.1 `desktop/src/domains/graph-view/types.ts` (수정)

트레이스의 계약 전체가 여기 산다. **이 파일이 처음으로 런타임 값을 export 한다** — 깊이 범위는 브리지 인자의 일부이고, main과 renderer가 같은 숫자를 알아야 하기 때문이다(main은 렌더러가 보낸 숫자를 믿지 않는다). 파일 머리 주석에 그 한 줄을 덧붙인다.

```ts
/** 어느 쪽으로 따라가는가. calls = 이 함수가 부르는 쪽, callers = 이 함수를 부르는 쪽. */
export type TraceDirection = 'calls' | 'callers'

/** UI가 조절할 수 있는 범위와 기본값. main도 이 상수로 클램프한다. */
export const TRACE_DEPTH_MIN = 1
export const TRACE_DEPTH_MAX = 5
export const TRACE_DEPTH_DEFAULT = 3

/**
 * 요청받은 깊이를, 실제로 답할 수 있는 깊이로.
 *
 * 렌더러가 보낸 숫자는 main에서도 다시 통과한다 — 경계를 넘어온 값은 모양이
 * 맞는다고 우리 것이 아니다(`register-handlers.ts` 머리말의 두 번째 규칙).
 *
 * @param depth  요청된 깊이. 숫자가 아니면 기본값으로 읽는다
 */
export function clampTraceDepth(depth: number): number

/** 사슬 위의 함수 하나. */
export interface TraceNode {
  id: number
  /** 루트에서 몇 걸음인가. 0은 루트 자신, 가장 짧은 경로 기준. */
  depth: number
}

/** 사슬이 실제로 따라간 호출 하나. 화살표 방향은 언제나 caller -> callee. */
export interface TraceEdge {
  from: number
  to: number
  /** 이 걸음이 도달한 쪽의 깊이. 1이 루트의 첫 고리. */
  depth: number
}

/**
 * 추적이 끊긴 자리. 그래프가 그 이름을 알지만 어느 것인지 고르기를 거부했다.
 */
export interface TraceBreak {
  /** 이 끊김이 매달린 사슬 노드. */
  atId: number
  depth: number
  /** 따라갈 수 없었던 이름. callers 방향에서는 사슬 노드 자신의 이름이다. */
  callee: string
  /** 그런 호출부가 몇 군데인가. */
  count: number
  /** 그 이름을 가진 함수가 저장소에 몇 개인가 — 왜 모호한지의 답. */
  candidates: number
}

export interface GraphTrace {
  rootId: number
  direction: TraceDirection
  /** 실제로 쓰인 깊이(클램프 뒤). */
  depth: number
  nodes: TraceNode[]
  edges: TraceEdge[]
  breaks: TraceBreak[]
  /** 그래프가 이름조차 모르는 호출부 — 끊김이 아니라 저장소의 경계. 개수만. */
  external: number
  /** MAX_TRACE_NODES에 걸려 잘린 노드 수. 범례가 이 숫자를 인쇄한다. */
  truncated: number
}
```

`GraphBridge`에 한 줄:

```ts
export interface GraphBridge {
  getGraphIndex(): Promise<GraphIndexResult>
  getGraphNode(id: number): Promise<GraphNodeDetail | null>
  /** 한 함수에서 뻗어나가는 호출 사슬. 저장소·DB·id 중 하나라도 없으면 null. */
  getGraphTrace(request: {
    id: number
    direction: TraceDirection
    depth: number
  }): Promise<GraphTrace | null>
}
```

> **`null`인 경우와 "빈 사슬"인 경우는 다르다.** 아무것도 해석되지 않은 함수의 트레이스는 `nodes.length === 1`(루트뿐), `edges: []`, `breaks`는 있을 수 있는 **정상 결과**다. `null`은 "저장소 없음 / DB 못 읽음 / 그런 id 없음"뿐이며, 이는 `getGraphNode`의 계약과 같다.

---

### 2.2 `desktop/src/domains/graph-view/main/graph-store.ts` (수정)

기존 함수·쿼리는 **한 줄도 건드리지 않는다.** 새 export 하나와, 그것이 쓰는 private helper들만 추가한다. 파일의 계약(“Nothing here throws”, `openReadonly` 재사용, `finally { closeQuietly }`)을 그대로 지킨다.

```ts
/** 한 번의 트레이스가 그릴 수 있는 노드 수의 상한. 넘으면 얕은 쪽부터 남기고 센다. */
const MAX_TRACE_NODES = 400

/**
 * 한 함수에서 깊이 N까지의 호출 사슬 — 그리고 그것이 끊긴 자리들.
 *
 * 사슬은 SQLite의 `WITH RECURSIVE`가 계산한다. 렌더러가 가진 `index.edges`로도
 * BFS는 돌지만, 그 목록은 `JOIN functions dst`로 미해석 호출을 이미 버린 뒤라서
 * "여기서 끊겼다"를 말할 재료가 없다. 끊김을 정직하게 말하려면 사슬과 끊김이
 * 같은 조회에서 나와야 한다.
 *
 * @param repoRoot   앱이 열어둔 저장소
 * @param id         사슬의 뿌리
 * @param direction  calls(부르는 쪽) 또는 callers(불리는 쪽)
 * @param depth      1..5. 범위 밖은 클램프된다 — 경계를 넘어온 값이므로
 * @flow  db 없음/열기 실패/그런 id 없음 -> null ; 재귀 CTE로 (id, 최단깊이) ->
 *        상한을 넘으면 얕은 쪽부터 남기고 나머지는 센다 -> 확장된 노드들 사이의
 *        엣지 -> 확장된 노드의 따라갈 수 없는 호출부를, 그래프가 그 이름을 아는
 *        것(끊김)과 모르는 것(저장소 밖)으로 나눈다
 * 주요 내부 변수: reached(id -> 최단 깊이), expanded(최대 깊이가 아니라 실제로
 * 펼쳐진 노드들), names(끊긴 이름 -> 후보 수)
 */
export function readGraphTrace(
  repoRoot: string,
  id: number,
  direction: TraceDirection,
  depth: number
): GraphTrace | null
```

#### 재귀 조회

`calls` 방향:

```sql
WITH RECURSIVE step(id, depth) AS (
  SELECT ?, 0
  UNION
  SELECT c.resolved_id, s.depth + 1
    FROM step s
    JOIN calls c ON c.caller_id = s.id
    JOIN functions dst ON dst.id = c.resolved_id
   WHERE s.depth < ?
)
SELECT id, MIN(depth) AS depth FROM step GROUP BY id
```

`callers` 방향은 두 줄만 다르다: `JOIN calls c ON c.resolved_id = s.id`, `SELECT c.caller_id, ...`, `JOIN functions src ON src.id = c.caller_id`.

- **`UNION`이며 `UNION ALL`이 아니다.** 호출 그래프에는 순환이 있고(`main → run_pipeline → … → main`), `UNION ALL`은 경로를 열거하므로 깊이 5에서 조합적으로 터진다. `UNION`은 `(id, depth)` 쌍을 중복 제거하므로 작업량이 노드×깊이로 묶인다 — 최악 1485×6.
- **`JOIN functions dst`** 가 `readGraphIndex:160-162`와 같은 필터다: 미해석 호출과 사라진 id를 가리키는 호출을 여기서 떨어뜨린다. 그 떨어진 것들이 아래 끊김 조회의 대상이다.
- `MIN(depth)` — 같은 노드가 여러 깊이로 도달되면 **가장 얕은 것**이 그 노드의 깊이다.
- 파라미터는 텍스트 등장 순서대로 위치 바인딩된다(`.all(id, depth)`). 기존 `.all(id)` 관용구와 같다.

#### 상한

SQL에 `LIMIT`을 걸지 않는다(재귀 CTE의 `LIMIT`은 큐 순서에 의존해서 무엇이 잘렸는지 말할 수 없다). 전부 읽은 뒤 `depth` 오름차순, 동률은 `id` 순으로 정렬해 `MAX_TRACE_NODES`까지 남기고, 나머지 개수를 `truncated`에 넣는다. **얕은 고리가 먼저 살아남는다** — 잘렸을 때 남는 그림이 뜻을 유지한다.

#### 엣지

노드 집합을 `id IN (?,?,…)`로(`db.py:by_path`의 placeholder 관용구) 두 번 써서:

```sql
SELECT DISTINCT c.caller_id AS from_id, c.resolved_id AS to_id
  FROM calls c
  JOIN functions dst ON dst.id = c.resolved_id
 WHERE c.caller_id IN (<ids>) AND c.resolved_id IN (<ids>)
   AND c.caller_id <> c.resolved_id
```

그리고 **JS에서 한 번 더 거른다: `depthOf(from) < depth`인 엣지만 남긴다.** 최대 깊이의 노드는 애초에 펼쳐지지 않았으므로, 그 노드에서 나가는 선을 그리면 화면이 "사슬은 여기서 더 간다"고 거짓말한다. `callers` 방향에서는 조건이 `depthOf(to) < depth`다(그쪽에서 펼쳐진 것은 피호출자다). `edge.depth`는 **바깥쪽 끝의 깊이** = `max(depthOf(from), depthOf(to))`.

#### 끊김

`expanded` = 깊이가 `depth`보다 작은 노드들(펼쳐진 것들). `calls` 방향:

```sql
SELECT c.caller_id AS at_id, c.callee_name AS callee, COUNT(*) AS n
  FROM calls c
  LEFT JOIN functions dst ON dst.id = c.resolved_id
 WHERE c.caller_id IN (<expanded>) AND dst.id IS NULL
 GROUP BY c.caller_id, c.callee_name
```

`LEFT JOIN … WHERE dst.id IS NULL`이 두 경우를 한 조건으로 잡는다: `resolved_id`가 NULL인 것과, 값은 있는데 그 id의 함수가 없는 것(반쯤 쓰인 갱신 — 픽스처의 `resolved_id = 999`가 정확히 그 경우다).

`callers` 방향:

```sql
SELECT f.id AS at_id, f.name AS callee, COUNT(*) AS n
  FROM calls c
  JOIN functions f ON f.name = c.callee_name
  LEFT JOIN functions dst ON dst.id = c.resolved_id
 WHERE f.id IN (<expanded>) AND dst.id IS NULL
 GROUP BY f.id
```

즉 "이 함수의 이름을 부르지만 엔진이 대상을 고르지 못한 호출부가 N군데" — `readGraphNode:282-284`가 이미 인정하는 그 행들이다.

후보 수는 한 번 더:

```sql
SELECT name, COUNT(*) AS n FROM functions WHERE name IN (<distinct callees>) GROUP BY name
```

`candidates >= 1` → `breaks`에 넣는다. `candidates === 0` → `external += n` (그리지 않는다).

전체를 `try { … } catch { return null } finally { closeQuietly(opened.db) }`로 감싼다.

---

### 2.3 `desktop/src/domains/graph-view/main/graph-store.test.ts` (수정)

`repoWithGraph()` 픽스처는 **그대로 쓴다** — 이미 트레이스를 검증하기에 충분하다. 그 안의 호출들:

```
1 run_pipeline → 2 say            (해석됨)
3 main         → 1 run_pipeline   (해석됨)
4 test_runs    → run_pipeline     (미해석, 모호)
2 say          → run_pipeline     (resolved_id = 999, 없는 id)
2 say          → 3 main           (해석됨, 두 행)
```

`describe('readGraphTrace', { skip: !available })` 에 넣을 케이스:

| 테스트 | 기대 |
|---|---|
| 깊이 1, calls, 루트 1 | 노드 `{1:0, 2:1}`, 엣지 `1→2` 하나 |
| 깊이 3, calls, 루트 1 | 노드 `{1:0, 2:1, 3:2}` — `say → main → run_pipeline`이 루트로 되돌아와도 `MIN(depth)`가 루트를 0으로 유지한다 |
| 순환이 무한 재귀가 되지 않는다 | 위 케이스가 끝나고, 엣지에 `3→1`이 포함된다(3은 깊이 2 < 3이라 펼쳐졌다) |
| 중복 호출은 엣지 하나 | `2→3`이 `calls`에 두 행인데 엣지는 하나 |
| calls 방향의 끊김 | 루트 1, 깊이 3 → `breaks`에 `{atId: 2, callee: 'run_pipeline', count: 1, candidates: 1}` (없는 id 999를 가리키던 그 행) |
| callers 방향 | 루트 1, 깊이 2 → 노드 `{1:0, 3:1, 2:2}`, 엣지 `3→1`, `2→3` |
| callers 방향의 끊김 | 루트 1 → `breaks`에 `{atId: 1, count: 1}` — `test_runs`의 모호한 호출부 |
| 최대 깊이 노드는 펼치지 않는다 | 깊이 1, calls, 루트 1 → `breaks`가 비어 있다(노드 2는 깊이 1 = 최대라 펼쳐지지 않았다) |
| 깊이 클램프 | `readGraphTrace(root, 1, 'calls', 0).depth === 1`, `…, 99).depth === 5` |
| 없는 것들 | `readGraphTrace(root, 9999, …) === null`, `(root, -1, …) === null`, `(root, 1.5, …) === null`, `(tempRoot(), 1, …) === null` |
| 잘리지 않았다 | `truncated === 0` |

`clampTraceDepth`는 `types.ts`에서 직접 import 해 별도 `describe`로 검사한다: `0→1`, `1→1`, `3→3`, `5→5`, `9→5`, `NaN→3`, `-1→1`, `2.7→` 정수(반올림 규칙을 테스트가 못 박는다).

---

### 2.4 `desktop/src/shared/ide.ts` (수정)

`IPC`에 한 줄:

```ts
  graphIndex: 'aidev:get-graph-index',
  graphNode: 'aidev:get-graph-node',
  /** v0.2.8 — 한 함수에서 뻗어나가는 호출 사슬. 읽기 전용. */
  graphTrace: 'aidev:get-graph-trace',
```

`AidevBridge`는 `GraphBridge`를 extends 하므로 **본문 변경 없음**. 276-283행의 설명 주석에 트레이스가 여전히 읽기라는 한 문장만 덧붙인다.

---

### 2.5 `desktop/src/preload/index.ts` (수정)

`getGraphNode` 아래 한 줄:

```ts
  getGraphTrace: (request) => ipcRenderer.invoke(IPC.graphTrace, request),
```

새 능력이 아니라 기존 "Function DB, 읽기 전용" 묶음의 세 번째 질문이다 — 55행의 주석 아래에 그대로 들어간다.

---

### 2.6 `desktop/src/app/main/register-handlers.ts` (수정)

`IPC.graphNode` 핸들러 뒤. 파일 머리말의 두 규칙을 그대로 지킨다: **아무것도 throw 하지 않고, 렌더러의 값은 두 번 검사한다.**

```ts
ipcMain.handle(IPC.graphTrace, (_event, request: unknown): GraphTrace | null => {
  const root = ctx.repoRoot()
  if (!root) return null
  const ask = (request ?? {}) as { id?: unknown; direction?: unknown; depth?: unknown }
  // 방향은 두 값 중 하나뿐이다. 모르는 문자열을 그대로 SQL 분기에 넘기지 않는다.
  const direction: TraceDirection = ask.direction === 'callers' ? 'callers' : 'calls'
  try {
    return readGraphTrace(root, Number(ask.id), direction, clampTraceDepth(Number(ask.depth)))
  } catch {
    return null
  }
})
```

import 추가: `readGraphTrace`, `clampTraceDepth` / `type { GraphTrace, TraceDirection }`.

---

### 2.7 `desktop/src/domains/graph-view/layout.ts` (수정)

여기에 그리기 결정 전부를 둔다. `ui/`가 아니라 이 파일인 이유: `tsconfig.test.json:19-28`이 `src/domains/**/*.ts`를 포함하고 `**/ui/**`를 제외하므로, **이 파일에 있는 것만 `node --test`가 검증할 수 있다.** 검증 여력이 얇은 slice일수록 로직은 여기로 내려와야 한다.

`edgeStyle` 섹션(981-1039행) 끝에:

```ts
/** 추적이 끊긴 자리. 색만이 유일한 차이가 되지 않도록 파선을 함께 쓴다 —
 *  EDGE_STYLES가 세운 규칙 그대로. */
export const TRACE_BREAK_STYLE: Readonly<EdgeStyle> = {
  stroke: 'var(--color-warn)',
  dash: '2 2',
  marker: ''
}
/** 끊김 스텁이 노드 옆으로 뻗는 길이, user unit. */
export const TRACE_STUB_LEN = 26
/** 한 트레이스가 그리는 곡선의 상한. 넘으면 얕은 고리부터 남기고 범례가 센다. */
export const MAX_TRACE_EDGES = 500
```

새 순수 함수 네 개:

```ts
/**
 * 사슬이 칠하는 행들 — 방향이 곧 색이다.
 *
 * 새 RowMark를 만들지 않는다: calls 방향의 사슬은 전부 "이 함수가 부르는 것"이고
 * callers 방향은 전부 "이 함수를 부르는 것"이므로, 범례가 이미 약속한 두 색이
 * 그대로 맞는 말이다. 그래서 `FileBox`도 `markColour`도 손대지 않아도 된다.
 *
 * @param trace  DB가 답한 사슬
 * @flow  루트는 'selected' ; 나머지 노드는 방향에 따라 'calls' 또는 'callers'
 */
export function traceMarks(trace: GraphTrace): Map<number, RowMark>

/**
 * 깊이가 멀수록 옅게. 사슬의 방향을 색이 아니라 농도로 한 번 더 말한다.
 *
 * @param depth  이 걸음이 닿은 깊이
 * @param max    이 트레이스의 깊이
 * @flow  1이 가장 진하고, 최대 깊이가 TRACE_FAR(0.45)까지 내려간다. 깊이 1짜리
 *        트레이스는 나눗셈이 없으므로 언제나 1
 */
export function traceOpacity(depth: number, max: number): number

/**
 * 사슬을 곡선으로, 끊김을 스텁으로 — 지금 상자들이 있는 자리에서.
 *
 * @param trace   DB가 답한 사슬
 * @param layout  이 레벨의 배치
 * @param byId    인덱스의 모든 함수, id별
 * @param byBox   끌어다 놓은 상자들의 이동량, 상자 index별
 * @param lod     이 줌이 그리는 레벨
 * @param limit   곡선 상한
 * @flow  파일 레벨은 함수 곡선을 그리지 않는다(원경이 읽히는 이유가 그것이다) ->
 *        깊이 얕은 것부터 정렬해 limit까지 -> 양 끝의 앵커를 지금 자리에서 읽어
 *        곡선 -> 끊김은 노드당 하나로 접어 스텁 하나 + 십자 틱
 * 주요 내부 변수: byNode(노드별로 접은 끊김), placed(앵커를 찾은 것만)
 */
export function traceLines(
  trace: GraphTrace,
  layout: GraphLayout,
  byId: Map<number, GraphFunction>,
  byBox: Map<number, Offset>,
  lod: Lod,
  limit = MAX_TRACE_EDGES
): TraceDrawing
```

```ts
export interface TraceLine {
  key: string
  d: string
  fromId: number
  toId: number
  depth: number
}

/** 한 노드가 따라가지 못한 호출부들을, 하나의 짧은 점선으로. */
export interface TraceStub {
  key: string
  /** 점선 본체. */
  d: string
  /** 그 끝의 십자 틱 — "여기서 끝"을 선의 끝 모양으로 말한다. */
  tick: string
  atId: number
  /** 이 노드가 따라가지 못한 호출부의 총 개수. */
  count: number
  /** 툴팁 한 줄: "3 call sites could not be followed: foo, bar, …" */
  label: string
}

export interface TraceDrawing {
  lines: TraceLine[]
  stubs: TraceStub[]
  /** 그린 곡선 수와 사슬이 가진 곡선 수. 범례가 둘 다 인쇄한다. */
  shown: number
  total: number
}
```

스텁 기하 (`resolveAnchor`가 준 `EdgeAnchor` 하나로 계산, `edgePath`를 쓰지 않는다):

- `calls` 방향: `d = M {right} {y} L {right + LEN} {y}`, `tick = M {right+LEN} {y-4} L {right+LEN} {y+4}`
- `callers` 방향: `d = M {left} {y} L {left - LEN} {y}`, `tick`도 `left - LEN`에서

**노드당 하나로 접는다.** 미해석 이름이 12개인 함수에 스텁 12개를 겹쳐 그리면 아무 뜻도 없다. `label`은 개수 + 이름 최대 3개 + `…`.

`traceLines`는 `trace.direction === 'callers'`를 그대로 `edgeStyle(incoming)`의 `incoming`으로 쓸 수 있게 두고(스타일 선택은 호출부에서), 곡선 자체는 언제나 `edgePath(fromAnchor, toAnchor)` — **화살표는 언제나 caller → callee**를 가리킨다. 역방향 추적이라고 화살표를 뒤집으면 그림이 호출 방향에 대해 거짓말을 한다.

---

### 2.8 `desktop/src/domains/graph-view/layout.test.ts` (수정)

`node --test`가 실제로 잡을 수 있는 것들:

- `traceMarks`: 루트는 `'selected'`; calls 방향 노드는 `'calls'`, callers 방향 노드는 `'callers'`; 빈 노드 목록은 빈 맵.
- `traceOpacity`: `(1, 3) === 1`, 단조 감소, `(3, 3) >= 0.45`, `(1, 1) === 1`(0으로 나누지 않는다).
- `traceLines`:
  - `lod === 'file'`이면 `lines`와 `stubs`가 비고 `total`은 여전히 사슬의 곡선 수다(범례가 "원경이라 그리지 않았다"를 말할 수 있어야 한다).
  - 상한을 넘으면 **얕은 깊이가 남는다** — `limit: 1`로 깊이 1짜리만 남는지.
  - 배치에 없는 id는 그 파일 상자 옆에 붙는다(`resolveAnchor` 폴백) / 인덱스에 아예 없는 id는 조용히 빠진다.
  - 끌어다 놓은 상자(`offsetsByBox`)를 주면 곡선과 스텁이 **같이** 움직인다 — `fileLines with dragged boxes` describe와 같은 관용구.
  - 한 노드의 끊김 두 건이 스텁 하나로 접히고 `count === 2`.
  - `callers` 방향의 스텁은 상자 **왼쪽**으로 뻗는다.
- 토큰 검사(1199-1206행) 확장: `assert.ok(defined(TRACE_BREAK_STYLE.stroke))`.
- 회귀 자물쇠 하나: `TRACE_BREAK_STYLE.dash`가 비어 있지 않다 — 색맹 독자에게 색만이 차이가 되면 안 된다는 규칙이 `EDGE_STYLES`에서 이미 테스트되고 있으므로, 새 스타일도 같은 자물쇠를 받는다.

---

### 2.9 `desktop/src/domains/graph-view/ui/FunctionGraphSurface.tsx` (수정)

#### (a) 상태

```ts
const [traceOn, setTraceOn] = useState(false)
const [traceDir, setTraceDir] = useState<TraceDirection>('calls')
const [traceDepth, setTraceDepth] = useState(TRACE_DEPTH_DEFAULT)
const [trace, setTrace] = useState<GraphTrace | null>(null)
const [traceLoading, setTraceLoading] = useState(false)
/** 이 요청보다 늦게 도착한 응답은 남의 것이다. */
const traceToken = useRef(0)
```

#### (b) 가져오기

```ts
useEffect(() => {
  if (!traceOn || selected === null) {
    setTrace(null)
    setTraceLoading(false)
    return
  }
  const token = ++traceToken.current
  setTraceLoading(true)
  // 이전 사슬은 그대로 둔다 — 깊이를 한 칸 올릴 때 화면이 깜빡이지 않도록.
  window.aidev
    .getGraphTrace({ id: selected, direction: traceDir, depth: traceDepth })
    .then((next) => {
      if (token !== traceToken.current) return
      setTrace(next)
      setTraceLoading(false)
    })
}, [traceOn, selected, traceDir, traceDepth, index])
```

`index`가 의존성인 이유: 재빌드하면 **`functions.id`가 전부 새로 발급된다**(`db.py:replace_file` → `drop_file` + INSERT). 옛 사슬의 id는 그때부터 남의 것이므로 반드시 다시 묻는다. `[files]`에 걸린 기존 `setOffsets` 초기화(329-331행)와 같은 이유의 같은 방어다.

#### (c) 엣지와 마크의 출처만 바꾼다

```ts
const traced = useMemo(
  () => (trace ? traceLines(trace, layout, byId, byBox, lod) : null),
  [trace, layout, byId, byBox, lod]
)

// 선택의 직접 엣지는 트레이스 중에 그리지 않는다: 깊이 1 고리가 이미 그것이고,
// 반대 방향의 선을 겹쳐 그리면 "추적"이 무엇을 뜻하는지 화면이 흐려진다.
const edges = useMemo(
  () => (trace ? [] : edgesFor(detail, layout, byId, byBox, lod)),
  [trace, detail, layout, byId, byBox, lod]
)

const focusMarks = useMemo(() => {
  if (trace) return traceMarks(trace)
  if (selected === null) return NO_MARKS
  …기존 그대로…
}, [trace, selected, edges])
```

**`focusBoxes`, `marks`, 박스 `opacity`, `FileLinks dim`은 한 줄도 바뀌지 않는다.** `focusMarks`가 사슬을 담게 되는 순간 디밍은 저절로 사슬을 따른다 — 요구사항의 "기존 디밍 인프라 재사용"이 문자 그대로 성립한다.

주의: `traceMarks`는 **lod와 무관**하다. 원경(`lod === 'file'`)에서 곡선은 사라지지만 사슬이 지나는 **파일 상자들은 계속 밝다** — 541-545행이 이미 인정하는 폴백과 같은 성질이고, 줌아웃으로 "이 사슬이 어느 파일들을 통과하나"를 보는 것이 이 모드의 가장 쓸모 있는 사용법이다.

#### (d) 그리기

기존 함수 엣지 `<g>`(1032-1048행) **뒤에**, 박스들 **앞에** 새 레이어 하나:

```tsx
{traced ? (
  <g pointerEvents="none">
    {traced.lines.map((line) => {
      const style = edgeStyle(trace!.direction === 'callers')
      return (
        <path key={line.key} d={line.d} fill="none"
              stroke={style.stroke} strokeWidth={1.4}
              strokeDasharray={style.dash ?? undefined}
              strokeOpacity={traceOpacity(line.depth, trace!.depth)}
              markerEnd={`url(#${style.marker})`} />
      )
    })}
    {traced.stubs.map((stub) => (
      <g key={stub.key}>
        <path d={stub.d} fill="none" stroke={TRACE_BREAK_STYLE.stroke}
              strokeWidth={1.4} strokeDasharray={TRACE_BREAK_STYLE.dash ?? undefined} />
        <path d={stub.tick} fill="none" stroke={TRACE_BREAK_STYLE.stroke} strokeWidth={1.4} />
        <title>{stub.label}</title>
      </g>
    ))}
  </g>
) : null}
```

`pointerEvents="none"` — `FileLinks`(1316행)와 같은 이유다. 스텁이 상자 옆으로 26 unit 뻗으므로 그것이 팬의 히트 테스트를 가로채면 안 된다. 툴팁(`<title>`)은 pointer-events 없이도 동작하지 않으므로, **툴팁은 포기하지 않고** 스텁 그룹만 `pointerEvents="auto"`로 되돌린다 — 26×8 unit 두 개는 팬을 방해하지 않는다. (`startPan`은 `closest('[data-box]')`만 보므로 스텁 위에서 시작한 드래그는 정상적으로 팬이 된다.)

기존 `defs`의 마커 세 개는 재사용한다. **새 마커를 만들지 않는다** — 끊김 스텁에는 화살표가 없다(가리킬 곳이 없다는 것이 요점이다).

#### (e) 조작부 — `FreshnessBar` 안의 새 `TraceControls`

`FreshnessBar`에 prop 하나(`trace: TraceUi`)를 더해 오른쪽 묶음(1228행)의 검색창 **앞**에 렌더한다.

```ts
interface TraceUi {
  on: boolean
  direction: TraceDirection
  depth: number
  /** 노드가 선택되어 있는가 — 없으면 토글은 비활성이다. */
  ready: boolean
  onToggle: () => void
  onDirection: (d: TraceDirection) => void
  onDepth: (d: number) => void
}
```

```tsx
function TraceControls({ trace }: { trace: TraceUi }): JSX.Element {
  return (
    <div className="flex items-center gap-1 font-mono text-micro">
      <button type="button" disabled={!trace.ready} onClick={trace.onToggle}
        aria-pressed={trace.on}
        title={trace.ready ? '선택한 함수에서 호출 사슬을 따라간다' : '먼저 함수를 하나 고른다'}
        className={…on ? 'border-accent text-accent' : 'border-line text-fg-dim'…}>
        Trace
      </button>
      {trace.on ? (
        <>
          {/* 역방향 토글: 두 칸짜리 세그먼트 — 어느 쪽인지 언제나 보인다 */}
          <div className="flex overflow-hidden rounded-sm border border-line" role="group">
            <SegButton on={trace.direction === 'calls'}    onClick={…'calls'}   label="calls"   title="이 함수가 부르는 쪽" />
            <SegButton on={trace.direction === 'callers'} onClick={…'callers'} label="callers" title="누가 여기까지 오나" />
          </div>
          {/* 깊이 1..5 */}
          <div className="flex items-center gap-0.5 text-fg-mute">
            <ZoomButton-형 '−' disabled={depth <= TRACE_DEPTH_MIN} onClick={onDepth(depth - 1)} />
            <span className="w-8 text-center text-fg-dim" title="추적 깊이 1..5">d{trace.depth}</span>
            <ZoomButton-형 '+' disabled={depth >= TRACE_DEPTH_MAX} onClick={onDepth(depth + 1)} />
          </div>
        </>
      ) : null}
    </div>
  )
}
```

**꺼져 있을 때는 버튼 하나만 보인다** — 기본 상태의 툴바가 지금보다 붐비지 않는다. 깊이는 `clampTraceDepth`를 통과시켜 넘긴다(버튼이 이미 막지만, 클램프가 한 곳에만 있어야 두 규칙이 어긋나지 않는다).

우클릭 메뉴는 만들지 않는다 — 요구사항이 "버튼 **또는** 우클릭 메뉴"라고 했고, 컨텍스트 메뉴 인프라가 이 앱에 아직 없다. 없는 인프라를 이 slice에서 만드는 것은 범위 밖이다.

#### (f) 범례 — 숫자는 여기서 말한다

`Legend`에 `trace: TraceLegend | null` prop을 추가하고, 있을 때만 세그먼트를 덧붙인다:

```
[점선 warn 견본] trace stops here (9)  ·  trace: callers · depth 3 · 128 fn · 214 calls · 41 external
```

- 곡선이 잘렸으면 `top 500 of 812 calls` (기존 file link 문구와 같은 어법).
- `truncated > 0`이면 `+37 fn not drawn`.
- `lod === 'file'`이면 `files only — 사슬은 상자 밝기로만 보인다`.
- **깊이 제한을 반드시 인쇄한다**: `depth 3 — nothing past this ring was followed`. 최대 깊이 노드에 스텁이 없는 것이 "거기서 끝난다"는 뜻이 아님을, 화면이 스스로 말해야 한다.
- `external`은 개수만 — "그래프가 이름조차 모르는 호출부", 즉 저장소의 경계.

`Legend`는 `pointer-events-none absolute bottom-2 left-2`인 한 줄짜리 인라인 박스다. 세그먼트가 늘어 넘칠 수 있으므로 컨테이너에 `flex-wrap`과 `max-w-[calc(100%-1rem)]`을 준다.

#### (g) Escape — 한 겹씩 벗긴다

419-428행의 핸들러를 층지게 만든다:

```ts
if (event.key !== 'Escape') return
if (target?.tagName === 'INPUT') return
if (traceOnRef.current) { setTraceOn(false); return }   // 먼저 추적을 푼다
onSelect(null)
```

효과 재구독을 막기 위해 `traceOn`은 ref로 읽는다(`selectedRef` 관용구 그대로). **의도된 동작 변경이다**: Esc 한 번이 "선택 하이라이트 상태로 복귀"(요구사항의 해제), 두 번이 전체 뷰. 검색창의 Esc는 지금처럼 입력 지우기가 먼저다.

#### (h) 선택이 바뀌면 사슬도 따라간다

`traceOn`은 유지하고 새 루트로 다시 조회한다 — 그것이 "누가 여기까지 오나"를 따라 올라가는 탐색의 자연스러운 모양이다. 선택이 **비면** 사슬은 null이 되고(위 (b)의 첫 분기) 화면은 전체 뷰로 돌아가지만 토글은 켜진 채 남는다: 다음 노드를 고르면 곧바로 그 노드의 사슬이 나온다.

---

## 3. 데이터 흐름

```
[Trace] 토글 / 방향 / 깊이 / 선택 / index
        │
        ▼
  window.aidev.getGraphTrace({id, direction, depth})
        │  preload → IPC.graphTrace
        ▼
  register-handlers: repoRoot() 있나 → direction을 두 값으로 강제 → clampTraceDepth
        ▼
  readGraphTrace(root, id, direction, depth)
        ├─ WITH RECURSIVE step(id, depth)  ── 사슬 (id -> 최단 깊이)
        ├─ 상한 400, 얕은 쪽부터            ── truncated
        ├─ 노드 집합 안의 해석된 엣지        ── edges   (펼쳐진 노드에서 나가는 것만)
        └─ LEFT JOIN … dst IS NULL         ── breaks (그래프가 아는 이름) / external (모르는 이름)
        ▼
  setTrace(GraphTrace)
        ├─▶ traceMarks(trace) ──▶ focusMarks ──▶ focusBoxes ──▶ 박스 디밍  [기존 인프라]
        │                                    └─▶ groupMarks ──▶ 행 색      [기존 인프라]
        └─▶ traceLines(trace, layout, byId, byBox, lod)
                 ├─▶ lines  ──▶ 방향색 곡선, 깊이만큼 옅게
                 └─▶ stubs  ──▶ warn 점선 + 십자 틱 + 툴팁
        ▼
  Legend: 깊이 · 노드 수 · 곡선 수(잘렸으면 top N of M) · 끊김 수 · external 수

[Trace] 해제 / Esc
  → trace = null → edges = edgesFor(detail, …) → focusMarks = 기존 계산
  → 화면은 선택 하이라이트 상태 그대로 (복구 코드는 없다)
```

---

## 4. 하지 않는 것

- **의미 흐름(`@flow` 기반) 추적.** 요구사항이 배제했다. `tags` 테이블은 읽지 않는다.
- **런타임 추적.** 정적 호출 그래프뿐이다.
- **애니메이션.** 새 `transition`은 넣지 않는다. 기존 `opacity 90ms linear` 두 곳(박스, FileLinks)이 전부다.
- **`aidev graph trace` CLI / `GraphDB.trace()`.** 요구사항에 없다. Python은 한 줄도 바뀌지 않는다. (덧붙이자면, 이 slice의 권한 규칙으로는 `pytest`를 돌릴 수도 없어서 파이썬 쪽 변경은 검증 없이 들어가게 된다 — 하지 않는 편이 정직하다.)
- **우클릭 컨텍스트 메뉴.** 앱에 그 인프라가 없다.
- **트레이스 결과 캐시.** 깊이를 한 칸 올릴 때마다 다시 묻는다. 재귀 CTE는 노드×깊이로 묶여 있어 캐시가 벌어줄 것이 없다.
- **사슬을 따라 스크롤/줌 이동.** 선택의 기존 스크롤 동작(403-415행)만 유지한다.
- **`FileBox` / `RowMark` / `markColour` / `FunctionDetail` / `Minimap` 변경.**
- **`package.json` / `package-lock.json` 변경.** 새 의존성이 없다.

---

## 5. 검증

### 5.1 이 워크트리에서 실제로 돌릴 수 있는 것

```
npx --prefix desktop tsc --noEmit -p desktop          # 선언된 test_command
```
실행하라. **그러나 이것이 통과했다고 "tsc 통과"라고 쓰지 말 것.** `desktop/tsconfig.json`은 `"files": []`인 솔루션 파일이고 `--build`가 없으므로 0개 파일을 검사한다. `node_modules`가 없어 npx가 typescript를 내려받아야 한다는 사실까지 그대로 보고하라.

```
npx --prefix desktop tsc --noEmit -p desktop/tsconfig.test.json
```
`Bash(npx --prefix:*)` 규칙에 걸리므로 **권한상으로는 허용된다.** 시도하라. `@types/node`가 없어 실패할 가능성이 높다 — 실패하면 실패한 출력을 그대로 남겨라. 이것이 이 slice에서 실제 타입 검사에 가장 가까운 시도다.

```
python -m aidev.specs
```
`SPEC_CHECK_TOOLS`로 허용된다. Python 파일이 바뀌지 않았으므로 이전과 같은 결과여야 한다. 실행하고 출력을 보고하라.

### 5.2 돌릴 수 없는 것 (그리고 왜)

```
npm install --prefix desktop && npm test --prefix desktop
python -m pytest
```
- `npm`에 대응하는 Bash 규칙이 이 slice에 **없다**(`test_commands`가 선언된 순간 `TEST_COMMAND_TOOLS`는 붙지 않는다 — `pipeline.py:2372-2380`). 
- 같은 이유로 **`pytest`도 허용되지 않는다.** Done Criteria의 "pytest 통과"는 이 단계에서 **실행으로 확인할 수 없다.** 확인할 수 있는 것은 "이 slice가 Python 파일을 하나도 바꾸지 않았다"뿐이며, 그것을 `git status`로 보이고 그대로 쓰라. **통과했다고 주장하지 말 것.**

### 5.3 작성하되 실행할 수 없는 단위 테스트

§2.3(`graph-store.test.ts`)과 §2.8(`layout.test.ts`)은 전부 작성한다. 이 워크트리에서는 `tsc`가 `out-test/`를 만들 수 없어 실행할 수 없다. **작성했고 실행하지 않았다고 명시하라.**

### 5.4 사람이 해야 하는 확인 (구현 보고서에 이 목록을 그대로 남길 것)

```
npm install --prefix desktop
npm test --prefix desktop            # 여기서 §5.3의 테스트가 처음으로 실제로 돈다
npm run dev --prefix desktop
```

1. Function graph 탭 → 검색 `run_pipeline` → Enter → 행 선택 → **[Trace]**
   → 깊이 3 사슬이 파랑으로, 무관한 상자·파일링크가 흐려짐. `aidev/pipeline.py:4277`이 루트.
2. **끊김 지점**: 사슬 노드 옆으로 warn 색 점선 스텁 + 십자 틱, 호버 시 "N call sites could not be followed: …". 범례가 끊김 수와 external 수를 따로 인쇄하는지.
3. **깊이 조절**: `d3` → `d1`(사슬 축소) → `d5`(확대). 1과 5에서 각각 −/+ 버튼이 비활성.
4. **역방향**: `callers` → 사슬이 초록 파선으로 바뀌고 스텁이 상자 **왼쪽**으로 뻗음. 화살표는 여전히 caller → callee를 가리키는지 (뒤집히면 버그).
5. **해제 복귀**: [Trace] 다시 누름 → 선택 하이라이트 + 직접 엣지 상태로 정확히 복귀. Esc 한 번도 같은 효과, 두 번째 Esc가 선택 해제.
6. **재회귀 없음**: 팬(빈 캔버스 드래그·스페이스 홀드), 박스 드래그(사슬 곡선과 스텁이 상자를 따라오는지), 행 클릭 선택/재클릭 해제, 더블클릭 코드 뷰어, 휠 줌, 미니맵, Reset positions.
7. **줌 레벨**: `d5` 트레이스를 켠 채 40%까지 줌아웃 → 곡선은 사라지고 사슬이 지나는 **파일 상자만 밝게** 남는지, 범례가 그렇게 말하는지.
8. **재빌드 중 트레이스**: 트레이스 켠 채 [Rebuild] → 사슬이 새 id로 다시 조회되는지(옛 id로 엉뚱한 노드가 밝아지면 버그).
9. **성능**: `say`(150 callers)를 루트로 `callers` 깊이 5 → 응답 지연을 체감으로 기록. 눈에 띄게 느리면 §6-2.

---

## 6. 무엇이 잘못될 수 있는가

1. **검증 공백 (가장 큰 위험).** `node_modules`가 없고 `setup:`도 없다. tsc·`node --test`·앱 실행 중 어느 것도 이 워크트리에서 불가능하고, 선언된 test_command는 0개 파일을 검사한다. 완화는 없다 — 있는 것은 (a) 로직을 `ui/` 밖(`layout.ts`, `main/graph-store.ts`, `types.ts`)에 두어 나중에 실제로 검증되게 하는 것, (b) §5의 정직한 보고뿐이다. 특히 **`pytest`가 권한 목록에 없다**는 사실은 Done Criteria와 정면으로 부딪히므로 보고서 첫머리에 적어야 한다.

2. **`callers` 방향 재귀의 비용.** `calls.resolved_id`에는 인덱스가 없다(`db.py:91-96`). 역방향 재귀는 매 단계 `calls`(8669행)를 훑을 수 있다. SQLite가 자동 임시 인덱스를 만들어줄 가능성이 높지만 보장은 아니다. 완화: 노드 상한 400, 깊이 상한 5, 그리고 §5.4-9의 실측. **읽기 전용 핸들이므로 인덱스를 추가할 수는 없다** — 느리다면 해법은 `db.py`의 `SCHEMA`에 `idx_calls_resolved`를 넣고 `GRAPH_SCHEMA`를 올리는 것이고, 그것은 **다음 slice**다(스키마를 올리면 `graph-store.ts:36`의 `GRAPH_SCHEMA = '1'`과 함께 전면 재빌드가 필요하다).

3. **순환.** 호출 그래프에는 순환이 있다(픽스처의 `1→2→3→1`이 그 축소판이다). `UNION ALL`을 쓰면 경로 열거로 폭발한다. **`UNION`이어야 한다** — 리뷰에서 가장 먼저 볼 한 단어다.

4. **끊김이 소음이 된다.** 저장소 밖 호출(`len`, `print`)까지 끊김으로 그리면 `run_pipeline` 깊이 3에 수백 개 마커가 찍힌다. §1.2의 `candidates >= 1` 분류가 이를 막는다. 구현 뒤 §5.4-2에서 마커 개수가 두 자리 이하인지 확인하라. 아니라면 분류가 잘못된 것이다.

5. **최대 깊이 노드에 스텁이 없는 것을 "여기가 끝"으로 읽는다.** 정직성 위반이다. 두 겹으로 막는다: 엣지 필터가 최대 깊이 노드에서 나가는 선을 그리지 않고, 범례가 `depth N — nothing past this ring was followed`를 인쇄한다.

6. **`FileBox`의 `memo` 붕괴.** 이 계획은 `FileBox`에 prop을 추가하지 않으므로 원칙적으로 안전하다. 다만 "행에 끊김 표시를 넣자"는 유혹이 구현 중에 올 것이다 — **넘어가지 말 것.** 105개 박스·1435개 행이 매 렌더 재조정되는 대가는 이 파일의 주석(1358-1370행)이 이미 설명하고 있다.

7. **재빌드 뒤 id 재발급.** `replace_file`은 `drop_file` 후 INSERT이므로 `functions.id`가 새로 발급된다. 트레이스 조회 효과의 의존성에 `index`가 빠지면 옛 id가 엉뚱한 함수를 밝힌다. `[files]`에 걸린 기존 `setOffsets` 초기화와 같은 종류의 방어다.

8. **경합하는 응답.** 깊이 −/+를 빠르게 누르면 응답이 뒤섞인다. `traceToken` ref로 늦게 온 것을 버린다 — `useFunctionGraph.ts:29-36`의 `alive` 관용구를 요청 단위로 확장한 것.

9. **스텁이 팬을 가로챈다.** 스텁 그룹에 `pointerEvents="auto"`를 주면 그 위에서 시작한 드래그가 문제가 될 수 있다. `startPan`은 `closest('[data-box]')`만 검사하므로 스텁은 여전히 "빈 캔버스"로 취급되어 팬이 된다 — 그 사실이 유지되는지(스텁에 `data-box`를 절대 붙이지 말 것) 확인하라.

10. **범례가 넘친다.** 트레이스 세그먼트가 붙으면 한 줄짜리 인라인 박스가 좁은 창에서 캔버스를 가릴 수 있다. `flex-wrap` + `max-w`로 감싸고, 이미 `pointer-events-none`이므로 클릭은 통과한다.

11. **`node:sqlite`의 재귀 CTE 지원.** `WITH RECURSIVE`는 SQLite 3.8.3+의 코어 기능이고 `node:sqlite`가 번들하는 버전은 훨씬 위다. 위험은 낮지만, `readGraphTrace`의 `try/catch → null`이 마지막 방어선이다 — 그 경우 화면은 "사슬 없음"이 되고 앱은 살아 있다.

12. **깊이 클램프가 두 곳에 생긴다.** UI 버튼과 main 핸들러 양쪽이 막는다. **판단은 `clampTraceDepth` 한 함수에만** 두고 양쪽이 그것을 부르게 하라 — `EDGE_STYLES`가 "세 곳이 하나의 표를 읽는다"로 해결한 문제와 같은 종류다.
