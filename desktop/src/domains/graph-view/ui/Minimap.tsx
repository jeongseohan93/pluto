import {
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
  type JSX,
  type PointerEvent as ReactPointerEvent,
  type RefObject
} from 'react'
import {
  MINIMAP_H,
  MINIMAP_W,
  centerScroll,
  minimapFit,
  offsetOf,
  viewportOf,
  type GraphLayout,
  type Offsets,
  type Viewport
} from '@domains/graph-view/layout'
import { Icon } from '@renderer/components/Icon'

const NO_VIEW: Viewport = { x: 0, y: 0, w: 0, h: 0 }

/**
 * The canvas from above, with a rectangle where the reader actually is.
 *
 * **It subscribes to the scroll itself.** Holding the viewport rectangle in the
 * surface's state would re-render 105 wrapper `<g>` elements on every scroll
 * frame for the sake of one `<rect>`. So the container's ref is handed down and
 * the listener lives here, with the boxes split off behind their own `memo`:
 * what changes as the reader scrolls is four attributes on one rectangle.
 *
 * @param scrollRef  the canvas's scroll container
 * @param layout     the placement, whose boxes are what the map draws
 * @param offsets    the boxes that have been dragged, which move here too
 * @param size       the canvas, in user units
 * @param zoom       the surface's zoom factor
 * @param open       is the map unfolded?
 * @param onToggle   fold it away, or bring it back
 * @flow  an empty graph draws nothing at all ; folded -> one icon button ;
 *        otherwise the overview, the viewport rectangle, and a pointer that
 *        moves the canvas wherever it is put down or dragged
 * 주요 내부 변수: fit(캔버스를 미니맵에 담는 배율), port(현재 뷰포트, user 좌표)
 */
export function Minimap({
  scrollRef,
  layout,
  offsets,
  size,
  zoom,
  open,
  onToggle
}: {
  scrollRef: RefObject<HTMLDivElement | null>
  layout: GraphLayout
  offsets: Offsets
  size: { width: number; height: number }
  zoom: number
  open: boolean
  onToggle: () => void
}): JSX.Element | null {
  const [port, setPort] = useState<Viewport>(NO_VIEW)
  const paper = useRef<SVGSVGElement>(null)
  const fit = minimapFit(size)

  // The listener alone is not enough: a zoom or a level change moves the
  // viewport without scrolling anything, so the rectangle is re-read whenever
  // what it is measured against changes.
  useEffect(() => {
    const node = scrollRef.current
    if (!node || !open) return
    const read = (): void => {
      setPort(
        viewportOf(
          {
            left: node.scrollLeft,
            top: node.scrollTop,
            width: node.clientWidth,
            height: node.clientHeight
          },
          zoom
        )
      )
    }
    read()
    node.addEventListener('scroll', read, { passive: true })
    return () => node.removeEventListener('scroll', read)
  }, [scrollRef, open, zoom, size.width, size.height])

  /**
   * Put the middle of the view where the pointer is on the map.
   *
   * @param event  a pointerdown or a pointermove over the overview
   */
  const goTo = useCallback(
    (event: ReactPointerEvent<SVGSVGElement>): void => {
      const node = scrollRef.current
      const box = paper.current
      if (!node || !box || !(fit.scale > 0)) return
      const rect = box.getBoundingClientRect()
      const to = centerScroll(
        { x: (event.clientX - rect.left) / fit.scale, y: (event.clientY - rect.top) / fit.scale },
        zoom,
        { width: node.clientWidth, height: node.clientHeight },
        size
      )
      // No smooth behaviour: a dragged rectangle has to be under the finger
      // that is dragging it, not 300ms behind it.
      node.scrollTo({ left: to.left, top: to.top })
    },
    [scrollRef, fit.scale, zoom, size]
  )

  const grab = useCallback(
    (event: ReactPointerEvent<SVGSVGElement>): void => {
      if (event.button !== 0) return
      event.preventDefault()
      event.currentTarget.setPointerCapture(event.pointerId)
      goTo(event)
    },
    [goTo]
  )

  const drag = useCallback(
    (event: ReactPointerEvent<SVGSVGElement>): void => {
      if ((event.buttons & 1) === 0) return
      goTo(event)
    },
    [goTo]
  )

  const release = useCallback((event: ReactPointerEvent<SVGSVGElement>): void => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }, [])

  if (!(fit.scale > 0)) return null

  if (!open) {
    return (
      <button
        type="button"
        onClick={onToggle}
        title="Show minimap"
        aria-label="Show minimap"
        aria-expanded={false}
        className="absolute right-2 bottom-2 rounded-sm border border-line bg-panel/90 p-1 text-fg-mute hover:text-fg-dim"
      >
        <Icon name="graph" size={13} />
      </button>
    )
  }

  return (
    <div
      className="absolute right-2 bottom-2 rounded-sm border border-line bg-panel/90"
      style={{ width: fit.width + 2, maxWidth: MINIMAP_W + 2 }}
    >
      <div className="flex h-5 items-center justify-between px-1">
        <span className="font-mono text-micro text-fg-mute">map</span>
        <button
          type="button"
          onClick={onToggle}
          title="Collapse minimap"
          aria-label="Collapse minimap"
          aria-expanded={true}
          className="text-fg-mute hover:text-fg-dim"
        >
          <Icon name="chevron" size={11} className="rotate-90" />
        </button>
      </div>
      <svg
        ref={paper}
        width={fit.width}
        height={fit.height}
        viewBox={`0 0 ${Math.max(1, size.width)} ${Math.max(1, size.height)}`}
        className="block cursor-crosshair select-none"
        style={{ maxHeight: MINIMAP_H, touchAction: 'none' }}
        onPointerDown={grab}
        onPointerMove={drag}
        onPointerUp={release}
        onPointerCancel={release}
      >
        <MinimapBoxes layout={layout} offsets={offsets} scale={fit.scale} />
        <rect
          x={port.x}
          y={port.y}
          width={Math.max(1, port.w)}
          height={Math.max(1, port.h)}
          fill="var(--color-accent-soft)"
          fillOpacity={0.25}
          stroke="var(--color-accent)"
          strokeWidth={1 / fit.scale}
          pointerEvents="none"
        />
      </svg>
    </div>
  )
}

/**
 * Every box of the placement, at map size.
 *
 * Memoised away from the viewport rectangle: this is a hundred rectangles that
 * change only when the layout or a drag does, and the rectangle above it changes
 * on every scroll frame.
 *
 * @param layout   the placement
 * @param offsets  the boxes that have been dragged
 * @param scale    the map's scale, for a stroke that stays one pixel wide
 * @flow  one rectangle per box, where that box now is
 */
const MinimapBoxes = memo(function MinimapBoxes({
  layout,
  offsets,
  scale
}: {
  layout: GraphLayout
  offsets: Offsets
  scale: number
}): JSX.Element {
  const hair = scale > 0 ? 1 / scale : 1
  return (
    <g pointerEvents="none">
      {layout.boxes.map((box) => {
        const off = offsetOf(offsets, box.path)
        return (
          <rect
            key={box.path}
            x={box.x + off.dx}
            y={box.y + off.dy}
            width={box.width}
            height={box.height}
            fill="var(--color-raised)"
            stroke="var(--color-line)"
            strokeWidth={hair}
          />
        )
      })}
    </g>
  )
})
