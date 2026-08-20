# Graph 탭 — 줌 LOD + 미니맵 · 구현 계획

이 slice가 건드리는 코드는 **전부 `desktop/` 안**이다. `aidev/**`도, IPC 채널도, main 프로세스(`graph-store.ts`)도, `types.ts`도 한 줄도 바뀌지 않는다 — LOD·미니맵·검색 점프는 전부 이미 로드된 인덱스(`GraphIndexResult`)에서 파생되는 화면의 성질이기 때문이다. 그래서 "pytest 전량 통과"가 곧 엔진 무변경의 증거가 된다.

---

## 0. 읽고 확인한 계약 (이 계획이 지켜야 하는 것들)

| 사실 | 출처 |
|---|---|
| "Graph 탭"의 실물은 `FunctionGraphSurface`다. `graph` 탭은 authored mock에 `[DEMO]` 뱃지가 붙은 자리표시자이고 `edgePath` 하나만 공유한다 | `SurfacePane.tsx:141`, `GraphSurface.tsx:5` |
| 배치는 순수 산술이다 — 파일 경로순, `COLUMN_H=1400`까지 채우고 다음 열. 물리 시뮬레이션 없음 | `layout.ts:88-138` |
| **캔버스는 `width={size.width*zoom}` + `viewBox="0 0 size.width size.height"`** → 1 user unit = `zoom` client px, viewBox 밖은 잘린다 | `FunctionGraphSurface.tsx:488-492` |
| `zoom`은 **App의 상태**이고 `SurfacePane`이 0.6~1.8을 0.2씩 움직인다. mock `graph` 탭과 **같은 값을 공유**한다 | `App.tsx:52,293`, `SurfacePane.tsx:117-119` |
| 성능 계약: `FileBox`는 `memo`, 무관한 상자는 공유 `NO_MARKS`를 받아 리렌더를 건너뛴다. 드래그·opacity는 memo **바깥** 래퍼 `<g>`에 있다 | `FunctionGraphSurface.tsx:554-582`, 직전 plan D2 |
| 선택을 화면에 들이는 effect가 **이미 있다** — `layout.anchors.get(selected)`가 없으면 **아무 것도 하지 않고 반환**한다 | `FunctionGraphSurface.tsx:212-225` |
| 검색(`matchFunctions`)은 `flattenFunctions(files)` 전체를 본다 → **LOD와 무관하게 1452개 전부가 검색된다** | `layout.ts:565-601`, `FunctionGraphSurface.tsx:161-162` |
| `functions.kind`는 `function`/`method`/`arrow` 뿐이다. **export 여부를 담은 열이 없다** | `aidev/graph/model.py:77`, `jsparse.py:767`, `pyparse.py:86` |
| `index.edges`는 `DISTINCT caller_id → resolved_id`다 → **함수별 in-degree = 그 함수를 부르는 서로 다른 함수의 수**를 렌더러에서 바로 셀 수 있다 | `graph-store.ts:176-185` |
| `tsconfig.test.json`은 `src/domains/**/*.ts`를 포함하되 `**/ui/**`를 제외한다 → **순수 로직은 `layout.ts`에 두어야 `node --test`가 잡는다** | `desktop/tsconfig.test.json:19-28` |
| 이 worktree에 **`desktop/node_modules`가 없다**(실측: `.package-lock.json` 부재). front matter의 `test_commands: npx --prefix desktop tsc --noEmit -p desktop`는 오프라인이면 설치 단계에서 멈출 수 있다 | glob 확인, `.aidev/history/20260820-graph-lod/requirement.md:4` |
| `tests/test_graph.py:612`가 이 저장소 자신을 파싱하며 실패율 5% 미만을 요구한다 → 새 `.tsx`는 jsparse가 읽는 평범한 문법이어야 한다 | `tests/test_graph.py:602-612` |
| 색 토큰·아이콘은 있는 것만 쓴다: `panel/app/raised/hover/line/line-strong/fg/fg-dim/fg-mute/accent/accent-soft/ok/warn`, `Icon: graph·chevron·split·search` | `theme.css:9-37`, `Icon.tsx:8-23` |

---

## 1. 설계 결정 (이유 포함)

**D1. LOD는 "숨기기"가 아니라 "다시 배치하기"다.**
행만 감추고 상자 높이를 그대로 두면 원경은 *빈 직사각형 105개가 6800px에 흩어진 그림*이 된다 — 지금보다 나을 것이 없다. 그래서 `layoutGraph`가 **그 레벨이 실제로 그리는 행 수로 상자 높이를 정한다**. 파일 레벨에서 캔버스는 6808×1400 → **2044×1400**으로 줄고(105상자 × 90 = 7열), zoom 0.5에서 **1022×697 client px** — 한 화면에 들어온다. 이것이 "판독 가능"의 산술적 근거다. 앵커·`boxOf`·`fileLines`·미니맵·캔버스 크기가 전부 layout에서 파생되므로, 한 곳만 바꾸면 나머지는 저절로 일관된다.

**D2. 원경의 글자만 줌을 되돌린다(`fontSize = px / zoom`).**
zoom 0.5에서 11px 글자는 5.5px, 즉 읽을 수 없다. 파일 레벨의 **상자 헤더 두 줄만** user unit이 아니라 화면 px 기준으로 크기를 정한다(`12/zoom`, `9/zoom`). 268 user unit 폭이 zoom 0.5에서 134 client px이므로 12px mono로 **약 18자**가 들어간다 — 파일명은 읽힌다. 중경·근경의 글자는 **손대지 않는다**(요구사항: 근경은 기존 동작 유지).

**D3. 중경의 "핵심 함수"는 in-degree 상위 N이다.**
요구사항이 "callers 상위 N **또는** export된 것"이라고 둘 중 하나를 허락했고, `functions` 테이블에 export 열이 **없다**(§0). export를 쓰려면 `aidev/graph/*.py` + 스키마 + 재빌드가 필요하고 그것은 이 slice가 "하지 않는 것"의 정신에 어긋난다. `index.edges`의 in-degree는 이미 손에 있고, "누가 많이 부르는가"는 파일의 대표 함수를 고르는 기준으로 export보다 오히려 정직하다. 동점은 **원래 순서(경로, 줄 번호)**로 갈라 같은 그래프가 늘 같은 그림을 준다.

**D4. LOD는 `layout`의 입력이지 컴포넌트의 조건문이 아니다.**
`lodFor(zoom)`이 `'file' | 'key' | 'full'` 문자열을 돌려주고 `useMemo(..., [files, lod, counts])`가 layout을 만든다. **같은 밴드 안에서 줌을 움직이면 문자열이 같으므로 layout은 재계산되지 않는다.** 밴드를 넘을 때만 1회 재배치(1452 anchor Map 삽입, 1ms 미만)가 일어나고, 그 뒤로 그리는 행 수는 1452 → ≤630 → 0으로 **줄어든다**. 줌 아웃이 느려질 구조가 아니다.

**D5. 선택은 layout의 입력이 아니다.**
선택된 함수를 중경에 강제로 끼워 넣으면 layout이 선택마다 새 참조가 되어 105 상자 memo가 전부 깨지고, `offsets` 초기화 effect(`[layout]`)까지 얻어맞는다. 대신 **선택이 그려지지 않는 레벨이면 근경으로 줌한다**(D6). 그래도 남는 경우(사용자가 뭔가 고른 채로 손수 줌아웃)는 **엣지와 센터링이 그 함수의 *상자*에 붙는다** — 정보가 사라지는 대신 한 단계 거칠어질 뿐이다.

**D6. "보이지 않는 선택"은 없다 — 새 선택이 현 레벨에 그려지지 않으면 `ZOOM_DETAIL`로 올린다.**
검색 드롭다운·사이드바 파일 목록·`FunctionDetail`의 caller 링크가 전부 같은 `onSelect(id)` 한 길로 들어오므로, 규칙 하나를 surface에 두면 셋 다 고쳐진다(요구사항 3). **새 선택일 때만** 발동한다(`jumped` ref) — 아니면 사용자가 일부러 줌아웃할 때마다 화면이 도로 튀어 올라온다.

**D7. `offsets` 초기화의 근거를 `layout` → `files`로 바꾼다.**
지금은 "layout이 바뀌면 드래그를 버린다"인데, LOD가 layout을 바꾸게 되면 **줌 한 번에 끌어놓은 배치가 날아간다**. 초기화가 지키려던 것은 "rebuild·새로고침이면 원복"이고 그 진짜 출처는 `files`다(`index`에서만 온다). `offsets`는 path로 키를 잡으므로 레벨이 달라져도 의미가 유지된다.

**D8. 줌을 바꿔도 보던 곳을 계속 본다.**
`scrollLeft`는 client px이라 zoom이 바뀌면 전혀 다른 곳을 가리키고, LOD 전환에서는 좌표계 자체가 달라진다. 그대로 두면 "줌 아웃 = 길 잃기"다. `useLayoutEffect`에서 **직전 zoom·layout**(ref)으로 현재 뷰포트 중심을 user 좌표로 환산 → 그 점에 가장 가까운 상자의 **path**를 기억 → 새 layout에서 그 path를 중앙에 놓는다. path는 레벨이 바뀌어도 살아남는 유일한 신원이다.

**D9. 미니맵은 스크롤을 자기 안에서 구독한다.**
뷰포트 사각형을 surface state로 들면 스크롤 프레임마다 surface 전체가 리렌더된다(105개 래퍼 `<g>` 재조정). `Minimap`이 `scrollRef`를 받아 자기 안에서 `scroll` 리스너를 걸고, 상자 105개는 `memo`된 하위 컴포넌트로 분리한다 → **스크롤 한 프레임에 바뀌는 것은 `<rect>` 하나의 x/y뿐**이다.

**D10. 새 의존성 0개, 새 색 토큰 0개, 필터·토글·물리 배치 0개.** 요구사항의 "하지 않는 것" 그대로.

---

## 2. `desktop/src/domains/graph-view/layout.ts` (수정) — 새 순수 로직은 전부 여기

`ui/`는 `node --test`가 보지 않는다(§0). **판단과 산수는 한 줄도 컴포넌트에 두지 않는다.**

### 2.1 새 상수 — 전환 임계값은 전부 여기 한 곳에 (요구사항 1)

```ts
/** 어느 축척에서 무엇을 그리는가. 실측 후 이 세 줄만 고치면 된다. */
export type Lod = 'file' | 'key' | 'full'
/** 이 줌 이하는 파일 박스만. 0.55에서 파일 레벨 캔버스는 2044x1400 -> 1124x770 client px. */
export const LOD_FILE_MAX = 0.55
/** 이 줌 이하는 파일 박스 + 핵심 함수만. */
export const LOD_KEY_MAX = 0.85
/** 한 파일이 중경에서 보여주는 함수 수. */
export const KEY_FN_PER_FILE = 6
/** 파일 레벨 상자의 높이 (헤더 두 줄이 zoom 0.4에서도 들어가는 크기). */
export const FILE_BOX_H = 72
/** 파일 레벨 라벨이 화면에서 유지하는 크기 (px). user unit은 이것을 zoom으로 나눈 값. */
export const FILE_LABEL_PX = 12
export const FILE_META_PX = 9
/** 한 함수로 점프할 때 착지하는 줌 = 모든 행을 그리는 레벨. */
export const ZOOM_DETAIL = 1
/** 줌 사다리. 아래 두 칸이 이 slice가 새로 여는 원경이다. */
export const ZOOM_STEPS: readonly number[] = [0.4, 0.5, 0.7, 0.85, 1, 1.25, 1.5, 1.8]
/** 미니맵의 최대 크기 (client px). */
export const MINIMAP_W = 196
export const MINIMAP_H = 132
```

### 2.2 새 순수 함수

```ts
/** 이 줌에서 무엇을 그리는가. 경계는 포함이다(<=). */
export function lodFor(zoom: number): Lod

/** 사다리에서 한 칸 위/아래. 사다리에 없는 값은 가장 가까운 칸에서 출발한다. */
export function zoomIn(zoom: number): number
export function zoomOut(zoom: number): number

/** 함수별로, 그 함수를 부르는 서로 다른 함수의 수. */
export function callerCounts(edges: GraphEdge[]): Map<number, number>

/** 이 레벨이 이 파일에서 그리는 행. 'full'과 '전부 보이는 파일'은 받은 배열을
 *  그대로 돌려준다 — 참조가 같아야 memo가 헛돌지 않는다. */
export function visibleRows(
  functions: GraphFunction[],
  lod: Lod,
  counts: Map<number, number>,
  perFile?: number
): GraphFunction[]

/** 이 폭에 이 크기의 mono 글자가 몇 자 들어가는가. 원경 파일명 자르기의 근거. */
export function fitChars(width: number, fontSize: number): number

/** 한 점을 화면 가운데 놓는 스크롤 위치. 0 미만과 끝 너머로는 가지 않는다. */
export function centerScroll(
  point: { x: number; y: number },
  zoom: number,
  view: { width: number; height: number },
  size: { width: number; height: number }
): { left: number; top: number }

/** 지금 보고 있는 영역, user 좌표로. */
export function viewportOf(
  scroll: { left: number; top: number; width: number; height: number },
  zoom: number
): { x: number; y: number; w: number; h: number }

/** 이 점에 가장 가까운 상자의 path (중심 거리 기준). 상자가 없으면 null. */
export function nearestBoxPath(layout: GraphLayout, point: { x: number; y: number }): string | null

/** 상자 하나를 가운데 놓기 위한 점 = 그 상자의 중심(끌린 만큼 옮겨서). */
export function boxCenter(box: GraphBox, offset: Offset): { x: number; y: number }

/** 미니맵이 캔버스를 담는 배율과 그때의 미니맵 크기. */
export function minimapFit(
  size: { width: number; height: number },
  maxW?: number,
  maxH?: number
): { scale: number; width: number; height: number }

/** 엣지가 붙을 자리: 그 함수의 행, 없으면 그 함수가 든 상자의 옆면. 둘 다 없으면 null. */
export function resolveAnchor(
  layout: GraphLayout,
  byId: Map<number, GraphFunction>,
  byBox: Map<number, Offset>,
  id: number
): EdgeAnchor | null
```

규칙(구현이 흔들리지 않게 못 박는다):

- `lodFor`: `zoom <= LOD_FILE_MAX ? 'file' : zoom <= LOD_KEY_MAX ? 'key' : 'full'`. `NaN`/0 이하는 `'file'`.
- `zoomIn/zoomOut`: 현재 값과의 절대차가 가장 작은 칸을 찾아 ±1, 양 끝에서는 그 끝을 그대로 돌려준다.
- `callerCounts`: `edges`를 한 번 훑어 `to`를 센다(8669회, 인덱스 로드당 1회).
- `visibleRows`:
  - `'full'` → `functions` **그대로**(같은 참조).
  - `'file'` → 모듈 상수 `NO_ROWS: GraphFunction[] = []` **그대로**(같은 참조).
  - `'key'` → `functions.length <= perFile`이면 그대로. 아니면 `{fn, order}`로 감싸 `counts` 내림차순, 동점은 `order` 오름차순으로 정렬 → 앞 `perFile`개 → **다시 `order` 순으로 정렬**해 돌려준다(행은 언제나 좌표 순서).
- `fitChars`: `Math.max(4, Math.floor((width - 16) / (fontSize * 0.62)))` — Cascadia/Consolas의 실측 종횡비.
- `centerScroll`: `left = clamp(x*zoom - view.width/2, 0, max(0, size.width*zoom - view.width))`, `top`도 같은 꼴.
- `viewportOf`: `{ x: left/zoom, y: top/zoom, w: width/zoom, h: height/zoom }`. `zoom <= 0`은 1로 취급.
- `resolveAnchor`: `layout.anchors.get(id)`가 있으면 `shiftAnchor(anchor, byBox.get(anchor.boxIndex) ?? ZERO)`. 없으면 `byId.get(id)?.path` → `layout.boxOf` → `shiftAnchor(boxAnchor(box), ...)`. 둘 다 없으면 null.

### 2.3 기존 것의 변경 — **모두 기본 인자라 기존 호출부·기존 테스트는 글자 하나 안 바뀐다**

```ts
export function boxHeight(count: number, lod: Lod = 'full', hidden = 0): number
// 'file'  -> FILE_BOX_H
// 그 외   -> HEADER_H + count * ROW_H + (hidden > 0 ? ROW_H : 0) + PAD_B

export function layoutGraph(
  files: GraphFileGroup[],
  lod: Lod = 'full',
  counts: Map<number, number> = NO_COUNTS   // 새 공유 빈 Map
): GraphLayout
```

`GraphBox`에 네 필드가 붙는다. **헤더의 산수를 `FileBox`에서 여기로 옮기는 것**이기도 하다 — 지금은 상자마다 `functions.filter(...)`를 두 번 돌린다.

```ts
export interface GraphBox {
  path: string
  lang: string
  x: number; y: number; width: number; height: number
  /** 이 레벨이 그리는 행. 파일 레벨에서는 비어 있다. */
  functions: GraphFunction[]
  /** 이 파일이 정의한 함수 전부 — 그리든 말든. 헤더의 "N fn"은 이것이다. */
  total: number
  /** 명세 커버리지: 테스트가 아닌 함수와, 그 중 명세가 있는 수. */
  specTotal: number
  specCovered: number
  /** 이 레벨이 그리지 않는 행 수. 0보다 크면 "+N more" 한 줄이 붙는다. */
  hidden: number
}
```

`layoutGraph` 본문은 `const rows = visibleRows(file.functions, lod, counts)` 한 줄과 `boxHeight(rows.length, lod, file.functions.length - rows.length)`로 바뀌고, **앵커는 `rows`에 대해서만** 만들어진다(파일 레벨은 앵커 0개 = 함수 엣지 0개, 요구사항 그대로). 열 채우기 규칙(`COLUMN_H`)·`edgePath`·`fileLines`·marks 계열·`matchFunctions`는 **변경 없음**.

---

## 3. `desktop/src/domains/graph-view/ui/FunctionGraphSurface.tsx` (수정)

### 3.1 새 prop 하나

```ts
/** 줌을 바꿀 수 있는가 — 검색 점프가 축척을 함께 데려오는 길(D6). */
onZoomChange?: (zoom: number) => void
```

### 3.2 파생값

```ts
const files = index?.ok ? index.files : NO_FILES        // 모듈 상수. 참조 안정성 = D7의 전제
const lod = useMemo(() => lodFor(zoom), [zoom])
const counts = useMemo(() => callerCounts(index?.ok ? index.edges : []), [index])
const layout = useMemo(() => layoutGraph(files, lod, counts), [files, lod, counts])
const byId = useMemo(() => new Map(functions.map((fn) => [fn.id, fn])), [functions])
```

`marks`/`focusMarks`/`hoverMarks`/`hotBoxes`/`links`/`size`/`byBox`의 정의는 그대로다 — 전부 `layout`에서 파생되므로 레벨을 따라간다.

`focusBoxes`에 한 줄 추가: 앵커로 못 찾은 선택은 **그 함수의 path로** 상자를 찾는다(파일 레벨에서도 선택된 파일이 밝게 남는다).

`edgesFor(detail, layout, byId, byBox)`로 시그니처를 바꾸고, 양 끝 좌표를 `resolveAnchor`로 얻는다(§2.2). 파일 레벨에서는 요구사항대로 **함수 엣지를 아예 그리지 않는다**: `if (lod === 'file') return []`를 맨 앞에.

### 3.3 effect — 순서가 곧 우선순위다 (뒤에 선언된 것이 스크롤을 이긴다)

| # | 이름 | deps | 하는 일 |
|---|---|---|---|
| 1 | `offsets` 원복 | `[files]` | D7. `setOffsets(prev => prev.size === 0 ? prev : NO_OFFSETS)` |
| 2 | 새 선택이면 축척을 데려온다 | `[selected, onZoomChange]` | D6. `jumped` ref로 **새 선택일 때만**. `layoutRef.current.anchors.has(selected)`가 false면 `onZoomChange?.(ZOOM_DETAIL)` |
| 3 | 보던 곳을 계속 본다 (`useLayoutEffect`) | `[zoom, layout]` | D8. `view` ref(직전 zoom·layout)로 이전 중심 → `nearestBoxPath` → 새 layout에서 `centerScroll(boxCenter(...))`. 첫 실행(ref 비어 있음)은 아무것도 하지 않는다. `pendingPath` ref가 있으면 그 path를 우선한다(§3.5) |
| 4 | 선택을 화면에 (기존, 수정) | `[selected, layout]` | `resolveAnchor`로 좌표를 얻어(행이 없으면 상자) `centerScroll`로 스크롤. **`zoom`은 ref로 읽는다** — 줌 칸을 옮길 때마다 다시 튀지 않게 |
| 5 | Escape / detail 로드 / detailOpen | 기존 그대로 | — |

effect 4가 `if (!anchor) return`으로 조용히 죽던 것이 요구사항 3의 "미동작"에 해당하는 지점이다. 이제 **행이 없으면 상자로, 레벨이 낮으면 effect 2가 레벨을 올린 뒤 layout이 바뀌면서 다시 실행**되어 반드시 착지한다.

### 3.4 렌더 트리 (바뀌는 곳만)

스크롤 컨테이너를 `relative` 래퍼로 한 겹 감싸, 미니맵과 범례를 **스크롤되지 않는 층**에 올린다(지금 범례는 `sticky` 꼼수다).

```
<div className="relative min-w-0 flex-1">
  <div ref={canvas} className="h-full w-full overflow-auto bg-app">
    <svg …기존…>                       {/* defs·배경 rect·FileLinks·엣지: 그대로 */}
      {layout.boxes.map((box, i) => (
        <g …드래그·opacity·hover 그대로…>
          <FileBox box={box} lod={lod} labelScale={1 / Math.max(zoom, 0.1)}
                   selectedId={…} marks={…} hot={…}
                   onSelect={pickRow}
                   onPickFile={lod === 'file' ? pickFile : undefined} />
        </g>
      ))}
    </svg>
  </div>
  <Legend shown={links.shown} total={links.total} lod={lod} boxes={layout.boxes.length} />
  <Minimap … />
</div>
```

`Legend`는 `absolute bottom-2 left-2`로 바뀌고 **지금 무엇을 보고 있는지 한 마디를 더한다**: `files only · 105 boxes` / `top 6 fn per file` / `all 1452 fn`. 행이 사라진 이유가 화면에 적혀 있지 않으면 그것은 그래프가 저장소에 대해 거짓말을 하는 것이다.

### 3.5 `FileBox` — 레벨별 헤더

`memo`는 그대로. 새 prop은 `lod`, `labelScale`, `onPickFile` 셋이고 **`labelScale`은 파일 레벨에서만 읽힌다**(다른 레벨에서는 props가 zoom과 무관하게 안정적이다).

- **`lod === 'file'`**: 상자 안에 두 줄만 그린다. 파일명 `fontSize={FILE_LABEL_PX*labelScale}`, `truncate(name, fitChars(BOX_W, size))`; 아래 줄에 `{total} fn · {covered}/{specTotal}` `fontSize={FILE_META_PX*labelScale}`, 커버리지가 모자라면 `--color-warn`. 구분선·행은 없다. 상자 전체에 `onClick={() => onPickFile?.(box.path)}`(드래그면 기존 `onClickCapture`가 삼킨다) → **원경에서 파일을 눌러 근경으로 들어가는 길**.
- **`lod === 'key'`**: 지금의 헤더 그대로 + 행 ≤6개 + `hidden > 0`이면 마지막에 `+{hidden} more` 한 줄(`--color-fg-mute`, 클릭 없음 — 토글은 다음 slice다).
- **`lod === 'full'`**: **지금과 픽셀 단위로 같다.** 헤더 숫자만 `box.total`/`box.specCovered`/`box.specTotal`에서 읽는다(값은 동일, 계산 위치만 layout으로 이동).

`pickFile(path)`: `pendingPath.current = path` → `onZoomChange?.(ZOOM_DETAIL)`. 착지는 effect 3이 한다.

### 3.6 프레임당 비용

| 조작 | 다시 계산되는 것 |
|---|---|
| 같은 밴드 안 줌 | layout·counts·marks·links **전부 memo 유지**. `<svg>`의 width/height 두 속성 + (파일 레벨이면) 105개 헤더 글자 크기 |
| 밴드 전환 | `layoutGraph` 1회(105 상자, ≤1452 anchor), `fileLines` 1회(≤240 베지어), 105 상자 리렌더. **그 뒤 그리는 행 수는 1452 → ≤630 → 0으로 줄어든다** |
| 스크롤 | `Minimap` 안의 `<rect>` 하나 (D9) |
| 드래그 | 직전 slice 그대로 — 1452행은 리렌더되지 않는다 |

---

## 4. `desktop/src/domains/graph-view/ui/Minimap.tsx` (신규) — 요구사항 2

순수 산수는 전부 §2.2에 있다. 이 파일은 그것을 SVG로 옮기고 포인터를 받는 일만 한다.

```tsx
export function Minimap({
  scrollRef,          // 캔버스 스크롤 컨테이너
  layout, offsets, size, zoom,
  open, onToggle
}: { … }): JSX.Element
```

- **조감**: `minimapFit(size)` 배율로 `layout.boxes`를 `<rect>` 105개로 그린다(`fill: --color-raised`, `stroke: --color-line`). 끌린 상자는 `offsetOf`만큼 옮긴다. 이 부분은 `MinimapBoxes = memo(…)`로 분리한다(D9).
- **뷰포트 사각형**: 자기 안에서 `scrollRef.current`에 `scroll` 리스너를 걸고(passive), `viewportOf(...)`를 state로 든다. `open`/`zoom`/`size`가 바뀌면 즉시 한 번 다시 읽는다(리스너만으로는 줌 직후 값이 낡는다). `stroke: --color-accent`, `fill: --color-accent-soft`, `fillOpacity 0.25`.
- **이동**: `pointerdown` → `setPointerCapture` → 눌린 점을 배율로 나눠 user 좌표로 → `centerScroll(...)` → `scrollRef.current.scrollTo({left, top})`(`behavior` 없음 — 드래그는 즉시 따라와야 한다). `pointermove`는 버튼이 눌린 동안 같은 일을 반복한다. `pointerup/cancel`에서 캡처 해제.
- **접기**: `open`이면 `absolute right-2 bottom-2` 패널(테두리 `--color-line`, 배경 `--color-panel/90`)에 우상단 셰브런 접기 버튼(`Icon name="chevron"`, `aria-expanded`). 접히면 그 자리에 `Icon name="graph"` 아이콘 버튼 하나만 남는다(`title="Show minimap"`). `open` 상태는 surface가 든다(`const [mapOpen, setMapOpen] = useState(true)`).
- 그래프가 비었으면(`size.width === 0`) 아무것도 렌더하지 않는다.

---

## 5. `desktop/src/renderer/src/features/workspace/SurfacePane.tsx` (수정, 작다)

```tsx
<ZoomButton label="−" onClick={() => onZoomChange(zoomOut(zoom))} />
<span className="w-9 text-center">{Math.round(zoom * 100)}%</span>
<ZoomButton label="+" onClick={() => onZoomChange(zoomIn(zoom))} />
```

`Math.max(0.6, …)` / `Math.min(1.8, …)` 두 줄이 `zoomOut`/`zoomIn`으로 바뀐다 — **원경 두 칸(0.4·0.5)을 여는 것이 이 파일의 전부**다. 위 끝(1.8)은 그대로. mock `graph` 탭도 같은 `zoom`을 쓰지만 그쪽은 그냥 더 작게 그려질 뿐이고, 기본값 1은 변하지 않으므로 **첫 화면은 지금과 같다**. `FunctionGraphSurface`에 `onZoomChange={onZoomChange}` 한 줄을 더한다.

`FunctionGraphSidebar.tsx`·`FunctionDetail.tsx`·`App.tsx`·`useFunctionGraph.ts`는 **수정하지 않는다** — 셋 다 `onSelect(id)` 한 길로 들어오고, 축척은 surface가 D6 규칙으로 알아서 데려온다.

---

## 6. 검증

### 6.1 stage 안에서 실제로 돌아가는 것

- **`python -m pytest`** (repo 루트). `aidev/**`를 건드리지 않았다는 증거이자 이 slice의 PASS/FAIL 판정 근거. `tests/test_graph.py:602-612`가 **새로 쓴 `Minimap.tsx`·수정한 `.tsx`를 실제로 파싱**하므로, 파싱 실패율 5% 선이 새 파일의 문법 게이트 역할을 한다 → 중첩 템플릿 리터럴·특이한 제네릭·JSX 안 정규식 리터럴 금지.
- **`npx --prefix desktop tsc --noEmit -p desktop`** (front matter의 `test_commands`). 이 worktree에는 `desktop/node_modules`가 없다(§0) → 오프라인이면 typescript를 받지 못하고 멈춘다. **그 경우 "실행 불가"로 정직하게 보고**하고, 억지로 통과시키지 않는다. 대비는 §7 R5.
- `node --test`(`npm test`)도 같은 이유로 이 worktree에서는 못 돈다. 그래도 **테스트는 전부 쓴다** — 로컬에서 `npm ci` 후 한 번에 돌아가고, 이 계획이 못 박은 계약이 코드에 남는다.

### 6.2 `desktop/src/domains/graph-view/layout.test.ts` (수정) — 기존 테스트는 한 줄도 고치지 않는다

새 `describe` 블록만 붙는다(기본 인자 덕에 `layoutGraph(files)`·`boxHeight(400)` 호출부가 그대로다).

| 테스트 | 못 박는 것 |
|---|---|
| `lodFor` | 0.4·0.55 → `file`, 0.7·0.85 → `key`, 0.86·1·1.8 → `full`. 0·NaN → `file` |
| `zoomIn/zoomOut` | 사다리를 한 칸씩 오르내리고 양 끝에서 멈춘다 · 사다리에 없는 0.63도 가장 가까운 칸에서 출발한다 · `zoomOut` 두 번이면 `full`에서 `file`까지 내려간다 |
| `callerCounts` | in-degree가 맞고, 아무도 부르지 않는 id는 키가 아니다 |
| `visibleRows` | `'full'`은 **받은 배열 그대로(참조 동일)** · `'file'`은 항상 같은 빈 배열(참조 동일) · `'key'`는 `KEY_FN_PER_FILE`개, **caller 많은 것부터 고르되 결과는 원래 줄 순서** · 동점이면 원래 순서가 이긴다 · 함수가 N 이하인 파일은 참조 그대로 |
| `layoutGraph(files,'file')` | 상자 105개, **앵커 0개**, 모든 상자 높이 = `FILE_BOX_H`, `total`/`specTotal`/`specCovered`는 **레벨과 무관하게 `'full'`일 때와 같다**, `hidden === total` |
| `layoutGraph(files,'key',counts)` | 상자당 행 ≤6 · `hidden = total - 행수` · 앵커는 그려진 행에만 있다 · `boxOf`는 여전히 파일 전부를 안다 |
| **축척 효과 (Done Criteria의 근거)** | `bigRepo(105,1452)`에서 `'file'` 캔버스의 **넓이가 `'full'`의 1/3 미만**이고 높이는 `COLUMN_H` 부근이다 — "줌 아웃 시 판독 가능"을 숫자로 증명 |
| `boxHeight` | `'file'`은 개수와 무관하게 `FILE_BOX_H` · `hidden>0`이면 한 줄만큼 높다 · `boxHeight(400)`은 예전 값 그대로 |
| `fitChars` | 폭이 좁아도 4자 밑으로는 안 내려간다 · 글자가 커지면 자릿수가 준다 |
| `centerScroll` | 점이 화면 가운데에 온다 · 왼쪽 위 끝에서 음수가 되지 않는다 · 오른쪽 끝에서 캔버스 밖으로 넘지 않는다 · 캔버스가 뷰포트보다 작으면 0 |
| `viewportOf` | zoom 0.5에서 폭이 두 배가 된다 · zoom 0은 1로 취급 |
| `nearestBoxPath` | 상자 중심을 주면 그 상자 · 캔버스 밖의 점도 가장 가까운 상자를 준다 · 빈 layout은 null |
| `minimapFit` | 가로로 긴 캔버스는 폭에, 세로로 긴 캔버스는 높이에 맞는다 · 결과가 `MINIMAP_W/H`를 넘지 않는다 · 0 크기 캔버스가 NaN을 내지 않는다 |
| `resolveAnchor` | 행이 있으면 행 · 없으면 그 함수가 든 **상자의 옆면** · 모르는 id는 null · 끌린 상자면 옮겨진 좌표 |

### 6.3 Done Criteria 실물 검증 (사람이, 이 저장소를 열고)

1. `cd desktop && npm ci && npm run dev` → 이 repo → **Function graph** 탭 (100%, 기존 화면 그대로).
2. **줌 아웃**: `−`를 눌러 85% → 70%(중경): 상자가 짧아지고 파일당 대표 함수 6개 + `+N more`만 남는다. 50% → 40%(원경): **파일 박스만 남고 캔버스가 한 화면에 들어온다.** 파일명이 읽히고, 그 아래 `N fn · c/t`가 보인다. 파일 간 집계 곡선은 그대로 있고 함수 엣지는 없다.
3. **범례**가 `files only · 105 boxes` 등으로 지금 무엇을 보고 있는지 말한다.
4. **원경에서 파일 상자 클릭** → 100%로 들어가면서 그 파일이 화면 가운데 온다.
5. **미니맵**: 우하단에 전체 조감 + 파란 뷰포트 사각형. 다른 곳을 클릭하면 캔버스가 그리로 간다. 사각형을 끌면 캔버스가 따라온다. 셰브런으로 접으면 아이콘 하나만 남고, 다시 누르면 돌아온다. 줌을 바꾸면 사각형 크기가 그에 맞게 변한다.
6. **검색 점프**: 40%(원경)에서 `say`를 검색해 고른다 → **줌이 100%로 올라가고 그 행이 화면 가운데** 오며 파란/초록 엣지가 그려진다. 사이드바 파일 목록 클릭, `Node` 패널의 caller 링크도 같다.
7. **길 잃지 않기**: 근경에서 저장소 오른쪽 끝까지 스크롤 → 줌 아웃 → **같은 파일 근처를 보고 있다**(D8).
8. **회귀**: 100%에서 hover·선택·드래그·`Reset positions`·`Escape`·빈 캔버스 클릭이 직전 slice와 똑같다. 상자를 끌어놓고 줌을 바꿔도 **끌어놓은 자리가 유지된다**(D7). [Rebuild] 하면 원복된다.
9. **지연**: 40%↔100%를 빠르게 왕복해도 끊김이 없다. 1452행 위 hover, `say`(callers 150) 선택 — 이전과 같은 반응 속도.

---

## 7. 위험과 대비

**R1. 원경이 여전히 안 읽힌다** (가장 그럴듯한 실패). 이 계획은 두 축으로 방어한다 — 배치를 줄이고(D1: 6808px → 2044px) 글자를 줌으로 나눈다(D2: 화면에서 항상 12px). 그래도 모자라면 고칠 곳은 **상수 세 개뿐**이다: `LOD_FILE_MAX`(더 일찍 전환), `FILE_BOX_H`/`FILE_LABEL_PX`(더 큰 라벨), `COLUMN_H`(더 정사각에 가까운 조감). 요구사항이 "전환 임계값은 상수로"라고 한 이유가 이것이고, 세 값 모두 `layout.ts` 위쪽 한 곳에 모여 있다.

**R2. 줌마다 재배치가 눈에 띄게 느리다.** 밴드 안에서는 재배치가 **아예 일어나지 않는다**(D4, `lod` 문자열 memo). 전환 1회의 비용은 105 상자 + ≤1452 anchor 삽입이고, 이는 마운트 때 이미 치르는 비용과 같다. 그래도 느리면 다음 수는 `layoutGraph`를 레벨별로 3개 캐시하는 것(`useMemo` 3개) — 순수 함수라 캐시가 안전하다.

**R3. 선택이 그려지지 않는 레벨에서 상세/엣지가 깨진다.** `resolveAnchor`가 행 → 상자 → null 순으로 물러난다(§2.2). 파일 레벨은 함수 엣지를 아예 그리지 않으므로(요구사항) 애초에 좌표가 필요 없다. `FunctionDetail`은 LOD를 모르고 `detail`만 보므로 **어느 레벨에서도 명세·callers가 그대로 보인다.**

**R4. 자동 줌(D6)이 사용자의 줌 아웃을 되돌린다.** `jumped` ref로 **선택 id가 실제로 바뀐 순간에만** 발동한다. 줌·layout은 deps에 없다. 뭔가 고른 채 손수 줌아웃하면 화면은 원경에 머무르고 선택은 상자 강조로만 남는다.

**R5. stage 안에서 TS가 컴파일되지 않아 타입 오류가 늦게 드러난다.** 완화: (a) 새 시그니처를 §2에 글자 그대로 적었고 제네릭·조건부 타입을 쓰지 않는다. (b) 바뀌는 **호출부가 전부 이 계획 안에 열거**되어 있다 — `layoutGraph`(1곳), `boxHeight`(layout 내부), `edgesFor`(1곳), `fileLines`(변경 없음), `GraphBox` 소비자(`FileBox`·`Minimap`·`boxAnchor`). (c) 새 prop은 전부 선택적이거나 호출 지점이 하나다. (d) `GraphBox`에 필드를 **더하기만** 하므로 기존 소비자는 깨지지 않는다.

**R6. 미니맵의 스크롤 구독이 리렌더 폭풍이 된다.** D9의 분리(상자 memo + 사각형 하나)가 1차 방어. 그래도 무거우면 다음 수는 사각형을 state가 아니라 `useRef` + 직접 `setAttribute`로 옮기는 것 — `Minimap` 안에서 끝나고 다른 파일은 그대로다.

**R7. `zoom`이 mock `graph` 탭과 공유된다.** 0.4·0.5가 새로 열리므로 그 탭도 더 작게 그려질 수 있다. mock은 authored 좌표라 아무 것도 깨지지 않고, 기본값 1은 그대로다. 탭별 줌 분리는 이 slice의 범위가 아니다(App 상태 구조 변경).

**R8. D8(중심 유지)과 effect 4(선택 센터링)가 싸운다.** 선언 순서로 결정한다 — 3번이 먼저, 4번이 나중이라 **선택이 있으면 선택이 이긴다**. 둘 다 같은 `scrollTo`를 부르므로 마지막 호출만 남는다. 선택이 없으면 3번만 돈다.

**R9. `visibleRows`가 `'full'`에서 새 배열을 만들면 memo가 조용히 죽는다.** 테스트가 **참조 동일성**을 직접 확인한다(§6.2). 이것이 105 상자·1452행이 지금 살아 있는 이유이므로 계약으로 못 박는다.

---

## 8. 하지 않는 것 (요구사항 그대로)

필터·토글 UI(`+N more`는 글자일 뿐 버튼이 아니다) / 물리 배치·force 시뮬레이션 / 렌더 라이브러리 교체 / 새 npm 의존성 / 새 IPC 채널·새 타입·`types.ts` 수정 / `aidev/**` 수정 / `functions` 테이블에 export 열 추가 / 줌·미니맵 상태의 영구 저장 / mock `GraphSurface`의 LOD / 탭별 줌 분리 / 90ms를 넘는 전환 애니메이션.
