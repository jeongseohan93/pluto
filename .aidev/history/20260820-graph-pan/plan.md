# Plan — Graph 탭 캔버스 팬 (빈 공간 드래그 이동)

## 0. What already exists (read, not guessed)

| Fact | Where |
|---|---|
| The "Graph 탭" of this requirement is the **Function graph** surface (`surface === 'functions'`), the one earlier slices (graph-visual / graph-interact / graph-lod) built. | `desktop/src/renderer/src/features/workspace/SurfacePane.tsx:143` |
| The canvas is a **native scroll container** (`div ref={canvas} className="… overflow-auto"`) holding one `<svg>` sized `size.* * zoom` with a `0 0 width height` viewBox. | `FunctionGraphSurface.tsx:639-646` |
| A **background `<rect>`** covering the whole canvas is drawn *before* the boxes; its click is "clear the selection". It is, by construction, exactly "empty space". | `FunctionGraphSurface.tsx:677-682` |
| **Node drag** lives on each box's wrapper `<g>` (`startDrag`/`moveDrag`/`endDrag`, pointer capture on the `<g>`, slop via `isDrag`, offset divided by `zoom`). | `FunctionGraphSurface.tsx:485-537`, `:709-727` |
| The **minimap already subscribes to the container's `scroll` event** and re-reads on `zoom`/`size` change. Anything that moves `scrollLeft/scrollTop` syncs it for free. | `Minimap.tsx:71-90` |
| Zoom is App state shared by both split panes; the only producers today are the `±` buttons. **There is no wheel handler anywhere in `desktop/`** (grepped `wheel|onWheel|deltaY` → no matches). | `App.tsx:52`, `SurfacePane.tsx:120-122` |
| A `useLayoutEffect` on `[zoom, layout]` re-centres the scroll on the box nearest the old view's middle after every zoom/level change, with `pendingPath` as an override hook. **Any mouse-centred zoom must go through this, or it will be undone before paint.** | `FunctionGraphSurface.tsx:288-316` |
| `layout` is memoised on `[files, lod, counts]` where `lod` is the *band string* — so a zoom change **inside one band leaves `layout` referentially identical**, and the `[selected, layout]` scroll effect (`:323-335`) does not re-run. | `:199`, `:201`, `:323` |
| Pure viewport math (`centerScroll`, `viewportOf`, `nearestBoxPath`, `clampOffset`, `isDrag`, `zoomIn/zoomOut`, `ZOOM_STEPS`) lives in `layout.ts` and is tested browser-free in `layout.test.ts`. | `layout.ts:502-591`, `layout.test.ts:656-728` |
| `test_specs.py` checks **Python only** (`("src/app.ts", True),  # only Python is checked`), so TS docs are a house-style obligation, not a gate. | `tests/test_specs.py:146` |

## 1. Decisions (made now, so implement does not re-litigate them)

1. **Pan = moving the scroll container**, not a new transform layer. `scrollLeft/scrollTop` is what `centerScroll`, `viewportOf` and the minimap already speak; a second translate layer would mean two truths about "where the reader is". Free consequence: minimap sync, scrollbars, and clamping all keep working with no new code.
2. **Hit test = `event.target.closest('[data-box]')`.** The box wrapper `<g>` gets a `data-box={box.path}` attribute (rows already carry `data-fn`). One handler on the container div in the bubble phase: a pointerdown that came from inside a box has already been claimed by `startDrag`, so pan returns; anything else pans. No `stopPropagation`, no coordinate math, no second source of truth.
3. **Space-hold makes the `<svg>` pointer-transparent** (`style={{ pointerEvents: 'none' }}` while `spaceHeld || panning`). This is the whole forced-pan mechanism *and* the whole cursor mechanism in one prop: hit-testing falls through to the container div, so `startDrag` can never fire, `closest('[data-box]')` can never match, and no descendant's `cursor-pointer`/`cursor-grab` can beat the container's `cursor-grabbing`. It also suppresses hover-mark flicker during a pan, which matches the existing `if (drag.current) return` guard in `enter` (`:568`).
   *Rejected alternative:* a `.panning *` CSS rule in `theme.css` — Tailwind v4's `utilities` layer wins over `components` regardless of specificity, so it would need unlayered CSS or `!important`. The `pointer-events` route needs neither.
4. **Plain wheel zooms, centred on the cursor.** The requirement names 휠 as zoom and rules out keyboard/inertia scrolling; with drag-pan in place, wheel-as-scroll has a replacement, and wheel-as-zoom does not. Wheel must be a **native `passive:false` listener** — React registers `wheel` passively at the root, so `preventDefault()` inside `onWheel` is a silent no-op.
5. **Zoom is continuous, clamped to the ladder's ends** (`ZOOM_STEPS[0]`=0.4 … `[7]`=1.8). `nearestStep` already handles an off-ladder zoom (`layout.test.ts:477` asserts it for 0.63), so the `±` buttons keep working from any wheel-produced value.
6. **Band change ⇒ hand over to the existing recentring.** Keeping a point under the cursor is meaningless when the placement itself changes; when `lodFor(next) !== lodFor(from)` the wheel writes `pendingPath.current = nearestBoxPath(...)` instead — the box under the cursor becomes the box in the middle. Same machinery `pickFile` already uses (`:456-465`).

## 2. `desktop/src/domains/graph-view/layout.ts` — new pure math

All additive; no existing export changes shape. Each gets the file's docstring form (summary line, `@param` per parameter, `@flow` where it branches).

```ts
// ------------------------------------------------------------------ panning
/** How far the view moves while a pan is under way — client pixels on both
 *  sides, 1:1 with the hand. (Node drag divides by the zoom because its offset
 *  is in user units; a pan does not, because the scroll already is in pixels.) */
export function panScroll(
  base: { left: number; top: number },
  moved: { dx: number; dy: number }
): { left: number; top: number }         // { left: base.left - moved.dx, top: base.top - moved.dy }
```

```ts
// ----------------------------------------------------- zoom about a point
/** The user-unit point currently under one place in the window. */
export function pointAt(
  scroll: { left: number; top: number },
  at: { x: number; y: number },          // client px from the container's top-left
  zoom: number
): { x: number; y: number }

/** The scroll that puts one canvas point under one window point — "zoom about
 *  the cursor", once the zoom has already changed. Clamped at both ends. */
export function anchorScroll(
  point: { x: number; y: number },
  at: { x: number; y: number },
  zoom: number,
  view: { width: number; height: number },
  size: { width: number; height: number }
): { left: number; top: number }

export const ZOOM_MIN = ZOOM_STEPS[0]
export const ZOOM_MAX = ZOOM_STEPS[ZOOM_STEPS.length - 1]
/** One wheel notch's share of an e-fold. 100px ⇒ ×1.16 — a notch you feel once. */
export const ZOOM_WHEEL_RATE = 0.0015
/** `deltaMode` 1 and 2 are lines and pages; these are what they are worth in px. */
export const WHEEL_LINE_PX = 16
export const WHEEL_PAGE_PX = 400

/** Where one wheel event lands the zoom. Exponential, so a notch is the same
 *  proportion at 0.4 as at 1.8; clamped to the ladder's two ends. */
export function zoomBy(zoom: number, deltaY: number, deltaMode?: number): number
```

`centerScroll` is **rewritten as one line over `anchorScroll`** (`at = {x: view.width/2, y: view.height/2}`), identical arithmetic to today's body (`layout.ts:528-541`). Its five existing tests (`layout.test.ts:656-686`) then also stand as proof of `anchorScroll`'s clamp. Signature and behaviour unchanged for its three call sites (`FunctionGraphSurface.tsx:308`, `:328`, `Minimap.tsx:102`).

Guards to keep, matching the file's habits: `zoom > 0 ? zoom : 1` everywhere (`viewportOf:552` does this and `layout.test.ts:694` asserts it); a non-finite `deltaY` returns the clamped current zoom, never a `NaN`.

## 3. `desktop/src/domains/graph-view/ui/FunctionGraphSurface.tsx` — the surface

**New local type** (next to `Dragging`, `:90-100`):

```ts
/** A pan in progress: the pointer that owns it, and where the view was. */
interface Panning {
  pointerId: number
  /** Where the pointer went down, in client pixels — the slop is measured here. */
  startX: number
  startY: number
  /** The scroll position this pan started from. */
  left: number
  top: number
}
```

**New state and refs** (beside `drag`/`dragged`, `:183-194`):
- `const [panning, setPanning] = useState(false)` — flips **once** per pan (at commit, and at release), never per frame. It feeds only the cursor class and the svg's `pointerEvents`.
- `const [spaceHeld, setSpaceHeld] = useState(false)`, mirrored into `spaceRef` in render (the file's established pattern, `:225-234`).
- `const pan = useRef<Panning | null>(null)`.
- `const pendingHold = useRef<{ point: {x:number;y:number}; at: {x:number;y:number} } | null>(null)` — the wheel's anchor for the next placement, the sibling of `pendingPath`.

**Handlers** (each with the file's doc form):

- `startPan(event: ReactPointerEvent<HTMLDivElement>)` — return unless `event.button === 0`; return if `pan.current || drag.current`; return if `event.target instanceof Element && event.target.closest('[data-box]')` (a node owns this drag — and when space is held the svg is transparent, so this can never match). Then `preventDefault()`, `event.currentTarget.setPointerCapture(event.pointerId)`, `dragged.current = false`, record `{pointerId, startX, startY, left: node.scrollLeft, top: node.scrollTop}`.
- `movePan(event)` — id must match; below `isDrag(mx, my)` do nothing (a click on bare canvas must still reach `clearSelection`); on the first commit `setPanning(true)` and `dragged.current = true` (so the click that ends a real pan does not also clear the selection at `:553-556`); then assign `panScroll(...)` to `scrollLeft/scrollTop` on `event.currentTarget`.
- `endPan(event)` — id must match; release capture if held; `pan.current = null`; `setPanning(false)`. Wired to both `onPointerUp` and `onPointerCancel` (a touch that the browser takes over ends the pan cleanly rather than sticking).

**Space bar** — a second window-key effect beside the Escape one (`:339-348`), deliberately not folded into it:
- `keydown`: ignore unless `event.code === 'Space'` (layout-independent); ignore `event.repeat`; ignore when `takesSpace(event.target)` or `drag.current` (arming mid-node-drag would flip the svg transparent under a live capture — a case not worth the uncertainty); then `preventDefault()` (or the scroll container pages down under the hold) and `setSpaceHeld(true)`.
- `keyup` → `setSpaceHeld(false)`; `window` `blur` → the same, or alt-tabbing away leaves pan armed forever.
- New file-local helper `takesSpace(target: EventTarget | null): boolean` — `INPUT` / `TEXTAREA` / `SELECT` / `BUTTON` / `isContentEditable`. The search box (`:875`) and every toolbar button keep the space bar.

**Wheel** — a native listener, since React's is passive:

```ts
useEffect(() => {
  const node = canvas.current
  if (!node || !onZoomChange) return          // no way to change the scale -> normal scrolling
  const onWheel = (event: WheelEvent): void => {
    event.preventDefault()                     // also at the ends of the range, or the
    const from = zoomRef.current               // canvas scrolls when the zoom will not move
    const next = zoomBy(from, event.deltaY, event.deltaMode)
    if (next === from) return
    const rect = node.getBoundingClientRect()
    const at = { x: event.clientX - rect.left, y: event.clientY - rect.top }
    const point = pointAt({ left: node.scrollLeft, top: node.scrollTop }, at, from)
    if (lodFor(next) === lodFor(from)) pendingHold.current = { point, at }
    else pendingPath.current = nearestBoxPath(layoutRef.current, point)
    onZoomChange(next)
  }
  node.addEventListener('wheel', onWheel, { passive: false })
  return () => node.removeEventListener('wheel', onWheel)
}, [onZoomChange])
```

Everything it reads is a ref that render keeps current (`zoomRef:225`, `layoutRef:229`), so the listener is subscribed once and never re-bound per zoom step.

**The placement effect** (`:288-316`) gains one branch, read and cleared exactly where `pendingPath` is (`:294-295`):

```ts
const hold = pendingHold.current
pendingHold.current = null
if (hold) {
  const to = anchorScroll(hold.point, hold.at, zoom,
    { width: node.clientWidth, height: node.clientHeight }, sizeRef.current)
  node.scrollTo({ left: to.left, top: to.top })
  return                                   // the point the wheel was over stays put
}
```

Before paint, so the mouse-centred zoom never visibly jumps and corrects — the same reason the existing body is a layout effect.

**Render**, five edits:

| Line | Change |
|---|---|
| `:639` | container div: `className={\`h-full w-full overflow-auto bg-app ${panning ? 'cursor-grabbing' : spaceHeld ? 'cursor-grab' : ''}\`}` plus `onPointerDown={startPan} onPointerMove={movePan} onPointerUp={endPan} onPointerCancel={endPan}` |
| `:644` | svg: add `style={{ pointerEvents: panning \|\| spaceHeld ? 'none' : undefined }}` |
| `:677-682` | background rect: add `className="cursor-grab"` — empty space advertises that it can be grabbed; during a pan the svg is transparent so the container's `grabbing` is what shows |
| `:709-713` | box wrapper `<g>`: add `data-box={box.path}` (a constant string prop; the wrapper is not memoised, so this costs nothing) |
| `:565-574` | `enter`: guard becomes `if (drag.current \|\| pan.current) return` |

**Untouched on purpose:** `startDrag`/`moveDrag`/`endDrag`, `swallowClick`, `clearSelection`, `clearDragFlag`, `FileBox`, `FileLinks`, `Minimap.tsx`, `SurfacePane.tsx`, `App.tsx`, `theme.css`, all Python. `Minimap` needs no change at all — `scrollLeft` assignment fires `scroll`, which is what its listener is already for (`Minimap.tsx:88`).

## 4. `desktop/src/domains/graph-view/layout.test.ts` — new tests

Appended in the file's voice (a `describe` per function, assertions phrased as facts):

- **`panScroll`** — the canvas follows the hand (a pointer moving right lowers `scrollLeft`); a pan that has not moved is where it began.
- **`pointAt`** — half the zoom is twice the distance into the canvas; a zoom of zero reads as one rather than dividing by it.
- **`anchorScroll`** — the point asked for lands under the place asked for; the top-left corner never scrolls negative; the far corner never scrolls past `size*z - view`; a canvas smaller than the window has nowhere to go; **`centerScroll` is `anchorScroll` at the middle** (asserted directly, so the rewrite is proven rather than assumed).
- **round trip** — `pointAt` at `z0` then `anchorScroll` at the same `z0` returns the scroll it started from (the zoom-about-a-point invariant, away from the clamps).
- **`zoomBy`** — a notch up zooms in and a notch down zooms out; a hundred notches in either direction stay inside `[ZOOM_MIN, ZOOM_MAX]`; `deltaMode: 1` moves further than `deltaMode: 0` for the same number; a `NaN` delta is the zoom it was; **wheeling out from 1 crosses `LOD_FILE_MAX` and `lodFor` says `'file'`** — the far view is reachable by wheel, in numbers.

## 5. Verification

| Command | What it proves | Note |
|---|---|---|
| `npx --prefix desktop tsc --noEmit -p desktop` | the slice's declared `test_commands` (`tasks/graph-pan.md:4`) | ⚠ `desktop/tsconfig.json` is a solution file (`"files": []` + references); without `-b` this checks **zero files**. Treat it as the gate that must pass, not as the typecheck. |
| `npm run typecheck --prefix desktop` | the real one — `typecheck:node` + `typecheck:web`; `tsconfig.web.json` is what covers `src/domains/**` and the `.tsx` | run if `npm` is permitted for this slice |
| `npm test --prefix desktop` | typecheck + `tsc -p tsconfig.test.json` + `node --test out-test/**/*.test.js` — the only thing that compiles and runs `layout.test.ts` (it is `exclude`d from `tsconfig.web.json:14`) | run if permitted; if not, say so plainly in the report rather than claiming the new tests ran |
| `python -m pytest -q` | no Python file is touched; this is a regression gate only | |

Manual, in `npm run dev` (report what was actually seen, not what should happen):
1. Drag bare canvas → the whole view moves with the hand; the minimap rectangle tracks it live.
2. Drag a file box → it still moves alone, curves following, exactly as before; the view does not pan.
3. Hold space over a box and drag → the view pans, the box does not move; cursor is `grab` on hold and `grabbing` during the drag, **including over rows**.
4. Release space mid-hold / alt-tab while holding → pan disarms.
5. Wheel over a specific box at 100% → the box under the cursor stays under the cursor; the `%` readout moves continuously; `±` still steps from wherever the wheel left it.
6. Wheel out past 55% → the file-box view arrives with the box that was under the cursor centred.
7. Click (no movement) on bare canvas → still clears the selection. Click a row after a pan → still selects.
8. Type a space in the search box → text, not pan. Space with a toolbar button focused → the button, not pan.

## 6. What could go wrong

1. **React's passive wheel listener.** `onWheel` cannot `preventDefault`; the canvas would scroll *and* zoom. Mitigated by the native `{passive: false}` listener — if a "Unable to preventDefault inside passive event listener" warning ever appears, that is this bug.
2. **Three things want to own the scroll.** The placement effect (`:288`), the selection effect (`:323`) and the wheel all write scroll on a zoom change. Resolved by `pendingHold` and by the fact that `layout` is referentially stable inside one LOD band, so the selection effect does not re-run on a same-band wheel. If a mouse-centred zoom is seen snapping back to the selection, this is where to look.
3. **Continuous zoom at the `file` level re-renders every box.** `labelScale = 1/zoom` (`:425`) is a `FileBox` prop only in that band, so 105 boxes reconcile per wheel event there (4 elements each — cheap, but it is the one place the memo does not hold). If it janks: coalesce wheel deltas onto one `requestAnimationFrame`, or quantise `labelScale`. Do not reach for either pre-emptively.
4. **`pointer-events: none` while space is held suppresses hover marks and row clicks.** Intended and conventional, but it is a behaviour change worth naming in the report.
5. **Space armed during a live node drag** is deliberately ignored (the keydown guard). The user must re-press after releasing the box. Cheaper than reasoning about whether a `pointer-events: none` element keeps its pointer capture across engines.
6. **Wheel no longer scrolls.** Deliberate (decision 5). Remaining ways to move: drag-pan, the minimap, the scrollbars, search/selection jumps. If a reviewer wants wheel-scroll back, the one-line variant is `if (!event.ctrlKey) return` in the wheel handler — but that contradicts the requirement as written.
7. **Both split panes share one `zoom`** (`App.tsx:293`, `:307`), so wheeling in one moves the other. Pre-existing (the `±` buttons already do this); out of scope, but say so rather than letting it read as new.
8. **Pan is bounded by the canvas** — you cannot push the graph past its own edges, because pan *is* scroll. This is a genuine limit of decision 1, and the right one: it keeps one definition of "where the reader is" for the minimap, `centerScroll` and the scrollbars.
9. **Touch/pen.** No `touch-action` change, so the browser keeps native touch scrolling and sends `pointercancel`, which ends the pan. Touch gestures stay out of scope, as required.

## 7. Explicitly not done

관성 스크롤, 터치 제스처, 키보드 이동, middle-button pan, persisted pan position, any change to the demo `GraphSurface`, any change to Python.
