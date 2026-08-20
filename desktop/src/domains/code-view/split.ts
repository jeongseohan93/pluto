/**
 * How the graph and the code share one row — arithmetic only, no React.
 *
 * It lives outside `ui/` for two reasons. `tsconfig.test.json` excludes
 * `ui/`, so this is the only place `node --test` can actually reach; and the
 * fraction is one fact read by two owners — the shell, which remembers it for
 * the session, and the graph surface, which drags it — so both must clamp it
 * the same way.
 */

/** The code panel's opening share — the ratio the fixed 34rem used to hold. */
export const SPLIT_DEFAULT = 0.45

/** The code panel's floor: narrower and the gutter crowds out the source. */
export const CODE_MIN_PX = 360

/** The graph's floor: narrower and not one column of file boxes fits. */
export const GRAPH_MIN_PX = 320

/** One arrow key. The divider has to be movable without a pointer. */
export const SPLIT_STEP = 0.02

/**
 * The share asked for, turned into a share both sides can stand in.
 *
 * When the row is narrower than the two floors together it is halved rather
 * than one side starved. `lo <= 0.5 <= hi` holds by construction, so the range
 * can never invert — which is why there is no error path here.
 *
 * @param fraction  the share the code panel is asking for (0..1)
 * @param rowWidth  the width of the row the two share, in client px. 0 is
 *                  allowed for a row nobody has measured yet — then only the
 *                  loose bounds apply
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
 * Where the pointer stands, as the code panel's share.
 *
 * The code panel's right edge *is* the row's right edge — the node panel sits
 * outside this row — so one rectangle is everything there is to measure. No
 * clamping here on purpose: `clampSplit` is the one place that cuts.
 *
 * @param clientX  the pointer's client x
 * @param row      the rectangle the graph and the code occupy (a DOMRect does)
 */
export function splitFrom(clientX: number, row: { right: number; width: number }): number {
  if (!(row.width > 0)) return SPLIT_DEFAULT
  return (row.right - clientX) / row.width
}
