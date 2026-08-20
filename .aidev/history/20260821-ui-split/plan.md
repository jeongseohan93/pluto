# UI 정리 — 탭 정돈 + 그래프/코드 스플릿 뷰 — 구현 계획

## 0. 먼저 확인한 사실 (이 워크트리 실측)

| 확인 대상 | 결과 |
|---|---|
| `tasks/ui-split.md` front matter | `approval: plan`, `max_turns: implement=140`, `test_commands: npx --prefix desktop tsc --noEmit -p desktop`. `setup:` 없음 |
| `desktop/node_modules` | **없다** (`.package-lock.json` 부재). `desktop/tsconfig.json`은 `{"files": [], "references": [...]}` 솔루션 파일 — `--build` 없이 `-p desktop`은 0개 파일을 검사하고 통과한다. **선언된 test_command는 이 워크트리에서 아무것도 증명하지 못한다** |
| 탭 정의 자리 | `SurfacePane.tsx:65-106` — `plan`, `functions`, `graph[DEMO]`, `code[DEMO]`, `test[DEMO]`, `diff[DEMO]`, `browser[DEMO]` |
| `SurfaceId` | `SurfacePane.tsx:17` — 7종 유니온. `App.tsx:47-48`이 기본값으로 `'graph'`(mock)와 `'diff'`(mock)를 쓴다 |
| 데모 서피스 파일 | `features/graph|code|tests|diff|browser/*.tsx` 5개. **다른 어디서도 import 되지 않는다** (`SurfacePane`이 유일한 진입) → 라우팅만 끊으면 파일은 그대로 남는다 |
| 액티비티 → 서피스 매핑 | `App.tsx:25-31` `ACTIVITY_SURFACE` = `{pipeline:'plan', functions:'functions', graph:'graph', changes:'diff', tests:'test'}` |
| 코드 뷰어의 현재 자리 | `FunctionGraphSurface.tsx:1270-1274` — `w-[34rem] shrink-0` 고정폭. 캔버스(`flex-1`)와 Node 패널(`w-80`) 사이의 flex 형제다. **덮고 있지는 않지만 폭이 고정이고 줄일 수도 늘릴 수도 없다** |
| 코드 뷰어 상태 | `codeTarget`/`codeOpen` 두 개 (`:247-248`). 더블클릭 = `openRow`(`:702-716`)가 둘 다 세우고, [코드 보기] = `openCode`(`:688-691`), 호출자 행 = `reveal`(`:679-681`)은 좌표만 옮긴다 |
| 닫기 | `CodeViewer` 헤더의 셰브런 → `onClose` → `setCodeOpen(false)` (`CodeViewer.tsx:231-240`) — **이미 있다** |
| 인스펙터(우측 사이드) | `App.tsx:342-360`의 `<Panel panelRef={inspectorRef} collapsible collapsedSize={32}>` + `toggleInspector`(`:211-218`). 접기/펴기 API(`isCollapsed/collapse/expand`)는 이 저장소에서 이미 검증된 것뿐이다 |
| Monaco 리사이즈 | `monaco.ts:56` `automaticLayout: true` — `ResizeObserver`로 컨테이너 폭 변화를 스스로 따라간다. 드래그 리사이즈에 추가 코드가 필요 없다 |
| 명세 게이트 | `aidev/specs.py:165` `paths=("*.py",)` — **파이썬 전용**. 이 slice는 `.py`를 건드리지 않으므로 게이트는 빈 채로 통과한다 |
| pytest의 desktop 의존 | `tests/test_graph.py:629-637` 단 하나 — `lang='ts' AND path LIKE 'desktop/src/%'`가 20개 초과인지. 파일을 하나 **추가**하므로 영향 없다 |
| 테스트 인프라 | `npm test` = typecheck + `tsc -p tsconfig.test.json` + `node --test out-test/**`. `tsconfig.test.json`은 `src/domains/**/*.ts`를 넣고 **`src/domains/**/ui/**`를 뺀다** → `ui/` 밖의 순수 모듈만 실제로 테스트할 수 있다 |

**이 계획이 서 있는 전제(정직하게):** 이 워크트리에서는 `tsc`도 `node --test`도 돌릴 수 없다. 그래서 (a) **새 라이브러리도 새 API도 쓰지 않는다** — `react-resizable-panels`의 `onResize`/`expand()`의 크기 복원 동작은 v4에서 확인할 수 없으므로 **의존하지 않는다**, (b) 계산은 전부 `ui/` 밖의 순수 함수로 밀어 넣어 나중에 `node --test`가 진짜로 검증하게 한다.

---

## 1. 설계 요지

### 1.1 "스플릿"은 이미 절반쯤 있다 — 없는 것은 경계와 기억이다

코드 뷰어는 지금도 캔버스를 덮지 않는다(flex 형제다). 요구사항이 실제로 요구하는 것은 세 가지다: **경계를 끌 수 있을 것**, **비율이 세션 안에서 남을 것**, **자리가 나도록 인스펙터가 접힐 것**. 그러므로 이 slice는 코드 뷰어를 옮기거나 다시 만들지 않는다. 고정폭 `w-[34rem]`을 **비율 + 드래그 핸들**로 바꾸고, 그 비율을 App까지 올린다.

### 1.2 비율은 왜 App에 사는가

`SurfacePane`은 탭이 바뀌면 `FunctionGraphSurface`를 언마운트한다. 비율을 화면 안에 두면 Plan 탭에 다녀오는 것만으로 사라진다 — "세션 내 기억"이 아니다. `zoom`이 이미 같은 이유로 App에 올라가 있으므로(`App.tsx:52`, `SurfacePane`을 거쳐 내려간다) **같은 자리, 같은 패턴**을 쓴다.

`codeOpen`/`codeTarget`은 올리지 **않는다**. 그것들은 그래프 화면의 소유이고(호출자 행이 좌표만 옮기는 `reveal` 규칙이 거기 산다), 요구사항이 기억하라고 한 것은 비율뿐이다.

### 1.3 경계는 손으로 만든다 — 라이브러리를 쓰지 않는 이유

`Group/Panel/Separator`를 화면 안에 한 벌 더 놓으면 드래그는 공짜지만, **닫았다 열 때 비율이 돌아오는지**가 `expand()`의 크기 복원 동작에 달린다. 그것을 이 워크트리에서 확인할 수 없다(`node_modules` 부재). 반면 손으로 만든 핸들은 이 파일이 이미 세 번(박스 드래그·팬·미니맵) 쓰고 있는 포인터 캡처 관용구의 네 번째 사본일 뿐이고, 비율은 우리가 들고 있으므로 열고 닫는 것과 무관하게 남는다. **확인할 수 없는 동작에 Done Criteria를 걸지 않는다.**

핸들은 캔버스의 스크롤 컨테이너 **밖**에 있다. `startPan`(`:854-868`)은 컨테이너 자신의 `pointerdown`이므로 핸들과는 만나지 않는다 — 팬 회귀가 구조적으로 불가능하다.

### 1.4 줄을 하나 더 감싼다 — 그래야 기하가 한 줄로 끝난다

지금은 캔버스·코드·Node 패널이 **한 줄**의 형제다. 비율의 기준이 "줄 전체"면 Node 패널(320px ↔ 28px)이 열리고 닫힐 때마다 기준이 흔들린다. 그래서 **캔버스 + 핸들 + 코드**만 감싸는 줄을 하나 만든다. 그러면 코드 패널의 오른쪽 끝 = 그 줄의 오른쪽 끝이고, 드래그의 산수는 `(row.right - clientX) / row.width` 한 줄이 된다. Node 패널은 그 바깥에 그대로 남는다 — 요구사항의 스플릿은 그래프와 코드 사이의 것이다.

### 1.5 인스펙터는 "나타나는 순간"에만 접힌다

`codeOpen`이 false→true로 바뀔 때만 셸에 알린다. 두 번째 노드를 더블클릭해도 `codeOpen`은 이미 true이므로 아무 일도 일어나지 않는다 — 사람이 다시 펼친 인스펙터를 도로 접지 않는다는 요구사항이, 상태 전이 하나에 의해 보장된다. 되돌리는 코드는 없다: 닫을 때 인스펙터를 자동으로 펴지 **않는다**("수동으로 다시 펼 수 있음"이 요구의 전부다).

접는 대상은 **App의 `Inspector`**(우측 사이드, mock 데이터, 자체 셰브런)다. 그래프 화면 안의 `Node` 패널은 접지 않는다 — 방금 더블클릭한 함수의 명세와 호출자가 코드 바로 옆에 있는 것이 이 화면의 요점이다.

---

## 2. 변경 파일 목록 (수정 4, 신규 2)

| # | 파일 | 성격 |
|---|---|---|
| 2.1 | `desktop/src/domains/code-view/split.ts` | **신규** — 순수 산술 |
| 2.2 | `desktop/src/domains/code-view/split.test.ts` | **신규** — `node --test` |
| 2.3 | `desktop/src/renderer/src/features/workspace/SurfacePane.tsx` | 탭 5종 라우팅 해제, 개명, props |
| 2.4 | `desktop/src/renderer/src/App.tsx` | 기본값, 매핑, 비율 상태, 인스펙터 접기 |
| 2.5 | `desktop/src/domains/graph-view/ui/FunctionGraphSurface.tsx` | 스플릿 줄 + 경계 핸들 |
| 2.6 | `desktop/README.md` | 한 문장(라우팅 사실 갱신) |

> **무변경:** `features/graph|code|tests|diff|browser/*.tsx`(5개 — 파일 삭제 금지), `mock-data.ts`, `DemoBadge.tsx`, `Inspector.tsx`, `ActivityBar.tsx`, `Sidebar.tsx`, `CodeViewer.tsx`, `monaco.ts`, `FunctionDetail.tsx`, `Minimap.tsx`, `layout.ts`, `graph-store.ts`, `preload/`, `main/`, **파이썬 전부**.

---

### 2.1 `desktop/src/domains/code-view/split.ts` (신규)

```ts
/**
 * 그래프와 코드가 한 줄을 나눠 갖는 방식 — 순수 산술로만.
 *
 * `ui/` 밖에 있는 이유는 두 가지다. `tsconfig.test.json`이 `ui/`를 제외하므로
 * 여기가 `node --test`가 실제로 검사할 수 있는 유일한 자리이고, "비율"이라는
 * 하나의 사실을 App(세션 기억)과 그래프 화면(드래그)이 같은 규칙으로 읽어야
 * 하기 때문이다.
 */

/** 코드 패널이 처음 차지하는 몫. 고정폭 34rem이 서 있던 자리의 비율판. */
export const SPLIT_DEFAULT = 0.45

/** 코드 패널의 바닥. 이보다 좁으면 줄 번호와 본문이 같이 서지 못한다. */
export const CODE_MIN_PX = 360

/** 그래프의 바닥. 이보다 좁으면 파일 상자 한 열도 들어가지 않는다. */
export const GRAPH_MIN_PX = 320

/** 화살표 한 칸. 경계는 포인터 없이도 옮길 수 있어야 한다. */
export const SPLIT_STEP = 0.02

/**
 * 요청된 몫을, 양쪽 모두 설 수 있는 몫으로.
 *
 * 줄이 두 바닥의 합보다 좁으면 어느 한쪽을 굶기는 대신 반으로 나눈다.
 * `lo <= 0.5 <= hi`가 정의상 언제나 성립하므로 범위가 뒤집히는 경우는 없다 —
 * 그것이 이 함수에 예외 처리가 없는 이유다.
 *
 * @param fraction  코드 패널이 요청한 몫 (0..1)
 * @param rowWidth  그래프와 코드가 나눠 갖는 줄의 폭, client px. 아직 잰 적이
 *                  없으면 0을 넘겨도 된다 — 그때는 느슨한 범위로만 자른다
 */
export function clampSplit(fraction: number, rowWidth: number): number {
  if (!Number.isFinite(fraction)) return SPLIT_DEFAULT
  if (!Number.isFinite(rowWidth) || rowWidth <= 0) {
    return Math.min(Math.max(fraction, 0.2), 0.8)
  }
  const lo = Math.min(CODE_MIN_PX / rowWidth, 0.5)
  const hi = Math.max(1 - GRAPH_MIN_PX / rowWidth, 0.5)
  return Math.min(Math.max(fraction, lo), hi)
}

/**
 * 포인터가 서 있는 자리를 코드 패널의 몫으로.
 *
 * 코드 패널의 오른쪽 끝은 이 줄의 오른쪽 끝이다(Node 패널은 이 줄 밖에 있다).
 * 그래서 잴 것이 사각형 하나뿐이고, 여기에 클램프가 없는 것은 자르는 자리가
 * `clampSplit` 하나여야 하기 때문이다.
 *
 * @param clientX  포인터의 client x
 * @param row      그래프와 코드가 차지한 줄의 사각형 (DOMRect로 충분하다)
 */
export function splitFrom(clientX: number, row: { right: number; width: number }): number {
  if (!(row.width > 0)) return SPLIT_DEFAULT
  return (row.right - clientX) / row.width
}
```

### 2.2 `desktop/src/domains/code-view/split.test.ts` (신규)

`layout.test.ts`와 같은 형식(`node:test` + `node:assert/strict`). 검사 항목:

| 검사 | 기대 |
|---|---|
| `clampSplit(0.45, 1000)` | `0.45` — 넉넉한 줄에서는 요청 그대로 |
| `clampSplit(0.05, 1000)` | `0.36` = `CODE_MIN_PX/1000` — 코드의 바닥 |
| `clampSplit(0.95, 1000)` | `0.68` = `1 - GRAPH_MIN_PX/1000` — 그래프의 바닥 |
| `clampSplit(x, 500)` (모든 x) | 정확히 `0.5` — 두 바닥이 함께 설 수 없는 줄은 반으로 |
| `clampSplit(NaN, 1000)` | `SPLIT_DEFAULT` |
| `clampSplit(0.9, 0)` | `0.8` — 아직 재지 못한 줄의 느슨한 범위 |
| 모든 `(fraction, rowWidth)` 조합에서 `lo <= hi` | 뒤집힘 없음 (0.1~2000px 격자로) |
| `splitFrom(700, {right: 1000, width: 1000})` | `0.3` |
| `splitFrom`은 왼쪽으로 갈수록 커진다 | 단조성 |
| `clampSplit(splitFrom(x, row), row.width)` | 언제나 `[lo, hi]` 안 (x를 줄 밖까지 밀어도) |
| `SPLIT_DEFAULT`가 1000px 줄의 `[lo,hi]` 안에 있다 | 기본값이 즉시 잘리지 않는다 |

### 2.3 `SurfacePane.tsx` (수정)

**(a) 탭 다섯 개의 라우팅을 끊는다.** 5개 import(`GraphSurface`, `CodeSurface`, `TestSurface`, `DiffSurface`, `BrowserSurface`)와 `DemoBadge` import를 지운다 — 파일은 남고 진입만 사라진다.

```ts
export type SurfaceId = 'plan' | 'functions'
```

`tabs` 배열은 두 개로 줄고, `functions`의 라벨이 **`'Graph'`** 가 된다:

```tsx
const tabs = [
  { id: 'plan' as const, label: 'Plan', badge: /* 기존 waitingCount 배지 그대로 */ },
  // 이제 이 저장소의 그래프는 이것 하나뿐이므로 이름도 그냥 Graph다.
  // 화면 id는 'functions'로 둔다 — 이것이 가리키는 것은 여전히 Function DB이고,
  // 사라진 mock 'graph'의 id를 물려받으면 남은 문자열이 어느 쪽인지 흐려진다.
  { id: 'functions' as const, label: 'Graph' }
]
```

`failed` 계산(`:63`)은 Test 탭과 함께 사라진다.

**(b) `SurfaceData`를 남은 두 화면이 읽는 것만으로 줄인다.** `graph`/`changes`/`tests`/`location`/`selectedSymbolId`/`onSelectSymbol` 필드와 `ChangeSummary`/`CodeGraph`/`TestCase`/`SymbolLocation` import를 지운다. 남는 것: `pipeline`, `functions`, `waitingCount`. (이 값들은 App에서 사라지지 않는다 — 사이드바와 인스펙터가 직접 받는다.)

**(c) 줌 컨트롤 조건**(`:116`): `surface === 'graph' || surface === 'functions'` → `surface === 'functions'`.

**(d) 페인 분할 버튼의 이름만 손본다.** 이제 "split"이라는 말은 그래프/코드 스플릿의 것이므로, 이 버튼은 두 번째 **페인**이라고 말한다(동작·아이콘·`aria-pressed`는 그대로):

```tsx
title={splitOpen ? 'Close the second pane' : 'Open a second pane'}
```

**(e) 새 props 세 개를 받아 `FunctionGraphSurface`에 넘긴다:**

```tsx
  codeSplit: number
  onCodeSplitChange: (fraction: number) => void
  onCodeSplitOpen: () => void
```

```tsx
{surface === 'plan' ? (
  <PlanSurface {...data.pipeline} />
) : (
  <FunctionGraphSurface
    index={data.functions.index}
    loading={data.functions.loading}
    selected={data.functions.selected}
    onSelect={data.functions.onSelect}
    onBuild={data.functions.onBuild}
    busy={data.functions.busy}
    zoom={zoom}
    onZoomChange={onZoomChange}
    split={data ? codeSplit : codeSplit}   // ← 그냥 codeSplit
    onSplitChange={onCodeSplitChange}
    onSplitOpen={onCodeSplitOpen}
  />
)}
```

> 마지막 분기가 `: (` 로 끝나는 것이 중요하다 — `SurfaceId`가 두 값뿐이므로 삼항 하나로 소진된다.

### 2.4 `App.tsx` (수정)

**(a) 기본값과 매핑.** `SurfaceId`가 좁아졌으므로 이 셋은 **고치지 않으면 타입 에러**다:

```ts
const ACTIVITY_SURFACE: Partial<Record<ActivityId, SurfaceId>> = {
  pipeline: 'plan',
  functions: 'functions'
}
// mock 화면들이 라우팅에서 빠지면서 graph/changes/tests 항목도 함께 사라진다.
// 그 액티비티들은 이제 사이드바만 바꾼다 — 열 메인 화면이 없기 때문이다.
```

```ts
const [primary, setPrimary] = useState<SurfaceId>('functions')  // was 'graph'(mock)
const [secondary, setSecondary] = useState<SurfaceId>('plan')   // was 'diff'(mock)
```

**(b) 비율은 세션이 기억한다** (`zoom` 바로 옆):

```ts
import { SPLIT_DEFAULT } from '@domains/code-view/split'
...
// 탭을 옮겨 다녀도 남아야 하므로 화면이 아니라 셸이 들고 있다 — zoom과 같은
// 이유, 같은 자리. 디스크에는 쓰지 않는다: 요구는 "세션 내"까지다.
const [codeSplit, setCodeSplit] = useState(SPLIT_DEFAULT)
```

**(c) 자리 내주기** (`toggleInspector` 옆):

```tsx
/**
 * 그래프 옆에 코드가 나타났다 — 인스펙터를 접어 자리를 낸다.
 *
 * 나타나는 *순간*에만 불린다(화면 쪽 effect가 `codeOpen`의 false→true에서만
 * 부른다). 그래서 두 번째 노드를 더블클릭해도 사람이 도로 펼쳐 둔 인스펙터를
 * 다시 접지 않는다. 되돌리는 경로는 없다 — 다시 펴는 것은 사람 몫이다.
 *
 * 이미 접혀 있으면 아무것도 하지 않는다: 접힘의 진실은 패널 자신이 안다.
 */
const makeRoomForCode = useCallback((): void => {
  const panel = inspectorRef.current
  if (!panel || panel.isCollapsed()) return
  panel.collapse()
  setInspectorCollapsed(true)
}, [inspectorRef])
```

**(d) `surfaceData`에서 삭제된 필드 제거** — `graph`/`changes`/`tests`/`location`/`selectedSymbolId`/`onSelectSymbol` 여섯 줄. `location`·`workspace.graph` 등은 `Sidebar`/`Inspector`/`StatusBar`가 계속 쓰므로 App에서는 그대로 남는다.

**(e) 두 `SurfacePane` 모두에 새 props를 넘긴다** (primary·secondary 동일 — `zoom`이 이미 공유되는 것과 같다):

```tsx
codeSplit={codeSplit}
onCodeSplitChange={setCodeSplit}
onCodeSplitOpen={makeRoomForCode}
```

> `App.tsx:91-97`의 "선택하면 인스펙터가 스스로 돌아온다" effect는 **건드리지 않는다**. 그것은 `selectedSymbolId`(mock 심볼)가 트리거이고, 함수 그래프의 더블클릭은 `functionId`를 움직이므로 두 경로는 만나지 않는다.

### 2.5 `FunctionGraphSurface.tsx` (수정)

**(a) import 세 개 추가** (`layout`에서 오는 것들 아래):

```ts
import { SPLIT_STEP, clampSplit, splitFrom } from '@domains/code-view/split'
```

**(b) props 세 개 추가**(시그니처 `:223-241`):

```ts
  split: number
  onSplitChange: (fraction: number) => void
  onSplitOpen?: () => void
```

머리 주석(`:204-222`)에 `@param` 세 줄을 더한다 — 이 저장소의 명세 관례이고, 그래프의 spec 커버리지가 이 주석을 읽는다:

```
 * @param split        코드 패널이 이 줄에서 차지하는 몫 (0..1). 셸이 들고 있다
 * @param onSplitChange  경계가 옮겨졌다 — 새 몫을 셸에 돌려준다
 * @param onSplitOpen  스플릿이 나타났다. 셸이 자리를 낼 기회이며, 나타나는
 *                     순간에만 불린다
```

`@flow`의 "plus the code viewer when a row was double-clicked" 를 "…, 캔버스 옆의 코드 패널로 — 덮지 않고 나란히, 사이의 경계는 끌 수 있다" 로 고치고, `주요 내부 변수` 줄에 `splitRow(그래프+코드가 나눠 갖는 줄 — 비율의 기준자)`를 더한다.

**(c) ref 하나와 콜백 둘** (`canvas` ref 옆):

```ts
/** 그래프와 코드가 나눠 갖는 줄. 비율은 언제나 이 사각형을 기준으로 잰다. */
const splitRow = useRef<HTMLDivElement>(null)
```

```tsx
/**
 * 경계가 포인터를 따라간다.
 *
 * @param clientX  포인터의 client x
 * @flow  줄을 아직 못 재면 아무것도 하지 않는다 ; 잰 폭으로 몫을 구하고, 양쪽
 *        바닥으로 자른 뒤 셸에 돌려준다 — 여기에는 상태가 없다
 */
const moveSplit = useCallback(
  (clientX: number): void => {
    const node = splitRow.current
    if (!node) return
    const rect = node.getBoundingClientRect()
    onSplitChange(clampSplit(splitFrom(clientX, rect), rect.width))
  },
  [onSplitChange]
)

/**
 * 포인터 없이 경계를 한 칸.
 *
 * @param direction  -1은 왼쪽(코드가 넓어진다), +1은 오른쪽
 */
const stepSplit = useCallback(
  (direction: number): void => {
    const width = splitRow.current?.getBoundingClientRect().width ?? 0
    onSplitChange(clampSplit(split - direction * SPLIT_STEP, width))
  },
  [onSplitChange, split]
)
```

**(d) 나타나는 순간을 알린다** (다른 effect들 옆):

```tsx
// 스플릿이 나타나는 순간, 그리고 그때만. 의존성이 `codeOpen`인 것이 요구사항의
// "다른 노드 더블클릭 → 스플릿 유지"를 코드로 보장한다: 두 번째 더블클릭은
// 이 불리언을 바꾸지 않으므로 셸은 다시 불리지 않는다.
useEffect(() => {
  if (codeOpen) onSplitOpen?.()
}, [codeOpen, onSplitOpen])
```

**(e) 본문 줄의 재구성** (`:1028-1274`). 캔버스 래퍼(`relative min-w-0 flex-1`)와 그 안의 `Legend`/`Minimap`은 **한 줄도 바뀌지 않는다** — 바깥에 줄을 하나 더 두를 뿐이다:

```tsx
<div className="flex min-h-0 flex-1">
  {/* 그래프와 코드의 줄. 비율의 기준자가 이 요소인 이유는, 코드 패널의
      오른쪽 끝이 곧 이 줄의 오른쪽 끝이어서 드래그의 산수가 사각형 하나로
      끝나기 때문이다 — Node 패널은 이 줄 밖에 있고, 접히고 펴져도 비율의
      의미를 흔들지 않는다. */}
  <div ref={splitRow} className="flex min-w-0 flex-1">
    <div className="relative min-w-0 flex-1">
      {/* …기존 캔버스 · Legend · Minimap 그대로… */}
    </div>

    {/* 소환된 에디터. 접힌 띠는 없다 — [코드 보기]가 돌아오는 길이고,
        상시 세로 막대는 캔버스만 좁힌다. */}
    {codeOpen ? (
      <>
        <SplitHandle onMove={moveSplit} onStep={stepSplit} />
        <div
          className="flex min-w-0 shrink-0 flex-col bg-panel"
          style={{ width: `${(split * 100).toFixed(3)}%` }}
        >
          <CodeViewer target={codeTarget} onClose={() => setCodeOpen(false)} />
        </div>
      </>
    ) : null}
  </div>

  {/* …기존 Node 패널 / 접힌 띠 그대로… */}
</div>
```

- `border-l border-line`은 코드 패널에서 **핸들로 옮긴다**(경계선이 곧 잡는 자리다).
- 퍼센트는 flex 컨테이너의 content box 기준이고 그 컨테이너가 정확히 `splitRow`이므로, 드래그가 계산한 몫과 렌더가 그리는 몫이 같은 기준자를 쓴다.
- `shrink-0` + `min-w-0`은 지금 Node 패널이 쓰는 조합 그대로다.

**(f) `SplitHandle`** (파일 하단, `SegButton`/`StepButton` 이웃):

```tsx
/**
 * 그래프와 코드 사이의 경계 — 끌어서 옮기고, 화살표로도 옮긴다.
 *
 * 캔버스의 스크롤 컨테이너 *밖*에 있으므로 `startPan`과는 만나지 않는다:
 * 경계를 끄는 일이 화면을 패닝할 수 없다는 것이 구조로 보장된다. 포인터
 * 캡처를 잡는 이유는 박스 드래그와 같다 — 경계를 끌다 코드 위로 넘어가도
 * 이 핸들이 계속 그 포인터의 주인이어야 한다.
 *
 * @param onMove  포인터의 client x. 새 몫은 부모가 줄을 재서 정한다
 * @param onStep  화살표 한 칸: -1은 왼쪽, +1은 오른쪽
 * @flow  주 버튼만 잡는다 ; 캡처를 쥔 포인터의 move만 읽는다 ; up·cancel·
 *        lostpointercapture 중 처음 도착한 하나가 놓고, 나머지는 자기 앞에
 *        누가 다녀갔음을 id 불일치로 안다
 */
function SplitHandle({
  onMove,
  onStep
}: {
  onMove: (clientX: number) => void
  onStep: (direction: number) => void
}): JSX.Element {
  const held = useRef<number | null>(null)

  const release = (event: ReactPointerEvent<HTMLDivElement>): void => {
    if (held.current !== event.pointerId) return
    held.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the code panel"
      title="Drag to resize · ← → to nudge"
      tabIndex={0}
      className="w-1 shrink-0 cursor-col-resize border-l border-line hover:bg-accent-soft focus:bg-accent-soft focus:outline-none"
      style={{ touchAction: 'none' }}
      onPointerDown={(event) => {
        if (event.button !== 0) return
        // 아니면 브라우저가 두 패널에 걸친 텍스트 선택을 시작한다.
        event.preventDefault()
        event.currentTarget.setPointerCapture(event.pointerId)
        held.current = event.pointerId
      }}
      onPointerMove={(event) => {
        if (held.current !== event.pointerId) return
        onMove(event.clientX)
      }}
      onPointerUp={release}
      onPointerCancel={release}
      onLostPointerCapture={release}
      onKeyDown={(event) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
        // 아니면 스크롤 컨테이너가 같이 옆으로 흐른다.
        event.preventDefault()
        onStep(event.key === 'ArrowLeft' ? -1 : 1)
      }}
    />
  )
}
```

`ReactPointerEvent`는 이 파일이 이미 import 하고 있다(`:11`).

### 2.6 `desktop/README.md` (수정)

`:48` 한 문장을 사실에 맞춘다:

```
Every other screen (Graph / Code / Diff / Test / Browser) is still mock data,
and since v0.2.9 none of them is reachable from the tab bar: the files stay,
the routing is gone. The tab bar is Plan and Graph — and Graph is the Function
DB, read from `.aidev/graph/graph.db`.
```

---

## 3. 검증

### 3.1 자동

| 명령 | 무엇을 말해 주는가 |
|---|---|
| `npx --prefix desktop tsc --noEmit -p desktop` (**선언된 것**) | 이 워크트리에서는 `node_modules`가 없고 `tsconfig.json`이 `files: []`라 **아무것도 검사하지 않는다**. 우회하지 않고 그대로 둔다 |
| `npm test --prefix desktop` (의존성이 있는 기계에서) | `typecheck:node` + `typecheck:web` + `tsc -p tsconfig.test.json` + `node --test` — 좁아진 `SurfaceId`가 App/SurfacePane 전부에서 맞는지, 그리고 §2.2가 실제로 도는지 |
| `python -m pytest -q` | 엔진 무변경. `tests/test_graph.py:629`의 ts 함수 수는 파일 추가로 늘기만 한다 |
| `python -m aidev.specs --base <요구사항 커밋>` | `paths=("*.py",)` — 이 slice의 diff에 `.py`가 없으므로 위반 0 |

### 3.2 손으로 (Done Criteria 대조)

| # | 확인 | 기대 |
|---|---|---|
| 1 | 앱을 켠다 | 탭바에 **Plan · Graph** 둘뿐. `[DEMO]` 배지는 탭바에서 사라졌다(사이드바·인스펙터에는 남는다 — 거기는 여전히 mock이다) |
| 2 | Graph 탭 | 첫 화면이 Function DB다(`primary` 기본값) |
| 3 | 함수 행 더블클릭 | 좌 그래프 / 우 Monaco. **캔버스가 좁아질 뿐 덮이지 않는다**. 우측 인스펙터가 32px 띠로 접힌다 |
| 4 | 경계를 좌우로 끈다 | 코드 폭이 포인터를 따라오고, 그래프는 320px, 코드는 360px 아래로 내려가지 않는다. Monaco가 스스로 다시 레이아웃한다 |
| 5 | 경계를 Tab으로 잡고 ←/→ | 한 번에 2%씩 움직인다 |
| 6 | 인스펙터 셰브런을 눌러 다시 편다 | 펴진다. **그 상태에서 다른 노드를 더블클릭해도 다시 접히지 않는다** |
| 7 | 다른 노드 더블클릭 | 우측 Monaco만 바뀐다(경로·범위·강조). 스플릿과 비율은 그대로 |
| 8 | 코드 패널 [닫기] | 그래프가 전체 폭으로 복귀. 인스펙터는 접힌 채(수동으로만 돌아온다) |
| 9 | 다시 더블클릭 | **아까 끌어 둔 비율 그대로** 열린다 |
| 10 | Plan 탭 → Graph 탭 → 더블클릭 | 화면 상태(선택·코드 패널)는 리셋되지만 **비율은 남아 있다**(셸이 들고 있으므로) |
| 11 | 회귀: 빈 캔버스 드래그 | 팬이 그대로 동작한다(핸들은 스크롤 컨테이너 밖이다) |
| 12 | 회귀: 상자 드래그 · 행 선택 · 휠 줌 · 미니맵 · Trace(방향·깊이·끊김 스텁) · Escape 두 단계 | 스플릿이 열린 채로도 전부 그대로 |
| 13 | 회귀: 액티비티 바 | Pipeline → Plan 탭, Function graph → Graph 탭. Changes/Tests/Code graph(demo)는 **사이드바만** 바꾼다(메인 패인은 그대로) — 열 mock 화면이 없어졌으므로 |
| 14 | 회귀: 두 번째 페인 버튼 | 열면 Plan이 뜬다(더 이상 Diff가 아니다). 각 페인은 각자의 그래프를 가지며 비율·줌은 공유한다 |

---

## 4. 무엇이 잘못될 수 있나

| # | 위험 | 완화 |
|---|---|---|
| R1 | **이 워크트리에서 `tsc`를 돌릴 수 없다** (`node_modules` 부재) | 새 라이브러리·새 API 0개. 모든 타입은 이 파일들에 이미 있는 패턴의 복제이고, 유일한 새 로직은 §2.2가 검증하는 순수 함수 두 개다 |
| R2 | 좁아진 `SurfaceId`를 놓친 자리가 남아 타입 에러 | 참조는 정확히 다섯 곳(`App.tsx:25,47,48` + `SurfacePane.tsx:17,55-56`)이고 전부 §2.3/2.4가 다룬다. `grep -rn "'diff'\|'browser'\|'test' as const" desktop/src/renderer` 로 마지막에 한 번 훑는다 |
| R3 | 퍼센트 폭이 flex에서 예상과 다르게 잡힌다 | 기준자는 `splitRow` 하나이고, 코드 패널은 `shrink-0`(Node 패널이 쓰는 조합 그대로), 그래프는 `flex-1 min-w-0`이다. 핸들 4px은 그래프 쪽에서 빠진다 — 비율의 의미가 1% 미만 어긋나는 것은 눈에 보이지 않는다 |
| R4 | 창을 아주 좁히면 두 바닥을 다 지킬 수 없다 | `clampSplit`이 그 경우 반으로 나눈다(굶기지 않는다). 드래그 시점에만 자르므로, 그 뒤 창을 줄이면 두 쪽이 **비례해서** 줄어든다 — "비율"을 기억하라는 요구의 직접적인 귀결이며 의도된 동작이다 |
| R5 | 드래그 중 Monaco가 매 프레임 `layout()`을 돈다 | 읽기 전용 단일 모델이고 `automaticLayout`은 `ResizeObserver` 기반이라 프레임에 한 번이다. 무겁다고 판명되면 "놓을 때만 반영"이 남은 길이지만, 계획에는 넣지 않는다(끌면서 보이지 않는 리사이즈가 더 나쁘다) |
| R6 | 인스펙터 자동 접힘이 `App.tsx:91-97`의 자동 펼침과 싸운다 | 트리거가 다르다: 저쪽은 `selectedSymbolId`(mock 심볼), 이쪽은 `codeOpen`. 함수 그래프의 더블클릭은 `functionId`만 움직인다 |
| R7 | 라우팅에서 끊긴 mock 화면 5개가 죽은 코드로 남아 계속 컴파일된다 | 요구사항이 **파일 삭제를 금지**했다. tsc 대상에 남지만 자족적으로 컴파일되고, 파일 단위 unused 규칙은 이 eslint 설정에 없다. README가 그 상태를 한 문장으로 기록한다 |
| R8 | 핸들이 팬·박스 드래그와 포인터를 다툰다 | 핸들은 스크롤 컨테이너의 형제이지 자식이 아니다. `startPan`은 컨테이너 자신의 `pointerdown`에서만 시작하므로 두 경로는 DOM 상 만나지 않는다 |
| R9 | "인스펙터"가 그래프 안의 Node 패널을 뜻한 것이었다면 | 접는 대상을 App의 `Inspector`(우측 사이드·이름 일치·셰브런 있음)로 읽었다. 만약 Node 패널이 의도였다면 §2.4의 콜백 한 줄을 `setDetailOpen(false)`로 바꾸는 것이 전부다 — 나머지 구조는 그대로다 |

---

## 5. 구현 순서

1. `split.ts` + `split.test.ts` (§2.1, §2.2) — 산수를 먼저 못박는다.
2. `SurfacePane.tsx` (§2.3) — 탭을 줄이면 컴파일러가 나머지 자리를 전부 가리킨다.
3. `App.tsx` (§2.4) — 컴파일러가 가리킨 자리 + 비율 상태 + 자리 내주기.
4. `FunctionGraphSurface.tsx` (§2.5) — 줄 하나 두르기 → 핸들 → effect.
5. `README.md` (§2.6).
6. §3.1의 명령들, 그다음 §3.2의 14줄.
