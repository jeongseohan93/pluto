# Graph 탭 — 노드 드래그 + 패널 최소화 · 구현 계획

이 slice가 건드리는 코드는 **전부 `desktop/` 안**이다. `aidev/**`도, IPC 채널도, main 프로세스도,
`types.ts`도 한 줄도 바뀌지 않는다 — 드래그 위치는 저장하지 않기로 한 요구사항 그 자체가
"렌더러 안에서만 사는 상태"를 뜻하기 때문이다. 그래서 "기존 pytest 전량 통과"가 곧 엔진 무변경의 증거가 된다.

---

## 0. 읽고 확인한 계약 (이 계획이 지켜야 하는 것들)

| 사실 | 출처 |
|---|---|
| **"Graph 탭"은 `FunctionGraphSurface`다.** 직전 slice(`20260820-graph-visual`, 같은 제목 형식)가 이 파일만 고쳤고, `graph` 탭은 authored mock에 `[DEMO]` 뱃지가 붙은 v0.0.1 자리표시자다 | `.aidev/history/20260820-graph-visual/plan.md`, `GraphSurface.tsx:7-16` |
| 화면의 **노드는 파일 상자**(`FileBox`), 행은 그 안의 함수다. 배치는 순수 산술(`layoutGraph`)이고 물리 시뮬레이션이 없다 | `layout.ts:88`, `FunctionGraphSurface.tsx:571` |
| 엣지는 **두 층** 모두 layout에서 좌표를 얻는다 — 파일 링크는 `fileLines(fileEdges, layout)`(=`boxAnchor`), 함수 엣지는 `edgesFor(detail, layout.anchors)` | `layout.ts:216`, `FunctionGraphSurface.tsx:718` |
| **성능 계약**: `FileBox`는 `memo`이고, 무관한 상자는 공유 `NO_MARKS`를 받아 리렌더를 건너뛴다. 상자를 감싼 `<g>`(opacity·hover 핸들러)는 memo **바깥**에 있어서, 그 `<g>`의 속성만 바뀌면 자식 1435행은 그대로 재사용된다 | `FunctionGraphSurface.tsx:370-386`, 직전 plan D4 |
| 캔버스는 `width={layout.width * zoom}` + `viewBox="0 0 layout.width layout.height"` — **viewBox 밖은 잘린다**. 1 user unit = `zoom` client px | `FunctionGraphSurface.tsx:308-312` |
| 행 선택은 `<g onClick>` 하나, 빈 캔버스 클릭(`<rect onClick>`)은 선택 해제다. 즉 **드래그가 끝난 지점의 click을 삼키지 않으면 선택이 풀린다** | `FunctionGraphSurface.tsx:344-348, 628` |
| **`body { user-select: none }`이 전역이다** — 텍스트 선택 방지는 이미 서 있고, 이 slice가 할 일은 네이티브 드래그/포커스 부작용을 `preventDefault`로 막는 것뿐 | `theme.css:57` |
| 셸 레이아웃: 바깥 `Group`(horizontal) = [Sidebar 260px] · [main] · [Inspector 300px], 셋 다 `groupResizeBehavior="preserve-pixel-size"`(**크기 단위는 px**) | `App.tsx:188-301` |
| **접기 패턴이 이미 있다**: BottomPanel이 `usePanelRef` + `<Panel panelRef collapsible collapsedSize={32}>` + `isCollapsed()/collapse()/expand()`로 동작한다. 새 라이브러리도 새 API 조사도 필요 없다 | `App.tsx:54, 169-179, 263-270` |
| 좌측에는 **항상 보이는 44px `ActivityBar`가 이미 있다**(Group 바깥). 우측에는 그런 것이 없다 | `App.tsx:186`, `ActivityBar.tsx:35` |
| 셸 `Inspector`는 mock 그래프의 `selectedSymbolId`만 본다. **함수 노드(`functionId`)의 정보를 보여주는 것은 surface 안의 `FunctionDetail`이다** | `Inspector.tsx:9-12, 30`, `FunctionGraphSurface.tsx:392` |
| `tsconfig.test.json`은 `src/domains/**/*.ts`를 포함하되 `**/ui/**`는 제외한다 → **순수 로직을 `layout.ts`에 두어야 `node --test`가 잡는다** | `desktop/tsconfig.test.json:19-28` |
| 이 slice의 front matter에 `setup:`이 없고 이 worktree에 `desktop/node_modules`가 **없다**(실측: `.package-lock.json` 부재) → stage 안에서 `npm test`/`tsc` 실행 불가 | `.aidev/history/20260820-graph-interact/requirement.md`, glob 확인 |
| 자기 저장소 빌드 테스트가 파싱 실패율 5% 미만을 요구한다 → 새 TS/TSX는 jsparse가 읽는 평범한 문법이어야 한다 | `tests/test_graph.py:612` |

마지막 두 줄이 검증 전략을 결정한다 → §7.

---

## 1. 설계 결정 (이유 포함)

**D1. 노드 = 파일 상자. 행(함수)은 드래그 대상이 아니다.**
행을 상자 밖으로 끌어내면 "파일이 함수를 담는다"는 이 캔버스의 유일한 구조가 무너지고, `anchors`의
`boxIndex` 계약(= memo 계약)도 함께 깨진다. 요구사항의 "노드"는 화면에서 하나의 덩어리로 움직이는 것,
곧 파일 상자다. 상자를 끌면 그 안의 행 전체가 따라간다.

**D2. 드래그는 `layout`을 고쳐 쓰지 않는다. `offsets`(경로→Δ) 한 겹을 위에 얹는다.**
`layoutGraph`의 결과를 매 프레임 다시 만들면 105개 상자 객체와 1435개 anchor가 새 참조가 되고,
`FileBox`의 memo가 전부 깨져 프레임마다 1435행이 리렌더된다 — 지금 이 화면이 살아 있는 이유가 바로
그 memo다. 대신 **기저 layout은 불변**으로 두고, `Map<path, {dx,dy}>` 하나를 따로 든다.

- **상자**: memo 바깥의 래퍼 `<g>`에 `transform="translate(dx dy)"`를 얹는다. **속성 하나**만 바뀌고
  `FileBox`의 props는 참조까지 그대로다 → 자식은 리렌더되지 않는다.
- **엣지**: 좌표를 계산할 때만 offset을 더한다(`shiftAnchor`). 파일 링크 240개 + 선택 노드의 엣지만
  다시 계산되므로, 이것이 "연결된 엣지 실시간 추종"의 전부이자 프레임 비용의 전부다.

**D3. 위치는 `layout`이 바뀌면 사라진다.** `useEffect(..., [layout])`에서 `offsets`를 비운다.
`layout`은 `files`에서만 파생되고 `files`는 인덱스에서만 오므로, **rebuild·새로고침 = 기본 배치 복귀**가
저장 코드 없이 정의상 성립한다. 요구사항의 "영구 저장은 범위 밖"을 코드로 지키는 가장 짧은 길이다.

**D4. 클릭과 드래그는 "움직인 거리"로 가른다(`DRAG_SLOP = 3` client px).**
3px 안에서 뗐으면 클릭 → 기존 선택 동작 그대로. 넘었으면 드래그로 승격하고, **뒤따르는 `click`을
캡처 단계에서 삼킨다**. 손을 뗀 자리가 빈 캔버스라도 선택이 풀리지 않아야 하므로 배경 `<rect>`의
`onClick`에도 같은 플래그를 건다(§5.3).

**D5. 포인터 캡처를 쓴다(`setPointerCapture`), window 리스너가 아니라.**
캡처하면 `pointermove/up`이 상자 `<g>`로 되돌아오고, 호환 마우스 이벤트도 함께 재타게팅되어
**click의 타깃이 그 `<g>` 안**이 된다 — D4의 "click 삼키기"가 한 곳에서 끝난다. window 리스너였다면
등록/해제 effect와 별도의 click 억제 경로가 둘 다 필요하다.

**D6. 좌측은 완전히 접고(0px), 우측은 32px 남긴다 — 비대칭은 의도다.**
요구사항이 "plan에서 기존 레이아웃 구조 보고 결정"이라고 했다. 좌측에는 **`ActivityBar`가 이미 항상
보이는 아이콘 바**다(§0). 그 안에 또 아이콘 바를 만드는 것은 같은 줄을 두 번 그리는 일이므로 좌측은
`collapsedSize={0}`로 완전히 접고, 다시 펴는 길은 ActivityBar가 맡는다(현재 activity를 다시 누르면 토글).
우측에는 그런 바가 없으니 `collapsedSize={32}`로 **접기 셰브런이 살아남을 만큼만** 남긴다.

**D7. 접힘 여부의 진실은 `panelRef.isCollapsed()`이고 React state가 아니다.**
사용자가 버튼이 아니라 separator를 끌어서 접을 수도 있다. 토글·자동 펴기는 전부 ref에게 물어보고
결정하므로 미러가 어긋나도 **동작은 틀리지 않는다**. state는 셰브런 방향 하나에만 쓴다(§6.3, R3).

**D8. 우측 인스펙터는 둘 다 접힌다 — 셸 `Inspector`와 surface 안의 `FunctionDetail`.**
요구사항이 말하는 "우측 패널(인스펙터)"은 셸의 오른쪽 Panel이 맞다. 다만 Function graph 탭에서
캔버스 오른쪽에는 `w-80` `FunctionDetail`이 **하나 더** 있고, "접힘 시 캔버스 확장"과 "노드 선택 시
자동 펴짐"이 실제로 의미를 갖는 쪽은 이것이다(셸 Inspector는 함수 노드에 대해 아무것도 모른다).
그래서 자동 펴짐은 각자의 선택을 본다 — 셸 Inspector는 `selectedSymbolId`, `FunctionDetail`은 `selected`.
**선택했는데 정보가 안 보이는 상황**이 어느 쪽에서도 생기지 않는다.

**D9. 새 의존성 0개, 새 색 토큰 0개, 줌·팬·자동 재배치 변경 0개.** 요구사항의 "하지 않는 것" 그대로.

---

## 2. `desktop/src/domains/graph-view/layout.ts` (수정) — 새 순수 로직은 전부 여기

`ui/`는 `node --test`가 보지 않는다(§0). 그래서 **판단과 산수는 한 줄도 컴포넌트에 두지 않는다.**

```ts
/** 클릭이 드래그가 되는 문턱 (client px). 이보다 짧게 움직이면 그것은 클릭이다. */
export const DRAG_SLOP = 3

/** 한 상자가 배치된 자리에서 얼마나 끌려왔는가. */
export interface Offset {
  dx: number
  dy: number
}

/** path -> offset. 키가 없으면 "layout이 놓은 자리 그대로"라는 뜻이다. */
export type Offsets = ReadonlyMap<string, Offset>

/** 아무것도 끌지 않은 상태. 참조가 하나뿐이라 memo가 헛돌지 않는다. */
export const NO_OFFSETS: Offsets = new Map()

/** 움직인 거리가 클릭이 아니라 드래그인가. */
export function isDrag(dx: number, dy: number, slop?: number): boolean

/** 그 파일의 offset, 없으면 원점. 공유 상수를 돌려주므로 할당이 없다. */
export function offsetOf(offsets: Offsets, path: string): Offset

/** 한 경로만 옮긴 새 Map. 원본은 건드리지 않는다. */
export function withOffset(offsets: Offsets, path: string, offset: Offset): Offsets

/** 상자를 캔버스 왼쪽·위 밖으로는 내보내지 않는다 — 나간 것은 잘려서 사라진다. */
export function clampOffset(box: GraphBox, offset: Offset): Offset

/** 끌려간 만큼 옮긴 anchor. 엣지가 노드를 따라가는 것은 이 한 줄이다. */
export function shiftAnchor(anchor: EdgeAnchor, offset: Offset): EdgeAnchor

/** boxIndex로 offset을 찾는 길. anchor는 path를 모르고 boxIndex만 안다. */
export function offsetsByBox(layout: GraphLayout, offsets: Offsets): Map<number, Offset>

/** 끌려나간 상자까지 담는 캔버스 크기. viewBox 밖은 잘리므로 필요하다. */
export function canvasSize(layout: GraphLayout, offsets: Offsets): { width: number; height: number }
```

규칙:

- `isDrag(dx, dy, slop = DRAG_SLOP)` = `Math.abs(dx) > slop || Math.abs(dy) > slop`.
- `offsetOf`: 없으면 모듈 상수 `ZERO: Offset = { dx: 0, dy: 0 }`를 그대로 반환(프레임마다 객체를 만들지 않는다).
- `withOffset`: `new Map(offsets)` 후 `set`. `dx === 0 && dy === 0`이어도 키를 지우지 않는다 — 지웠다 넣었다
  하는 것보다 그대로 두는 편이 예측 가능하다.
- `clampOffset`: `dx = Math.max(-box.x, offset.dx)`, `dy = Math.max(-box.y, offset.dy)`.
  오른쪽·아래는 제한하지 않는다 — 그쪽은 `canvasSize`가 캔버스를 늘려 받아준다("자유 이동").
- `offsetsByBox`: `offsets`의 키만 훑어 `layout.boxOf.get(path)`로 접는다. **끌린 상자 수만큼**만 크기를 갖는다.
- `canvasSize`: 아무것도 안 끌었으면 `{ layout.width, layout.height }`를 그대로. 끌었으면 끌린 상자에 대해서만
  `x + dx + width`, `y + dy + height`의 최댓값을 기존 값과 비교한다(105개 전부를 훑지 않는다).
- `fileLines`에 네 번째 인자를 더한다:
  `fileLines(edges, layout, limit = MAX_FILE_EDGES, offsets: Offsets = NO_OFFSETS)`.
  `d` 계산이 `edgePath(boxAnchor(from), boxAnchor(to))` → `edgePath(shiftAnchor(boxAnchor(from), offsetOf(offsets, edge.from)), shiftAnchor(...))`가 된다.
  **기본값이 있으므로 기존 호출·기존 테스트(`fileLines(edges, layout, 2)`)는 글자 하나 바뀌지 않는다.**
- `edgePath`, `boxAnchor`, `layoutGraph`, marks 계열은 **변경 없음**.

---

## 3. `desktop/src/domains/graph-view/ui/FunctionGraphSurface.tsx` (수정) — 드래그

### 3.1 상태와 파생

```ts
const [offsets, setOffsets] = useState<Offsets>(NO_OFFSETS)
// 진행 중인 드래그. state가 아닌 이유: 프레임마다 리렌더를 한 번 더 부를 이유가 없다.
const drag = useRef<{
  path: string
  pointerId: number
  box: GraphBox
  startX: number
  startY: number
  base: Offset
} | null>(null)
// 방금 끝난 포인터 시퀀스가 드래그였는가 → 뒤따르는 click을 삼킬지의 근거.
const dragged = useRef(false)
const offsetsRef = useRef(offsets)
offsetsRef.current = offsets

const byBox = useMemo(() => offsetsByBox(layout, offsets), [layout, offsets])
const size  = useMemo(() => canvasSize(layout, offsets), [layout, offsets])
const links = useMemo(() => fileLines(fileEdges, layout, MAX_FILE_EDGES, offsets),
                      [fileEdges, layout, offsets])
const edges = useMemo(() => edgesFor(detail, layout.anchors, byBox), [detail, layout, byBox])
```

`layout` 자체는 `offsets`에 의존하지 **않는다**(D2). 그 덕에 `marks` / `focusMarks` / `hoverMarks` /
`focusBoxes` / `hotBoxes` memo는 드래그 중 한 번도 다시 돌지 않고, "선택을 화면에 스크롤" effect
(`[selected, layout, zoom]`)도 드래그 때문에 다시 실행되지 않는다. 그 effect 안에서만 위치 보정을 위해
`offsetsRef.current`를 읽는다(의존성은 그대로 두어야 매 프레임 스크롤이 튀지 않는다).

기본 배치 복귀(D3):

```ts
useEffect(() => {
  setOffsets((prev) => (prev.size === 0 ? prev : NO_OFFSETS))
}, [layout])
```

### 3.2 `edgesFor` 시그니처

`edgesFor(detail, anchors, byBox)` — 양 끝 anchor를 `shiftAnchor(anchor, byBox.get(anchor.boxIndex) ?? ZERO)`로
옮긴 뒤 지금처럼 `edgePath`에 넘긴다. 그 외 로직(자기 재귀 제외, 중복 제거, `faint`)은 그대로다.

### 3.3 핸들러 (상자 래퍼 `<g>`에, 이벤트 위임 그대로)

| 이벤트 | 하는 일 |
|---|---|
| `<svg onPointerDownCapture>` | `dragged.current = false` — 새 포인터 시퀀스가 시작될 때 플래그를 **캡처 단계에서** 먼저 지운다. 상자든 빈 캔버스든 똑같이 초기화된다. |
| `<g onPointerDown>` | `event.button !== 0`이면 무시. `event.preventDefault()`(네이티브 드래그·포커스 부작용 차단), `event.currentTarget.setPointerCapture(event.pointerId)`, `drag.current = { path: box.path, box, pointerId, startX: event.clientX, startY: event.clientY, base: offsetOf(offsets, box.path) }` |
| `<g onPointerMove>` | `drag.current`가 없거나 `pointerId`가 다르면 무시. `mx = event.clientX - startX`, `my = event.clientY - startY`. `!dragged.current && !isDrag(mx, my)`면 아직 클릭이므로 반환. 아니면 `dragged.current = true`, `setOffsets((prev) => withOffset(prev, path, clampOffset(box, { dx: base.dx + mx / z, dy: base.dy + my / z })))` — `z = zoom || 1`(1 user unit = `zoom` px, §0) |
| `<g onPointerUp>` / `onPointerCancel` | `releasePointerCapture`, `drag.current = null`. `dragged.current`는 **그대로 둔다** — 바로 뒤의 click이 그것을 보고 스스로를 취소한다. |
| `<g onClickCapture>` | `if (dragged.current) event.stopPropagation()` — 캡처 단계이므로 행의 `onClick`(선택)에 닿기 전에 끊긴다. |
| 배경 `<rect onClick>` | `if (dragged.current) return` 후 기존 `onSelect(null)`. D5대로면 여기까지 오지 않지만, 오면 선택이 풀리는 사고라 방어한다. |
| `enter(...)` (hover) | 맨 앞에 `if (drag.current) return` — 끄는 동안 hover 상태가 요동치지 않는다. |

`<g>`에는 `className="cursor-grab"`, `style={{ touchAction: 'none' }}`. `<svg>`에는 `select-none`
(전역 `user-select: none`의 명시적 재확인 — 이 캔버스는 읽는 그림이지 고르는 텍스트가 아니다).

### 3.4 렌더 트리 (바뀌는 곳만)

```
<svg width={size.width * zoom} height={size.height * zoom}
     viewBox={`0 0 ${max(1,size.width)} ${max(1,size.height)}`}
     className="block select-none" onPointerDownCapture={clearDragFlag}>
  ...
  <rect width={...size...} onClick={...가드...} />
  <FileLinks lines={links.lines} ... />        {/* offsets가 이미 d에 녹아 있다 */}
  <g>{edges.map(...)}</g>                      {/* byBox가 이미 d에 녹아 있다 */}

  {layout.boxes.map((box, i) => {
    const off = offsets.get(box.path)
    return (
      <g key={box.path}
         transform={off ? `translate(${off.dx} ${off.dy})` : undefined}
         opacity={...기존...} className="cursor-grab"
         style={{ transition: 'opacity 90ms linear', touchAction: 'none' }}
         onPointerDown={...} onPointerMove={...} onPointerUp={...}
         onPointerCancel={...} onClickCapture={...}
         onMouseOver={...기존...} onMouseLeave={leave}>
        <FileBox box={box} ... />              {/* props 참조 불변 → memo 유지 */}
      </g>
    )
  })}
</svg>
```

**`FileBox`는 한 글자도 바뀌지 않는다.** 드래그가 memo 계약 밖에서 끝난다는 것이 이 설계의 요점이다.

`transition: 'opacity 90ms linear'`는 `opacity`만 지정하므로 `transform`은 즉시 따라간다(끌리는 상자가
90ms 뒤처지지 않는다).

### 3.5 프레임당 비용

`setOffsets` 한 번 → surface 리렌더. 다시 계산되는 것: `byBox`(끌린 상자 수), `size`(같음),
`links`(파일 쌍 정렬 1회 + 최대 240개 베지어 문자열), `edges`(선택 노드의 엣지, 없으면 0개),
래퍼 `<g>` 105개의 속성. **1435행은 리렌더되지 않는다.** 240개 문자열 조립은 프레임 예산 안이다.

### 3.6 surface 안 인스펙터(`FunctionDetail`) 접기 — D8

```ts
const [detailOpen, setDetailOpen] = useState(true)
useEffect(() => {
  if (selected !== null) setDetailOpen(true)   // 선택했는데 정보가 안 보이는 상황 방지
}, [selected])
```

- 펼침: 지금의 `<div className="w-80 shrink-0 border-l border-line bg-panel">` 위에 높이 `h-7`짜리 줄을 얹고,
  왼쪽에 셰브런 버튼(`Icon name="chevron"`, 접기), 그 옆에 `panel-label`로 `Node`.
- 접힘: `w-7`짜리 세로 막대 버튼 하나(`border-l border-line bg-panel`, `Icon name="chevron"` `rotate-180`,
  `title="Show node detail"`, `aria-expanded={false}`). 캔버스는 `flex-1`이라 남은 폭을 **그대로 차지한다**.
- `FunctionDetail.tsx`는 수정하지 않는다(감싸기만 한다).

---

## 4. `desktop/src/renderer/src/components/primitives.tsx` (수정, 작다)

`PanelHeader`에 선택적 `left?: ReactNode` 슬롯을 하나 더한다. 제목 앞에 오고, 없으면 지금과 완전히 같은
DOM이다. **접힌 32px 우측 패널에서 마지막까지 살아남는 것이 이 슬롯**이기 때문에 오른쪽이 아니라 왼쪽이다
(제목은 `truncate`로 사라지고 셰브런만 남는다 → 드래그로 접혔더라도 다시 펼 수 있다, R3).

```tsx
export function PanelHeader({ title, left, right, className = '' }: {...}): JSX.Element
// <div ...> {left} <span className="panel-label truncate">{title}</span> {right} </div>
```

---

## 5. `desktop/src/renderer/src/features/sidebar/Sidebar.tsx` (수정)

`onCollapse?: () => void` prop을 하나 받아, `PanelHeader`의 `right` 슬롯에 접기 버튼을 기존 `search`
아이콘 **앞**에 넣는다.

```tsx
<button type="button" onClick={onCollapse} title="Collapse panel" aria-label="Collapse panel"
        className="text-fg-mute hover:text-fg-dim">
  <Icon name="chevron" size={13} className="rotate-180" />
</button>
```

그 외 본문(`WorkspacesView`·`FunctionGraphSidebar`·나머지 뷰)은 **변경 없음** — 어떤 activity가 열려 있든
같은 패널이 접히므로 "워크스페이스/파일 패널"은 이 한 버튼으로 전부 덮인다.

---

## 6. `desktop/src/renderer/src/features/inspector/Inspector.tsx` + `App.tsx` (수정)

### 6.1 Inspector

`collapsed: boolean`, `onToggle: () => void`를 받아 `PanelHeader`의 새 `left` 슬롯에 셰브런을 놓는다
(`rotate-180`이면 접기, 그대로면 펴기). `collapsed`이면 본문(`min-h-0 flex-1 …`)을 렌더하지 않는다 —
32px 안에서 mock 상세가 눌려 있는 그림을 만들지 않기 위해서다. 나머지는 그대로.

### 6.2 App.tsx — 패널

```tsx
const sidebarRef = usePanelRef()
const inspectorRef = usePanelRef()
const [inspectorCollapsed, setInspectorCollapsed] = useState(false)
```

| 위치 | 변경 |
|---|---|
| 좌측 `<Panel>` (`:189`) | `panelRef={sidebarRef} collapsible collapsedSize={0}` 추가. `defaultSize/minSize/maxSize/groupResizeBehavior`는 그대로 |
| 우측 `<Panel>` (`:287`) | `panelRef={inspectorRef} collapsible collapsedSize={32}` 추가. 나머지 그대로 |
| `Sidebar` | `onCollapse={() => sidebarRef.current?.collapse()}` |
| `Inspector` | `collapsed={inspectorCollapsed}` `onToggle={toggleInspector}` |

```ts
/** 접힘의 진실은 패널 자신이 안다 — 버튼으로 접었든 separator를 끌어 접었든. */
function toggleInspector(): void {
  const panel = inspectorRef.current
  if (!panel) return
  const collapsed = panel.isCollapsed()
  if (collapsed) panel.expand()
  else panel.collapse()
  setInspectorCollapsed(!collapsed)
}
```

`toggleBottom`(`:169`)과 같은 모양이다 — 이 앱에 이미 있는 관용구를 그대로 쓴다.

### 6.3 App.tsx — 다시 펴기와 자동 펴기

**좌측을 다시 펴는 길**은 `ActivityBar`다(D6). `ActivityBar.tsx`는 **수정하지 않는다** — 판단은 App에 둔다.

```ts
function handleActivity(id: ActivityId): void {
  const panel = sidebarRef.current
  // 지금 열려 있는 뷰를 다시 누르면 토글, 다른 뷰를 누르면 반드시 펴진다.
  if (id === activity && panel && !panel.isCollapsed()) panel.collapse()
  else panel?.expand()
  setActivity(id)
  const surface = ACTIVITY_SURFACE[id]
  if (surface) setPrimary(surface)
}
```

**우측 자동 펴기**(요구사항 3, mock 그래프 쪽):

```ts
// 노드를 골랐는데 인스펙터가 접혀 있으면, 고른 것에 대해 아무 말도 못 하게 된다.
useEffect(() => {
  if (selectedSymbolId === null) return
  const panel = inspectorRef.current
  if (panel?.isCollapsed()) {
    panel.expand()
    setInspectorCollapsed(false)
  }
}, [selectedSymbolId])
```

Function graph 노드의 자동 펴기는 §3.6이 담당한다(D8).

---

## 7. 검증

### 7.1 stage 안에서 실제로 돌아가는 것

- **`python -m pytest`** (repo 루트). Done Criteria의 "기존 pytest 전량 통과"이며, 이 slice가
  `aidev/**`를 건드리지 않았다는 증거다. 변경 파일이 전부 `desktop/`이므로 **통과가 기본값**이다.
- **`npm test`는 이 worktree에서 돌릴 수 없다** — front matter에 `setup:`이 없어 `desktop/node_modules`가
  없다(§0 실측). 직전 slice와 같은 상황이다. test stage는 **"실행 불가"로 정직하게 보고**하고
  PASS/FAIL은 pytest로 판정한다. 억지로 통과시키려 하지 말 것.
- 부수 확인: `tests/test_graph.py:612`가 이 저장소 자신을 파싱하며 실패율 5% 미만을 요구한다.
  새로 쓰는 코드는 기존 파일들의 문법 범위 안에서만 — 중첩 템플릿 리터럴, 특이한 제네릭, JSX 안의
  정규식 리터럴 금지.

### 7.2 이 slice가 추가하는 단위 테스트 — `desktop/src/domains/graph-view/layout.test.ts`

기존 테스트는 **한 줄도 고치지 않는다**(§2의 기본 인자 덕분에 `fileLines` 호출부도 그대로다).
새 `describe` 블록만 붙는다.

| 테스트 | 못 박는 것 |
|---|---|
| `isDrag` | `DRAG_SLOP` 이내는 클릭, 어느 축으로든 넘으면 드래그, 음수 방향도 같다 |
| `offsetOf` | 키가 없으면 `{0,0}`이고, **호출할 때마다 같은 참조**다(프레임마다 할당하지 않는다) |
| `withOffset` | 새 Map을 돌려주고 원본은 그대로 · 같은 경로를 두 번 옮기면 마지막 값만 남는다 |
| `clampOffset` | 왼쪽·위로는 상자를 캔버스 밖(`x+dx < 0`)으로 못 내보낸다 · 오른쪽·아래로는 제한이 없다 |
| `shiftAnchor` | `left/right/y`가 정확히 Δ만큼 이동하고, `{0,0}`이면 값이 같다 |
| `offsetsByBox` | 끌린 상자만 키가 된다 · 상자가 없는 경로는 조용히 빠진다 |
| `canvasSize` | 아무것도 안 끌면 `layout.width/height` 그대로 · 오른쪽/아래로 끌면 그만큼 커진다 · 왼쪽으로 끌어도 줄어들지 않는다 |
| **`fileLines` + offsets** | **끌린 상자에서 나가는 링크의 `d`가 옮겨진 오른쪽 변에서 시작한다** = "엣지가 노드를 따라간다"의 단위 증명 · offsets를 안 주면 기존 결과와 **바이트까지 같다** |
| 규모 | `bigRepo(105, 1435)`에서 상자 하나를 끌었을 때 `offsetsByBox`·`canvasSize`가 **끌린 것 수에만 비례**하는 크기의 결과를 낸다(105개를 훑지 않는다는 계약) |

### 7.3 Done Criteria 실물 검증 (사람이, 이 저장소를 열고)

1. `cd desktop && npm ci && npm run dev` → 이 repo를 연다 → **Function graph** 탭.
2. **드래그**: 아무 파일 상자를 잡아 끈다 → 상자가 커서를 따라오고, 그 파일의 집계 곡선들이 **끊김 없이
   같이 움직인다**. 함수를 하나 선택한 뒤 관련 상자를 끌면 개별 엣지(파랑 실선/초록 파선)도 따라온다.
   끄는 동안 글자가 선택되지 않는다. 캔버스 오른쪽·아래로 끌어도 상자가 잘리지 않는다(스크롤 영역이 자란다).
3. **클릭과의 구분**: 행을 그냥 클릭 → 선택된다. 행을 잡고 끈 뒤 놓으면 → **선택이 바뀌지도, 풀리지도 않는다**.
   같은 행을 다시 클릭 → 선택 해제(기존 토글 그대로). `Escape`·빈 캔버스 클릭도 그대로.
4. **줌 60%/180%**에서 끌어도 커서와 상자가 어긋나지 않는다.
5. **복귀**: [Rebuild] 또는 창 새로고침 → 모든 상자가 기본 배치로 돌아온다(저장되지 않았다는 증거).
6. **좌측 접기**: 사이드바 헤더의 셰브런 → 패널이 사라지고 캔버스가 그 폭을 가져간다. ActivityBar의 현재
   아이콘을 다시 누르면 펴지고, 다른 아이콘을 누르면 펴지면서 그 뷰로 바뀐다.
7. **우측 접기**: Inspector 헤더의 셰브런 → 32px 막대만 남고 캔버스가 넓어진다. **Graph(demo) 탭에서
   노드를 하나 고르면 접혀 있던 Inspector가 스스로 펴진다.**
8. **Function graph의 노드 패널**: 캔버스 오른쪽 `Node` 패널의 셰브런으로 접으면 캔버스가 넓어지고,
   접힌 채로 함수를 하나 고르면 **자동으로 펴지면서 그 함수의 명세가 보인다.**
9. 1435행 위를 훑는 hover, `say`(callers 150) 선택 — 드래그를 넣기 전과 같은 반응 속도.

---

## 8. 위험과 대비

**R1. 드래그 종료 click이 선택을 건드린다.** 가장 그럴듯한 실패다. 방어가 셋 겹이다 —
`<svg onPointerDownCapture>`로 매 시퀀스마다 플래그 초기화, 상자 `<g>`의 `onClickCapture` 차단,
배경 `<rect onClick>`의 가드(§3.3). 셋 중 하나만 남아도 선택은 안전하다.

**R2. `setPointerCapture`가 SVG `<g>`에서 기대대로 안 붙는다.** Chromium(Electron)에서는 `Element` API라
동작한다. 그래도 어긋나면 대안은 **`drag.current`가 살아 있는 동안 `window`에 `pointermove/pointerup`을
거는 것** — 로직(§3.3의 표)은 그대로이고 리스너의 집만 바뀐다. 다만 그 경우 click 타깃이 `<g>` 밖일 수
있으므로 R1의 배경 rect 가드가 **필수**가 된다(그래서 처음부터 넣어 둔다).

**R3. 사용자가 버튼이 아니라 separator를 끌어 패널을 접으면 React state가 어긋난다.**
동작은 어긋나지 않는다 — 토글·자동 펴기는 전부 `panelRef.isCollapsed()`에게 묻는다(D7). 어긋나는 것은
셰브런 방향뿐이고, 그마저 다음 토글 한 번에 스스로 맞는다. 좌측은 `collapsedSize={0}`이라 접힌 상태에
헤더 자체가 보이지 않으므로 어긋날 표시가 없다. 우측은 셰브런을 헤더 **왼쪽**에 두어(§4) 32px에서도
반드시 눌러 펼 수 있다.

**R4. `react-resizable-panels`의 collapse API를 이 worktree에서 확인할 수 없다**(node_modules 부재).
완화: 새 API를 쓰지 않는다. `usePanelRef` + `panelRef` + `collapsible` + `collapsedSize` +
`isCollapsed()/collapse()/expand()`는 **BottomPanel이 이미 쓰고 있는 것들 뿐**이다(`App.tsx:54, 169-179, 263-270`).
`onCollapse`/`onExpand` 같은 미확인 prop은 쓰지 않는다.

**R5. 상자를 다른 상자 위에 겹쳐 놓아 캔버스가 엉킨다.** 세션 임시 상태이므로 [Rebuild]나 새로고침이
원상 복구다(D3). 그 길이 무겁다는 판단이 서면, FreshnessBar에 `offsets.size > 0`일 때만 나타나는
`reset positions` 버튼 하나(`setOffsets(NO_OFFSETS)`)를 둔다 — 6줄이고 상태도 늘지 않는다. **이 계획은
그 버튼을 포함한다**(끌어놓은 캔버스에서 빠져나올 길은 눈에 보여야 한다).

**R6. 드래그 프레임에 240개 파일 링크를 다시 만드는 비용.** 문자열 조립 240회 + 파일 쌍 정렬 1회다.
그래도 무거우면 다음 단계는 `fileLines`를 "고르기(정렬·상한)"와 "놓기(`d` 계산)"로 쪼개 고르기만
`[fileEdges, layout]`에 memo하는 것 — 순수 함수 안의 분해라 컴포넌트는 그대로다.

**R7. 접힌 좌측 패널을 다시 펴는 길을 사용자가 못 찾는다.** ActivityBar가 항상 보이고 아이콘을 누르면
반드시 펴진다(§6.3). separator를 끄는 두 번째 길도 남아 있다.

**R8. stage 안에서 TS가 컴파일되지 않아 타입 오류가 늦게 드러난다.** 완화: 이 계획이 새 시그니처를 글자
그대로 적었고, 제네릭·조건부 타입을 쓰지 않는다. 새 prop 셋(`PanelHeader.left`, `Sidebar.onCollapse`,
`Inspector.collapsed/onToggle`)은 전부 **선택적이거나 호출 지점이 한 곳**이라 누락이 있으면 `npm test`
첫 줄에서 전부 드러난다.

---

## 9. 하지 않는 것 (요구사항 그대로)

노드 위치의 영구 저장 / 자동 재배치(force·물리 시뮬레이션) / 줌·팬 동작 변경 / 새 npm 의존성 /
새 IPC 채널·새 타입 / `aidev/**` 수정 / 데모 `GraphSurface`(mock, `[DEMO]`)의 드래그 /
행(함수) 단위 드래그 / 90ms를 넘는 전환·애니메이션.
