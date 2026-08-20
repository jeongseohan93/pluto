# Plan — Graph 탭 엣지 색·스타일 복구 (회귀 수정)

base `99b6f37` · 대상은 **Function graph 서피스**(`surface === 'functions'`, `SurfacePane.tsx:143`)다. `renderer/src/features/graph/GraphSurface.tsx`는 [DEMO] 목업이므로 이 slice와 무관하다(§6).

---

## 0. 읽고 확인한 사실 (추측 아님)

| 사실 | 출처 |
|---|---|
| 엣지 색 매핑은 **소스에 그대로 살아 있다**: `stroke={edge.incoming ? 'var(--color-ok)' : 'var(--color-accent)'}`, callers만 `strokeDasharray="3 3"`, 양쪽 다 `markerEnd="url(#fn-arrow)"` | `FunctionGraphSurface.tsx:941-954` |
| 토큰도 살아 있다: `--color-accent: #4a7fc7`, `--color-ok: #4f9d62` | `theme.css:23, :25` |
| 같은 두 토큰을 같은 캔버스의 다른 것들이 쓰고 있고 그것들은 정상 렌더된다(선택 행 배경 `--color-accent-soft`, 명세 점 `--color-ok`, 행 마크 바) → **"토큰이 사라져서 무색"은 아니다** | `:1359`, `:1392`, `:1425-1429` |
| 범례도 같은 두 토큰을 쓴다 (calls=accent 실선, called by=ok 파선) | `:1570-1589` |
| 즉 같은 매핑이 **세 벌** 존재한다: 엣지 레이어 `:947`, 행 마크 `markColour` `:1425-1429` + `both` 바 `:1368-1369`, 범례 `:1572/:1583`. "범례와 렌더 일치"를 보장하는 장치는 하나도 없다 | 위 세 곳 |
| 엣지 스타일 사슬에서 **평범한 CSS가 아닌 유일한 고리**: 화살표 마커 두 개가 `stroke="context-stroke"`를 쓴다. `context-fill`/`context-stroke`는 Firefox 확장이고 **Chromium(=Electron)은 구현하지 않는다**. 값이 무효면 marker 내부 `<path>`는 상속된 `stroke`를 쓰고, marker 콘텐츠는 **참조한 도형이 아니라 `<defs>`/`<svg>`에서 상속**하므로 결국 `stroke: none` → **화살촉이 아예 칠해지지 않는다** | `:893-919` |
| 그리고 이 사실을 지금까지 아무도 화면으로 검증하지 못했다: graph-visual은 worktree에 `node_modules`가 없어 앱을 띄우지 못했고(§7.1에 그렇게 적혀 있다), graph-pan의 수동 점검 목록도 실행되지 않았다 | `.aidev/history/20260820-graph-visual/plan.md:306`, `.../graph-pan/failure.md` |
| 이 worktree에도 `desktop/node_modules`가 **없다**(실측: `.bin/tsc` 부재). 그런데 front matter는 `setup:` 없이 `test_commands: npx --prefix desktop tsc --noEmit -p desktop` — graph-pan verify 1차를 죽인 그 명령 그대로다 | `tasks/edge-style-fix.md:4`, `.../graph-pan/diagnosis.md` |
| `tsconfig.test.json`은 `src/domains/**/*.ts`를 컴파일하되 `**/ui/**`는 제외한다 → **테스트가 닿을 수 있는 곳은 `layout.ts`뿐** | `desktop/tsconfig.test.json:19-28` |

### 진단 (정직하게)

`FunctionGraphSurface.tsx:947`의 색 지정은 **끊겨 있지 않다.** 그러므로 이 slice는 "색을 다시 적는" 작업이 **아니다**. 읽어서 실제로 찾을 수 있는 끊긴 곳은 두 군데다.

1. **방향 화살표가 Chromium에서 그려지지 않는다** (`context-stroke`). Done Criteria가 명시적으로 요구하는 "방향 화살표"가 여기서 죽는다. 요구사항이 말한 "매핑이 어디서 끊겼는지"에 정확히 해당하는, 코드를 읽어서 확인 가능한 유일한 단절이다.
2. **매핑이 세 벌이라 "범례 일치"에 기계적 보장이 없다.** 요구사항 2번 줄이 요구하는 것이 바로 이 보장이다.

색까지 정말로 무색이라면 원인은 소스가 아니라 런타임 쪽이므로, §4.3에 **2분짜리 판별 트리**를 둔다. 구현자는 고치기 전에 그것부터 확인하고, 결과를 보고서에 적는다.

---

## 1. 설계 결정

**D1. 매핑을 `layout.ts`의 표 하나로 단일화한다.** `ui/`는 `node --test`가 닿지 않는다(§0). 표를 `layout.ts`에 두면 "calls는 파랑, callers는 구분색+파선, 범례는 같은 값" 이 셋이 **눈이 아니라 테스트로** 증명된다. 이것이 요구사항의 "범례와 실제 렌더 색이 일치해야 한다"를 회귀로부터 지키는 유일한 방법이다.

**D2. 화살촉에 색을 명시한다 — kind별 마커 2개.** `context-stroke` 의존을 없앤다. 만약 이 Chromium이 `context-stroke`를 지원하더라도 결과 색은 **같은 토큰이므로 동일**하다 — 즉 이 변경은 최악의 경우에도 무해하고, 최선의 경우 잃었던 화살표를 돌려준다.

**D3. 새 색 토큰을 만들지 않는다.** graph-visual의 팔레트 결정(D6: calls=accent, callers=ok, 색만으로 구분하지 않도록 파선 유지)을 글자 그대로 보존한다.

**D4. 집계(파일 링크) 레이어와 `fn-arrow-flat`은 손대지 않는다.** 거기에도 같은 `context-stroke`가 있지만, 고치면 평시 뷰에 240개 화살촉이 새로 나타난다 — "다른 변경 금지"에 정면으로 걸린다. 의도적 미변경으로 보고서에 적는다.

**D5. 팬/드래그/줌/디밍/호버 코드는 한 줄도 건드리지 않는다.** 목록은 §3.

---

## 2. 변경 (파일 3개, 전부 가산적)

### 2.1 `desktop/src/domains/graph-view/layout.ts`

`groupMarks` 끝(현재 `:993`) 뒤, `matchFunctions` 앞에 새 절을 추가한다. 기존 export는 시그니처·동작 모두 불변.

```ts
// -------------------------------------------------------------- edge style
//
// One table, three readers: the drawn edge, the row's 2px mark, and the legend.
// Three copies of one mapping is how a legend comes to disagree with the screen,
// which is the regression this section exists to make impossible. The values are
// theme tokens and never literals, and `layout.test.ts` holds every one of them
// against theme.css — a renamed token fails a test instead of quietly painting
// an uncoloured line.

/** Which way an edge points, seen from the selection. */
export type EdgeKind = 'calls' | 'callers'

/** How one kind of edge is drawn: line, dash and arrowhead as one fact. */
export interface EdgeStyle {
  /** A `var(--color-…)` token — never a literal colour. */
  stroke: string
  /** `stroke-dasharray`, or null for a solid line. */
  dash: string | null
  /** The `<marker>` id this kind's arrowhead is drawn by. */
  marker: string
}

/** calls = accent(파랑) 실선, callers = ok(초록) 파선. 색이 유일한 차이가 되지
 *  않도록 파선을 함께 남긴다 — graph-visual의 결정 그대로. */
export const EDGE_STYLES: Readonly<Record<EdgeKind, EdgeStyle>> = {
  calls: { stroke: 'var(--color-accent)', dash: null, marker: 'fn-arrow-calls' },
  callers: { stroke: 'var(--color-ok)', dash: '3 3', marker: 'fn-arrow-callers' }
}

/**
 * How one edge of the selection's neighbourhood is drawn.
 *
 * @param incoming  is this someone calling the selection, rather than the
 *                  selection calling out?
 */
export function edgeStyle(incoming: boolean): EdgeStyle {
  return incoming ? EDGE_STYLES.callers : EDGE_STYLES.calls
}

/**
 * The colour one row mark is drawn in — the same two the edges use.
 *
 * Moved out of the component so the mapping has one home, and so a test can
 * hold it against `EDGE_STYLES` without a browser (`tsconfig.test.json`
 * excludes `ui/`).
 *
 * @param mark  what this row is to whatever the pointer is on
 * @flow  outgoing and the selection itself read accent, incoming reads ok, the
 *        rest are neutral
 */
export function markColour(mark: RowMark): string {
  if (mark === 'selected' || mark === 'calls') return EDGE_STYLES.calls.stroke
  if (mark === 'callers') return EDGE_STYLES.callers.stroke
  if (mark === 'hover') return 'var(--color-fg-dim)'
  return 'var(--color-fg-mute)'
}
```

`markColour`의 본문은 `FunctionGraphSurface.tsx:1425-1429`와 **동작이 동일하다**(리터럴이 표 참조로 바뀐 것뿐). `'both'`가 기본값으로 떨어지는 것도 그대로 둔다 — 컴포넌트가 `'both'`를 먼저 분기하므로 호출되지 않는 경로이고, 여기서 바꾸면 그것이 새 회귀다.

### 2.2 `desktop/src/domains/graph-view/ui/FunctionGraphSurface.tsx`

편집 5곳. 그 외는 한 글자도 바꾸지 않는다.

**E1 — import** (`:21-65`의 `@domains/graph-view/layout` 목록에 세 개 추가, 기존 정렬 규칙대로 대문자 상수 먼저·소문자 함수 뒤):
`EDGE_STYLES`(`FILE_BOX_H` 앞), `edgeStyle`(`edgePath` 뒤), `markColour`(`matchFunctions` 앞).

**E2 — `<defs>`** (`:893-904`의 `fn-arrow` 하나를 kind별 두 개로 교체. `fn-arrow-flat` `:905-919`는 **그대로 둔다**):

```tsx
{/* One arrowhead per edge kind, each painted with that kind's own token.
    These markers used `context-stroke`, which is a Firefox extension that
    Chromium does not implement — and marker content inherits from `<defs>`,
    not from the path that references it, so the arrowheads were resolving to
    `stroke: none` and never painted at all. The direction the legend promises
    has to be drawn in a colour this renderer actually has. */}
<marker
  id="fn-arrow-calls"
  viewBox="0 0 8 8"
  refX="7"
  refY="4"
  markerWidth="7"
  markerHeight="7"
  orient="auto-start-reverse"
>
  <path d="M1 1 L7 4 L1 7" fill="none" stroke={EDGE_STYLES.calls.stroke} strokeWidth="1.2" />
</marker>
<marker
  id="fn-arrow-callers"
  viewBox="0 0 8 8"
  refX="7"
  refY="4"
  markerWidth="7"
  markerHeight="7"
  orient="auto-start-reverse"
>
  <path d="M1 1 L7 4 L1 7" fill="none" stroke={EDGE_STYLES.callers.stroke} strokeWidth="1.2" />
</marker>
```

`fn-arrow`를 참조하는 곳은 `:951` 하나뿐이므로(grep 확인) 삭제해도 남는 참조가 없다. 데모 `GraphSurface.tsx`는 자기 `<svg>` 안에 자기 `arrow` 마커를 따로 갖고 있어 영향 없음.

**E3 — 엣지 레이어** (`:941-954` 교체):

```tsx
<g>
  {edges.map((edge) => {
    const style = edgeStyle(edge.incoming)
    return (
      <path
        key={edge.key}
        d={edge.d}
        fill="none"
        stroke={style.stroke}
        strokeWidth={1.4}
        strokeDasharray={style.dash ?? undefined}
        strokeOpacity={edge.faint ? 0.55 : 1}
        markerEnd={`url(#${style.marker})`}
      />
    )
  })}
</g>
```

**E4 — 로컬 `markColour` 삭제** (`:1419-1430` 전체, 주석 포함). 호출부 `:1372`는 그대로 두면 import된 것을 쓴다. `both` 바 `:1368-1369`의 리터럴 두 개도 `EDGE_STYLES.calls.stroke` / `EDGE_STYLES.callers.stroke`로 바꾼다 — 같은 매핑의 네 번째 사본이 남으면 안 된다.

**E5 — 범례** (`:1572`, `:1578-1586`): 두 스와치가 표를 읽게 한다.

```tsx
<line x1="0" y1="3" x2="18" y2="3" stroke={EDGE_STYLES.calls.stroke} strokeWidth="1.4" />
...
<line
  x1="0"
  y1="3"
  x2="18"
  y2="3"
  stroke={EDGE_STYLES.callers.stroke}
  strokeWidth="1.4"
  strokeDasharray={EDGE_STYLES.callers.dash ?? undefined}
/>
```

범례의 나머지 항목(파일 링크 굵기, has spec, grey = no spec, LOD 줄)은 손대지 않는다.

### 2.3 `desktop/src/domains/graph-view/layout.test.ts`

파일 말미에 `describe` 하나를 추가한다(기존 문체대로 — 단언은 사실 문장으로).

```ts
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
```

| 테스트 | 못 박는 것 |
|---|---|
| `edgeStyle` | `edgeStyle(false)`는 `EDGE_STYLES.calls`, `edgeStyle(true)`는 `EDGE_STYLES.callers` |
| 두 종류는 구분된다 | 두 `stroke`가 서로 다르다 · calls는 `dash === null`, callers는 dash가 있다(색만으로 구분하지 않는다) · 두 `marker` id가 서로 다르고 비어 있지 않다 |
| 범례 = 렌더 | `markColour('calls')`와 `markColour('selected')`가 `EDGE_STYLES.calls.stroke`와 **같은 문자열**, `markColour('callers')`가 `EDGE_STYLES.callers.stroke`와 같은 문자열 (범례·행 마크·엣지가 한 표를 읽는다는 증명) |
| 이동이 동작을 바꾸지 않았다 | `markColour('hover') === 'var(--color-fg-dim)'`, `markColour('near') === 'var(--color-fg-mute)'` |
| **토큰이 실재한다** | `EDGE_STYLES`의 모든 `stroke`와 `markColour`가 돌려주는 모든 값이 `theme.css`에 정의되어 있다 — 이 요구사항이 말한 "매핑이 끊긴 자리"를 테스트가 지키게 만드는 줄 |

마지막 것의 형태(경로는 CommonJS `__dirname` 기준, `out-test/domains/graph-view` → 위로 셋이 `desktop/`):

```ts
// out-test/domains/graph-view -> desktop/, then into the renderer's styles.
// Tied to tsconfig.test.json's rootDir/outDir on purpose: if those move, this
// fails loudly rather than silently stopping checking anything.
const THEME = readFileSync(
  resolve(__dirname, '../../../src/renderer/src/styles/theme.css'),
  'utf8'
)
const defined = (token: string): boolean =>
  THEME.includes(`${token.slice('var('.length, -1)}:`)
```

`tsconfig.test.json`은 `types: ["node"]`이고 `module: CommonJS`이므로 `__dirname`과 `node:fs`는 그대로 쓸 수 있다(`src/main/aidev-store.test.ts`가 이미 `node:fs`를 쓴다).

---

## 3. 절대 건드리지 않는 것 (재회귀 금지 목록)

`startPan`/`movePan`/`endPan` (`:688-754`) · `startDrag`/`moveDrag`/`endDrag` (`:619-671`) · `onLostPointerCapture` 배선 (`:874`) · svg의 `pointerEvents` 스타일 (`:890`) · 휠 리스너 (`:458-482`) · 스페이스 효과 (`:422-448`) · 배치 `useLayoutEffect` (`:340-384`) · 선택 스크롤 효과 (`:391-403`) · `data-box`/박스 `opacity`/`touchAction` (`:966-972`) · `FileLinks`와 `fn-arrow-flat` (`:1191-1221`, `:905-919`) · `AGGREGATE_DIM`/`BOX_DIM` (`:79-81`) · `edgesFor`(`:1459-1508`) · `layout.ts`의 기존 export 전부 · `graph-store.ts` · `types.ts` · `App.tsx` · `SurfacePane.tsx` · `Minimap.tsx` · `aidev/**`와 모든 Python.

`theme.css`는 §4.3의 판별이 "변수가 실제로 없다"를 증명한 경우에만, 그 한 줄만 고친다. 그 외에는 무변경.

---

## 4. 검증

### 4.1 먼저 해결해야 하는 환경 문제 (이걸 안 하면 verify가 코드와 무관하게 죽는다)

이 worktree에 `desktop/node_modules`가 없다. 선언된 `test_commands: npx --prefix desktop tsc --noEmit -p desktop`는 graph-pan 1차 verify를 죽인 그 명령이고(레지스트리의 미끼 `tsc` 패키지를 받아 exit 1), 게다가 `desktop/tsconfig.json`은 `"files": []` + references인 솔루션 파일이라 **통과해도 0개 파일을 검사한다**.

1. 구현 중에 `npm ci --prefix desktop`을 직접 실행한다(허용되지 않으면 그 사실을 보고서에 그대로 적고, 아래 명령들이 실행되지 않았음을 명시한다 — 통과했다고 쓰지 말 것).
2. `tasks/edge-style-fix.md` front matter에 `setup: npm ci --prefix desktop` 한 줄을 넣는다. 이것이 파이프라인이 이 문제를 위해 갖고 있는 기제이고(`pipeline.py:304` `resolve_setup`, `:3199` `run_setup`, `Bash(npm ci:*)` 자동 허용), graph-pan diagnosis가 이미 처방한 것이다. 단 front matter는 실행당 한 번만 읽히므로(`pipeline.py:5225-5244`) **이번 실행에는 적용되지 않는다** — 다음 launch/재개를 위한 것이다. 제품 코드 변경이 아니라 배관이며, 보고서에 이유와 함께 적는다.

### 4.2 명령

| 명령 | 증명하는 것 |
|---|---|
| `npm run typecheck --prefix desktop` | 진짜 타입 게이트 (`typecheck:node` + `typecheck:web`; `.tsx`와 `src/domains/**`를 실제로 덮는다) |
| `npm test --prefix desktop` | typecheck + `tsc -p tsconfig.test.json` + `node --test` — §2.3의 새 테스트를 **실제로 돌리는 유일한 명령** |
| `npx --prefix desktop tsc --noEmit -p desktop` | 선언된 게이트. 통과시켜야 하지만 타입 검사로 세지 말 것 |
| `python -m pytest -q` | Done Criteria의 pytest. 이 slice는 Python을 한 줄도 건드리지 않으므로 순수 회귀 게이트다. 실패가 나오면 graph-pan 때 보고된 **기존 실패와 같은 것인지** 확인하고 그대로 보고할 것(쫓아가서 고치지 말 것 — "다른 변경 금지") |

`tests/test_graph.py`가 이 저장소 자신을 파싱해 실패율 5% 미만을 요구한다 → 새 TS는 평범한 문법으로(중첩 템플릿 리터럴·특이 제네릭 금지). 새 export에는 이 파일들의 문서 형식(요약 줄, `@param`, 분기가 있으면 `@flow`)을 지킨다.

### 4.3 화면 확인 (`npm run dev`, 본 것만 적을 것)

1. **Function graph** 탭 → 검색창에 `run_pipeline` → Enter.
   - calls = 파란 실선 곡선, callers = 초록 파선, **양쪽 다 피호출자 쪽 끝에 화살촉**이 있다.
   - 무관 상자는 흐려지고 집계 레이어도 흐려진다(디밍 유지).
2. 좌하단 범례의 두 스와치 색이 화면의 곡선 색과 **같다**.
3. 회귀 확인: 빈 캔버스 드래그(팬) · 상자 드래그(곡선이 따라온다) · 스페이스+드래그 · 휠 줌 인/아웃(55% 경계 통과) · Escape/빈 곳 클릭/같은 행 재클릭으로 집계 뷰 복귀 — 전부 이전과 같다.

**만약 이 변경 뒤에도 곡선이 무색이라면** — 화면의 선은 이 `<path>`들이 아니다. DevTools에서 집계 레이어 다음 `<g>`의 자식을 본다.

- 자식이 **0개** → `edgesFor`가 `[]`를 돌려준 것이다(`:1466` `!detail` 또는 `lod === 'file'`). 눈에 보였던 "엣지"는 집계 파일 링크(`--color-line-strong`, opacity 0.18)였고, 그것이 정확히 "무색/기본색 선"으로 읽힌다. 그때 고칠 곳은 색이 아니라 `getGraphNode` 상세 조회 또는 LOD 분기다 — **발견한 것을 보고서에 적고 그쪽을 고친다.**
- 자식은 있는데 `stroke="var(--color-accent)"`가 회색으로 칠해진다 → 커스텀 프로퍼티가 렌더러에 없는 것이다. 콘솔에서 `getComputedStyle(document.documentElement).getPropertyValue('--color-accent')`가 빈 문자열이면 Tailwind가 `@theme` 변수를 tree-shaking한 것이고, 그때의 최소 수정은 `theme.css:9`의 `@theme`을 `@theme static`으로 바꾸는 **한 단어**다. 그 경우에만 `theme.css`를 건드린다.

---

## 5. 위험

1. **`context-stroke` 진단이 이 Chromium에서 틀릴 수 있다.** 그래도 변경은 안전하다 — 마커에 명시하는 색이 선의 색과 같은 토큰이므로 결과가 같고, 불확실성만 사라진다. (틀렸다면 화살표는 원래도 보였던 것이고, 보고서에 그렇게 적는다.)
2. **마커는 참조한 도형이 아니라 `<defs>` 트리에서 상속한다.** 그래서 색을 명시해야 하고, 반대로 callers의 파선이 화살촉으로 새지 않는다. 화살촉이 파선으로 보이면 이 가정이 틀린 것이다.
3. **분할 뷰에서 마커 id가 중복된다.** 두 패널이 모두 Function graph면 `fn-arrow-calls`가 문서에 둘 존재하고 두 번째 패널은 첫 번째의 마커로 해석된다 — 모양도 색도 같아서 눈에 띄지 않는다. 기존 `fn-arrow`/`fn-arrow-flat`도 똑같았던 선행 문제이므로 id 네임스페이싱은 범위 밖이다.
4. **`markColour`가 배치 모듈로 간다** — UI 값이 `layout.ts`에 있는 것은 어색하지만, `tsconfig.test.json`이 `ui/`를 제외하므로 테스트가 닿을 수 있는 유일한 자리다. 인자에 대한 순수 함수라는 성질은 유지된다.
5. **`npm test`를 이 worktree에서 못 돌릴 수 있다**(§4.1). 그러면 새 테스트는 "작성했으나 실행하지 못함"으로 정직하게 보고한다.
6. **회귀 표면.** 편집은 한 컴포넌트의 5곳 + 순수 모듈의 가산 절 하나다. 팬/드래그/줌/디밍 코드는 §3 목록대로 diff에 나타나면 안 된다 — 커밋 전에 `git diff`로 그것을 직접 확인할 것.

---

## 6. 하지 않는 것

집계(파일 링크) 레이어와 `fn-arrow-flat`의 화살촉 · 새 색 토큰 · 팔레트 변경 · 애니메이션 추가 · 데모 `GraphSurface.tsx` · IPC/타입/스토어 · 마커 id 네임스페이싱 · 팬/드래그/줌/호버/디밍 로직 · `aidev/**`와 Python(§4.2의 pytest는 실행만 하고 고치지 않는다).
