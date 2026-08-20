# Graph 탭 — LOD 엣지 + 시각 다듬기 · 구현 계획

이 slice가 쓰는 코드는 **전부 `desktop/` 안**이다. `aidev/**`는 한 줄도 건드리지 않는다 —
그래서 "기존 pytest 전량 통과"가 곧 엔진 무변경의 증거가 된다.

---

## 0. 읽고 확인한 계약 (이 계획이 지켜야 하는 것들)

| 사실 | 출처 |
|---|---|
| `calls` 테이블은 `caller_id / callee_name / callee_raw / lineno / resolved_id` 다섯 칸이고, `resolved_id`는 **존재하지 않는 id를 가리킬 수 있다**(픽스처의 999) | `aidev/graph/db.py:74`, `desktop/src/domains/graph-view/main/graph-store.test.ts:97` |
| 인덱스는 `idx_calls_caller`, `idx_calls_callee`, `functions.id`는 PK | `aidev/graph/db.py:91-96` |
| 앱은 graph.db를 **요청마다 열고 닫는 read-only**로만 읽는다(3조). 어떤 실패도 던지지 않고 `{ok:false, problem}`이 된다 | `graph-store.ts:82, 282` |
| `GraphIndexResult`를 만드는 곳은 **세 군데뿐** — `graph-store.ts:84`의 `empty`, `register-handlers.ts:90`, `:102` | grep `GraphIndexResult` |
| 현재 엣지는 **선택 노드의 calls/callers만** 그린다(`edgesFor`), 평시 엣지 0개 | `FunctionGraphSurface.tsx:204, 480` |
| 배치는 순수 산술이다: 경로순 → 열 그리디 패킹, 함수 1개당 anchor 1개(`boxIndex` 포함) | `layout.ts:73` |
| 성능 계약: `FileBox`는 `memo`, 무관한 상자는 **빈 배열 동일 참조**(`NO_IDS`)를 받아 리렌더를 건너뛴다 | `FunctionGraphSurface.tsx:18, 106, 362` |
| 곡선은 이미 베지어다. 오른쪽에 없는 타겟은 우회 레인으로 돈다 — 다만 레인 폭이 `Δy * 0.22`로 **무한히 커진다**(pipeline.py 상자 높이 3700px → 레인 +800px) | `layout.ts:134-148` |
| 화살표 마커 `fn-arrow`는 `stroke="context-stroke"` — 선 색을 그대로 따라간다. 다만 기본 `markerUnits="strokeWidth"`라 **굵은 선에서 화살표가 같이 커진다** | `FunctionGraphSurface.tsx:190-200` |
| 팔레트: `accent #4a7fc7 / ok #4f9d62 / warn #c39331 / line #232529 / line-strong #31343b / fg-mute #676c74`. 애니메이션은 `.rp-separator`의 100ms가 이 앱의 상한 | `theme.css:10-29, 106` |
| 선택 상태는 App이 소유(`functionId: number | null`)하고 `setFunctionId`가 그대로 내려간다 — **null을 받을 수 있는 그릇은 이미 있다** | `App.tsx:46, 138` |
| `tsconfig.test.json`은 `src/domains/**/*.ts`를 포함하되 `**/ui/**`는 제외한다 → **순수 로직을 `layout.ts`에 두어야 `node --test`가 잡는다** | `desktop/tsconfig.test.json:19-28` |
| 이 slice의 front matter에 `setup:`이 없고, 이 worktree에 `desktop/node_modules`가 **없다**(실측) → stage 안에서 `npm test`/`tsc` 실행 불가 | `.aidev/history/20260820-graph-visual/requirement.md`, glob 확인 |
| 자기 저장소 빌드 테스트가 파싱 실패율 5% 미만을 요구한다 → 새로 쓰는 TS는 jsparse가 읽을 수 있는 평범한 문법이어야 한다 | `tests/test_graph.py:612, 629` |

마지막 두 줄이 검증 전략을 결정한다 → §5.

---

## 1. 설계 결정 (이유 포함)

**D1. 집계 엣지는 `GraphIndexResult`에 실어 보낸다 — 새 IPC 채널 없음.**
`getGraphIndex`는 마운트 시 1회 + `graph build` 종료 시 1회만 불린다(`useFunctionGraph.ts:38`, `App.tsx:86`).
집계를 그 응답에 함께 담으면 "1회 계산 캐시"가 **공짜로** 성립하고, preload / `ide.ts` / `register-handlers`의
채널 목록은 손대지 않아도 된다. 계산 자체는 SQL `GROUP BY`다 → 요구사항 3의 "파일 쌍별 카운트 쿼리"를 문자 그대로 만족한다.

**D2. 함수 단위 인접(adjacency)도 인덱스에 함께 실어 보낸다.**
"호버 시 해당 노드+직접 이웃 하이라이트"를 DB 조회로 하면 마우스가 지나가는 행마다 IPC 왕복이 생긴다 —
1435행 위에서 그것은 곧 지연이다. `SELECT DISTINCT caller_id, resolved_id`는 최대 8.7k행(JSON 약 150–250KB,
지금 인덱스가 이미 나르는 1435개 함수 레코드보다 작다)이고, 이것을 **한 번** Map 두 개로 접으면 호버는 이후
메모리 조회만 남는다. 요구사항이 금지한 것은 *전체 함수 엣지의 동시 렌더*이지 적재가 아니다 — 그리는 것은
언제나 선택 노드의 이웃뿐이다.

**D3. 호버는 그리지 않고 밝히기만 한다.** 선택 = 엣지를 **그린다**, 호버 = **하이라이트만**.
요구사항의 단어 배분(선택→"엣지", 호버→"하이라이트")과 같고, 마우스가 행을 훑는 동안 SVG 패스가
생겼다 사라지는 깜빡임이 원천적으로 없다.

**D4. 디밍은 상자 단위 그룹 opacity로 한다.**
상자 내부까지 흐리려면 `FileBox`의 props가 전부 바뀌어 105개 상자 1435행이 리렌더된다 — 지금의 memo 계약이
깨진다. 대신 각 상자를 감싸는 `<g opacity={...}>` 래퍼를 memo **바깥**에 두면, 무관한 상자는 속성 1개만 갱신되고
자식(FileBox)은 참조가 같아 그대로 재사용된다. 집계 엣지 레이어도 마찬가지로 `<g opacity>` **하나**로 흐린다
— 240개 패스의 props를 건드리지 않고 한 속성으로 끝난다.

**D5. 선택 중에도 집계 레이어는 남기되 크게 흐린다(0.18).**
요구사항 2의 "무관 …엣지 디밍"이 성립하려면 흐려질 엣지가 있어야 하고, 구조가 통째로 사라지면 선택한 함수가
어디쯤 있는 파일인지 읽을 수 없다. 스파게티 걱정은 없다 — 0.18은 배경이지 선이 아니다.

**D6. 방향 색: calls = accent(파랑), callers = ok(초록), 그리고 실선/파선도 함께 다르게.**
새 토큰을 만들지 않는다(팔레트 준수). 두 색 모두 경보색이 아니라서 "호출 관계"에 의미가 얹히지 않는다.
`ok`는 이 캔버스에서 1.8px 점(명세 있음)으로만 쓰이므로 곡선과 혼동될 여지가 낮고, 색만으로 구분하지 않도록
파선(callers) 차이를 남긴다.

**D7. 집계 엣지는 굵기 상한과 **개수 상한**을 둔다.** 105개 파일이면 파일 쌍은 수백~천 단위가 될 수 있고,
그 전부를 그리면 요구사항이 피하려던 바로 그 헤어볼이 된다. 무게 내림차순 상위 `MAX_FILE_EDGES = 240`만 그리고,
**범례가 "top 240 of N file links"라고 사실을 말한다**(조용한 절단 금지).

**D8. 새 npm 의존성 0개, 물리 시뮬레이션 없음.** 기존 결정 그대로.

---

## 2. `desktop/src/domains/graph-view/types.ts` (수정)

두 타입을 추가하고 `GraphIndexResult`에 두 필드를 **필수로** 얹는다(필수여야 세 곳의 생성 지점이
컴파일 단계에서 전부 드러난다 — §3, §6).

```ts
/** One file→file link: `calls` grouped by the two ends' files. */
export interface GraphFileEdge {
  from: string
  to: string
  /** How many call sites in `from` resolve into a function of `to`. */
  weight: number
}

/** One resolved call, function to function, deduplicated. */
export interface GraphEdge {
  from: number
  to: number
}

export interface GraphIndexResult {
  // ... 기존 그대로 ...
  /** 평시 뷰가 그리는 것: 파일 쌍별 호출 총량, 무게 내림차순. */
  fileEdges: GraphFileEdge[]
  /** 호버 이웃 판정용 인접 목록의 원본. 렌더 대상이 아니다. */
  edges: GraphEdge[]
}
```

---

## 3. `desktop/src/domains/graph-view/main/graph-store.ts` (수정)

`readGraphIndex`에 쿼리 두 개를 더한다. 둘 다 `calls` 1회 스캔 + PK 조인이고, 실패 경로는 기존
`try/catch`가 그대로 삼킨다.

```sql
-- fileEdges: 파일 쌍별 호출 총량 (같은 파일 안의 호출은 링크가 아니다)
SELECT src.path AS from_path, dst.path AS to_path, COUNT(*) AS weight
  FROM calls c
  JOIN functions src ON src.id = c.caller_id
  JOIN functions dst ON dst.id = c.resolved_id
 WHERE src.path <> dst.path
 GROUP BY src.path, dst.path
 ORDER BY weight DESC, from_path, to_path;

-- edges: 해결된 호출을 함수 쌍으로 중복 제거 (자기 재귀 제외)
SELECT DISTINCT c.caller_id AS from_id, c.resolved_id AS to_id
  FROM calls c
  JOIN functions src ON src.id = c.caller_id
  JOIN functions dst ON dst.id = c.resolved_id
 WHERE c.caller_id <> c.resolved_id;
```

- `JOIN functions dst`가 **미해결(NULL)과 유령 id(999)를 동시에 걸러낸다** — 화면은 존재하는 노드만 받는다.
- `empty`(`:84`)에 `fileEdges: [], edges: []`를 넣는다 → no-graph / unreadable / schema 세 실패 경로가 모두
  같은 모양으로 돌아온다.
- 두 결과를 `{ ok: true, ... }`에 싣는다. 매핑은 `int()/text()` 헬퍼 그대로.
- 문서 주석 한 줄 추가: 왜 여기서 집계하는가(= 렌더 시 전체 스캔 금지, 빌드 1회당 1회 계산).

---

## 4. `desktop/src/domains/graph-view/layout.ts` (수정) — 새 순수 로직은 전부 여기

`ui/`는 테스트 대상이 아니므로(§0) **판단은 한 줄도 컴포넌트에 두지 않는다.**

```ts
/** 평시에 그리는 파일 링크의 상한. 넘는 만큼은 범례가 숫자로 말한다. */
export const MAX_FILE_EDGES = 240
export const EDGE_W_MIN = 0.5
export const EDGE_W_MAX = 4
/** 호버 한 번이 밝힐 수 있는 이웃의 상한 (say는 callers가 150이다). */
export const HOVER_MARK_LIMIT = 300
/** 우회 레인이 옆 열을 침범하지 못하게 하는 상한. */
export const LANE_MAX = 160

export interface GraphLayout {
  boxes: GraphBox[]
  anchors: Map<number, Anchor>
  /** path -> index into `boxes`: 파일 단위 엣지가 자기 양끝을 찾는 길. */
  boxOf: Map<string, number>
  width: number
  height: number
}

/** 파일 상자가 엣지를 물리는 자리 — 좌우 변의 한가운데. */
export function boxAnchor(box: GraphBox): EdgeAnchor

export interface FileLine {
  key: string        // `${from}->${to}`
  d: string
  from: string
  to: string
  weight: number
  width: number
  fromBox: number
  toBox: number
}
export interface FileLines {
  lines: FileLine[]
  /** 그린 개수와 전체 개수 — 범례가 절단을 숨기지 않기 위한 값. */
  shown: number
  total: number
}
export function fileLines(edges: GraphFileEdge[], layout: GraphLayout, limit?: number): FileLines

/** 무게를 굵기로. 최대 무게를 상한에 맞추고, 1은 언제나 보이는 최소 굵기. */
export function edgeWidth(weight: number, heaviest: number): number

export interface Adjacency { outs: Map<number, number[]>; ins: Map<number, number[]> }
export function buildAdjacency(edges: GraphEdge[]): Adjacency

/** 한 행이 지금 무엇으로 보여야 하는가. */
export type RowMark = 'selected' | 'calls' | 'callers' | 'both' | 'hover' | 'near'
export function neighbourMarks(adj: Adjacency, id: number, limit?: number): Map<number, RowMark>
export function mergeMarks(base: Map<number, RowMark>, extra: Map<number, RowMark>): Map<number, RowMark>
export function groupMarks(
  anchors: Map<number, Anchor>,
  marks: Map<number, RowMark>
): Map<number, Map<number, RowMark>>
```

규칙:

- `fileLines`: 양끝이 `boxOf`에 있는 쌍만 남기고(반쯤 쓰인 DB에서도 죽지 않는다), 무게 내림차순 상위
  `limit`(기본 `MAX_FILE_EDGES`)만 `d`를 계산한다. `total`은 자르기 **전** 개수.
- `edgeWidth(w, heaviest)`: `heaviest <= 1`이면 `EDGE_W_MIN`. 그 외 `EDGE_W_MIN + (EDGE_W_MAX -
  EDGE_W_MIN) * Math.sqrt(w / heaviest)` — 제곱근이라 무게 100과 300이 둘 다 최대치로 붙지 않는다.
  결과는 항상 `[EDGE_W_MIN, EDGE_W_MAX]` 안, NaN 없음.
- `neighbourMarks`: `outs`는 `'calls'`, `ins`는 `'callers'`, 양쪽에 다 있으면 `'both'`, 자기 자신은 `'hover'`.
  합계가 `limit`을 넘으면 넘는 만큼은 버린다(호버는 정보이지 완전성 보고가 아니다).
- `mergeMarks` 우선순위: `selected > both > calls = callers > hover > near`. `calls`와 `callers`가 만나면
  `both`로 승격.
- `groupMarks`: `anchors.get(id).boxIndex`로 상자별로 접는다. **마크가 하나도 없는 상자는 키 자체를 만들지
  않는다** — 그래야 컴포넌트가 공유 빈 Map을 건네고 memo가 유지된다.
- `edgePath`의 레인 폭을 `Math.min(LANE_MAX, Math.abs(to.y - from.y) * 0.22)`로 **클램프**한다.
  pipeline.py처럼 3700px짜리 상자에서 곡선이 옆 열을 가로지르며 튀는 것이 지금의 실제 증상이고,
  기존 테스트는 끝점만 보므로 그대로 통과한다.
- `layoutGraph`는 루프 안에서 `boxOf.set(file.path, boxIndex)` 한 줄만 늘어난다.

---

## 5. `desktop/src/domains/graph-view/ui/FunctionGraphSurface.tsx` (수정) — 화면

### 5.1 상태와 파생

```ts
const [hover, setHover] = useState<{ fn: number | null; path: string | null }>({ fn: null, path: null })
const focus = selected !== null                       // 집계 뷰 / 포커스 뷰
const adjacency = useMemo(() => buildAdjacency(index?.ok ? index.edges : []), [index])
const links = useMemo(() => fileLines(index?.ok ? index.fileEdges : [], layout), [index, layout])
const edges = useMemo(() => edgesFor(detail, layout.anchors), [detail, layout])   // 기존
const marks = useMemo(() => groupMarks(layout.anchors, mergeMarks(focusMarks, hoverMarks)), [...])
const dimmed = focus ? (boxIndex) => !marks.has(boxIndex) : () => false
```

`hover.path`(호버한 행이 속한 파일)를 `hover.fn`과 **따로** 들고 있는 이유: 집계 레이어는 `hover.path`에만
의존하므로, 마우스가 같은 파일 안 행들을 훑는 동안 240개 패스가 다시 그려지지 않는다.

### 5.2 렌더 트리

```
<svg>
  <defs>
    <marker id="fn-arrow" .../>                        {/* 기존, context-stroke */}
    <marker id="fn-arrow-flat" markerUnits="userSpaceOnUse" .../>  {/* 집계용: 굵기와 무관하게 일정 */}
  </defs>

  <rect width height fill="transparent" onClick={() => onSelect(null)} />   {/* 빈 곳 클릭 = 선택 해제 */}

  <g opacity={focus ? 0.18 : 1} style={{ transition: 'opacity 90ms linear' }}>
    {links.lines.map(line => <path ... strokeWidth={line.width} markerEnd="url(#fn-arrow-flat)" />)}
  </g>                                                  {/* 평시 = 집계 엣지, 여기 하나뿐 */}

  <g>{edges.map(...)}</g>                               {/* 포커스 = 개별 엣지 */}

  {layout.boxes.map((box, i) => (
    <g key={box.path} opacity={dimmed(i) ? 0.3 : 1}>
      <FileBox box={box} selectedId={...} marks={marks.get(i) ?? NO_MARKS}
               hot={hotBoxes.has(i)} onSelect={pick} onHover={onHover} />
    </g>
  ))}
</svg>
```

### 5.3 규칙 하나씩

- **평시(집계)**: `links.lines`만. 색 `--color-line-strong`, 굵기 `line.width`, 화살표
  `fn-arrow-flat`(`markerUnits="userSpaceOnUse"`라 4px짜리 굵은 선에서도 화살촉이 커지지 않는다).
  개별 함수 엣지는 `edges`가 `detail === null`이라 자연히 0개다 — 그리는 경로 자체가 없다.
- **선택**: `edges`가 calls(accent, 실선) / callers(ok, 파선)로 나뉜다. 미해결 caller 행에서 온 엣지는
  `opacity 0.55` — 엔진이 "이건 확실하지 않다"고 한 것을 화면도 확실하지 않게 그린다.
  자기 재귀(from === to)는 건너뛰고 행 마크로만 표시한다(지금은 납작한 선분이 그려진다).
- **디밍**: 포커스일 때 마크가 없는 상자 = `opacity 0.3`. 집계 레이어 = `opacity 0.18`.
  전환은 90ms linear 하나뿐(그 외 애니메이션 없음).
- **호버**: 상자 `<g>`에 `onMouseOver` 하나 + `onMouseLeave` 하나(이벤트 위임).
  `event.target.closest('[data-fn]')`가 잡히면 행 호버, 없으면 상자(헤더) 호버.
  행마다 리스너를 새로 만들지 않는다.
  - 행 호버 → `neighbourMarks(adjacency, id)`가 그 함수의 직접 이웃을 `calls/callers/both`로 밝히고,
    자기 행은 `hover`.
  - 상자 호버 → 그 파일과 링크된 상대 상자들이 `hot`(테두리 `--color-line-strong`), 해당 집계 선들이
    `--color-fg-dim`으로 밝아진다. 이웃 판정은 잘리지 않은 `index.fileEdges` 전체로 한다.
  - 호버는 **디밍하지 않는다**. 요구사항이 디밍을 붙인 곳은 선택뿐이고, 마우스 이동마다 105개 속성을
    건드리지 않는 편이 "지연 없음"에 곧바로 기여한다.
- **선택 해제(집계 뷰 복귀)**: 세 갈래 — ① 빈 캔버스 클릭, ② 이미 선택된 행을 다시 클릭(토글),
  ③ `Escape`(입력창에 포커스가 있을 때는 검색어 지우기가 먼저다). 모두 `onSelect(null)`.
- **`FileBox`**(memo 유지): `highlights: number[]` → `marks: ReadonlyMap<number, RowMark>`로 교체.
  `includes()` 선형 탐색이 사라진다. 마크별 왼쪽 2px 바: selected=accent(+accent-soft 채움),
  calls=accent, callers=ok, both=위아래 반씩 accent/ok, hover=fg-dim(+hover 채움), near=fg-mute.
  행마다 `data-fn={fn.id}`를 단다. 무관 상자는 공유 상수 `NO_MARKS`(빈 Map)를 받아 props 참조가 그대로다.
- **범례**: calls / called by / file → file (굵기 = 호출 수) / has spec / grey = no spec.
  집계가 잘렸으면 `top 240 of 1132 file links`를 그대로 적는다.
- `onSelect` 타입을 `(id: number | null) => void`로 넓힌다. `FunctionDetail`에 넘길 때는 그대로 통과한다
  (더 넓은 함수는 좁은 자리에 들어간다).

---

## 6. 나머지 배선 (작다)

| 파일 | 변경 |
|---|---|
| `desktop/src/app/main/register-handlers.ts:90, :102` | 두 실패 리터럴에 `fileEdges: [], edges: []` |
| `desktop/src/renderer/src/features/workspace/SurfacePane.tsx:26` | `onSelect: (id: number \| null) => void` |
| `desktop/src/renderer/src/App.tsx` | **변경 없음** — `setFunctionId`는 이미 `number \| null`을 받는다 |
| `preload/index.ts`, `shared/ide.ts` | **변경 없음** — 새 채널이 없다 |

---

## 7. 검증

### 7.1 stage 안에서 실제로 돌아가는 것

- **`python -m pytest`** (repo 루트). Done Criteria의 "기존 pytest 전량 통과"이며, 이 slice가
  `aidev/**`를 건드리지 않았다는 증거다.
- **`npm test`는 이 worktree에서 돌릴 수 없다.** 요구사항 front matter에 `setup:`이 없어
  `desktop/node_modules`가 없다(실측: `desktop/node_modules/.package-lock.json` 부재).
  `npm ci`는 이 stage에 허용되지 않았다 → test stage는 **"실행 불가"로 정직하게 보고**하고
  PASS/FAIL은 pytest로 판정한다. 억지로 통과시키려 하지 말 것.
- 부수 확인: `tests/test_graph.py`가 이 저장소 자신을 빌드하며 파싱 실패율 5% 미만을 요구한다.
  새로 쓰는 TS/TSX는 평범한 문법으로 — 중첩 템플릿 리터럴, 특이한 제네릭, JSX 안의 정규식 리터럴 금지.

### 7.2 이 slice가 추가하는 단위 테스트 (사람이 `cd desktop && npm ci && npm test`)

**`desktop/src/domains/graph-view/layout.test.ts` (확장)**

`bigRepo()`를 `bigRepo(fileCount = 78, total = 1335)`로 매개변수화한다(기존 단언 그대로 통과).

| 테스트 | 못 박는 것 |
|---|---|
| `boxOf` | 모든 파일 경로가 자기 상자 인덱스를 가리키고, 개수가 `boxes.length`와 같다 |
| `boxAnchor` | 좌/우가 상자의 두 변, y가 상자 세로 한가운데 |
| `edgeWidth` | 무게에 대해 단조 증가 · 항상 `[EDGE_W_MIN, EDGE_W_MAX]` · `heaviest = 0/1`에서 NaN 없음 · 최대 무게가 상한에 닿는다 |
| `fileLines` | 무게 내림차순 상위 `limit`개만 `lines`에 남고 `total`은 자르기 전 개수 · 없는 파일을 가리키는 쌍은 조용히 빠진다 · `d`가 출발 상자의 오른쪽 변에서 시작해 도착 상자에 닿는다 |
| `buildAdjacency` | outs/ins 양방향이 서 있고, 중복 쌍이 한 번만, 모르는 id는 빈 배열 |
| `neighbourMarks` | calls는 `'calls'`, callers는 `'callers'`, 둘 다면 `'both'`, 자기 자신은 `'hover'`, `limit` 초과분은 잘린다 |
| `mergeMarks` | 선택이 호버를 이긴다 · calls + callers = `'both'` · 원본 Map을 변형하지 않는다 |
| `groupMarks` | 상자별로 접히고, **마크 없는 상자는 키가 없다**(memo 계약) |
| `edgePath` 레인 | Δy가 아무리 커도 곡선의 제어점이 `x1 + 16 + LANE_MAX`를 넘지 않는다 |
| 규모 | `bigRepo(105, 1435)` + 6000개 엣지에서 `buildAdjacency` → 최고 차수 노드의 `neighbourMarks` → `groupMarks`가 정상 결과를 낸다(예외·스택 폭발 없음) |

**`desktop/src/domains/graph-view/main/graph-store.test.ts` (확장)**

픽스처 `calls`에 두 줄을 더한다 — `(5, 2, 'main', 'main', 102, 3)`, `(6, 2, 'main', 'cli.main', 103, 3)`.
`caller_id = 2`, `callee_name = 'main'`이라 기존 단언(run_pipeline의 calls 1개, callers 2개)은 **건드리지 않는다**.

| 테스트 | 못 박는 것 |
|---|---|
| `fileEdges` | `[{pipeline.py→cli.py, 2}, {cli.py→pipeline.py, 1}]` — 무게 내림차순 · **같은 파일 안의 호출(run_pipeline→say)은 링크가 아니다** · 미해결(NULL)과 유령 id(999)는 빠진다 |
| `edges` | `{(1,2), (3,1), (2,3)}` 정확히 — DISTINCT가 중복 호출을 한 줄로 접고, 자기 재귀·미해결·유령은 없다 |
| 실패 경로 | no-graph / 깨진 파일 / 스키마 2 모두 `fileEdges: []`, `edges: []`로 돌아온다(던지지 않는다) |

### 7.3 Done Criteria 실물 검증 (사람이, 이 저장소를 열고)

1. `cd desktop && npm ci && npm run dev` → 이 repo를 연다 → **Function graph** 탭.
2. **평시**: 파일 상자 사이에 집계 곡선이 보이고 굵기가 다르다. 개별 함수 엣지는 하나도 없다.
   범례가 `top N of M file links`를 말한다.
3. 검색 `run_pipeline` → Enter. **선택**: calls는 파란 실선, callers는 초록 파선, 화살표가 방향을 말하고,
   관계없는 상자와 집계 레이어가 흐려진다. 우측 패널은 지금처럼 동작한다.
4. `Escape`(또는 빈 곳 클릭, 또는 같은 행 재클릭) → **집계 뷰로 복귀**.
5. **호버**: 아무 행 위에 마우스를 올리면 그 행과 직접 이웃 행들이 밝아진다. 상자 헤더 위에서는
   상대 파일 상자와 그 링크가 밝아진다. 1435행 위를 훑어도 끊김이 없다.
6. `say`(callers 150) 위에서 호버·선택을 반복해도 체감 지연이 없다. 스크롤·줌(60%~180%)에서도 같다.

---

## 8. 위험과 대비

**R1. 파일 쌍 개수를 지금 측정할 수 없다**(PLAN은 읽기 전용). 105 파일이면 상한 240을 넘길 수도, 못 미칠 수도 있다.
→ `fileLines`가 항상 `total`을 함께 돌려주고 범례가 그 숫자를 적는다. 240은 `MAX_FILE_EDGES` 상수 한 줄이므로
실물을 보고 조정하면 된다. **절대 조용히 자르지 않는다.**

**R2. 상자 105개에 그룹 opacity를 걸면 합성 레이어가 늘어난다.** → 흐린 상자만 `<1`이고, 값이 바뀌는 순간은
선택이 바뀔 때뿐이다(호버는 디밍하지 않는다 — D3/§5.3). 그래도 무거우면 대안: 흐린 상자 위에
`pointer-events: none`인 `--color-app` 스크림 `<rect>` 한 장을 덮는다(합성 없이 같은 그림, 노드 105개 추가).

**R3. 인덱스 payload가 커진다**(엣지 8.7k행, 약 150–250KB). → 인덱스는 마운트와 build 종료 시에만 읽힌다.
그래도 체감되면 `edges`만 별도 채널로 떼어내면 되고, 그것은 `useFunctionGraph`에 fetch 한 줄을 더하는 일이다
(D1의 나머지 결정은 그대로 산다).

**R4. stage 안에서 TS가 컴파일되지 않는다** → 타입 오류가 사람의 `npm test`까지 잡히지 않는다.
완화: 이 계획이 새 시그니처와 SQL을 글자 그대로 적어 두었다. 제네릭·조건부 타입을 쓰지 않는다.
`GraphIndexResult`의 새 두 필드를 **필수**로 둔 것도 같은 이유다 — 빠뜨린 생성 지점이 `npm test` 첫 줄에서
전부 드러난다(생성 지점은 세 곳뿐이고 §3·§6에 다 적었다).

**R5. 고차수 노드 호버**(say: callers 150)가 여러 상자를 동시에 리렌더한다. → `HOVER_MARK_LIMIT = 300`이
상한을 잡고, 리렌더 대상은 마크를 실제로 받은 상자뿐이다(마크 없는 상자는 `groupMarks`가 키를 만들지 않는다).

**R6. 초록(ok)이 "명세 있음" 점과 겹쳐 읽힐 수 있다.** → 점은 상자 안 1.8px, 엣지는 캔버스를 가로지르는
곡선이고 범례가 둘 다 이름을 댄다. 파선/실선 차이도 남겨 색만으로 구분하지 않는다.

**R7. jsparse가 새 TSX를 못 읽으면 pytest의 파싱 실패율 단언에 걸린다.** → 기존 파일들의 문법 범위 안에서만
쓴다(§7.1). 변경은 대부분 기존 파일 수정이라 노출면이 작다.

---

## 9. 하지 않는 것 (요구사항 그대로)

전체 함수 엣지 동시 렌더 / 물리 시뮬레이션 배치 / 새 그래프·렌더 라이브러리 / `aidev/**` 수정 /
새 IPC 채널 / 새 색 토큰 / 90ms를 넘는 전환·애니메이션.
