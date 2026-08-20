/**
 * The placement, the links and the search, at the size this repository is.
 *
 * 105 files and 1435 functions is the measured shape of this graph today (78
 * and 1335 when the layout was first written, which is why both sizes appear
 * below), so that is what everything here is checked at: every function has
 * somewhere to be, no two boxes are drawn on top of each other, and pointing at
 * the most-called function in the repository stays a Map lookup.
 */
import { describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import {
  BOX_W,
  COLUMN_H,
  DRAG_SLOP,
  EDGE_STYLES,
  EDGE_W_MAX,
  EDGE_W_MIN,
  FILE_BOX_H,
  KEY_FN_PER_FILE,
  LANE_MAX,
  LOD_FILE_MAX,
  LOD_KEY_MAX,
  MAX_FILE_EDGES,
  MINIMAP_H,
  MINIMAP_W,
  NO_OFFSETS,
  ROW_H,
  ZOOM_MAX,
  ZOOM_MIN,
  ZOOM_STEPS,
  anchorScroll,
  boxAnchor,
  boxCenter,
  boxHeight,
  buildAdjacency,
  callerCounts,
  canvasSize,
  centerScroll,
  clampOffset,
  edgePath,
  edgeStyle,
  edgeWidth,
  fileLines,
  fitChars,
  flattenFunctions,
  groupMarks,
  isDrag,
  layoutGraph,
  lodFor,
  markColour,
  matchFunctions,
  mergeMarks,
  minimapFit,
  nearestBoxPath,
  neighbourMarks,
  offsetOf,
  offsetsByBox,
  panScroll,
  pointAt,
  resolveAnchor,
  shiftAnchor,
  viewportOf,
  visibleRows,
  withOffset,
  zoomBy,
  zoomIn,
  zoomOut,
  type Offset,
  type Offsets,
  type RowMark
} from './layout'
import type { GraphEdge, GraphFileEdge, GraphFileGroup, GraphFunction } from './types'

let nextId = 1

function fn(name: string, path: string, overrides: Partial<GraphFunction> = {}): GraphFunction {
  const id = nextId++
  return {
    id,
    qualname: name,
    name,
    path,
    lang: 'py',
    lineno: id,
    endLineno: id + 3,
    signature: `${name}()`,
    kind: 'function',
    summary: 'does a thing',
    hasSpec: true,
    isTest: false,
    ...overrides
  }
}

/**
 * A repository the size of this one, with one file as big as pipeline.py.
 *
 * @param fileCount  how many files
 * @param total      how many functions in all of them
 */
function bigRepo(fileCount = 78, total = 1335): GraphFileGroup[] {
  const files: GraphFileGroup[] = []
  let made = 0
  for (let i = 0; i < fileCount; i++) {
    const path = `pkg${String(i).padStart(2, '0')}/module_${i}.py`
    const count = i === 0 ? 207 : Math.max(1, Math.round((total - 207) / (fileCount - 1)))
    const functions: GraphFunction[] = []
    for (let j = 0; j < count && made < total; j++) {
      functions.push(fn(`fn_${i}_${j}`, path))
      made += 1
    }
    files.push({ path, lang: 'py', functions })
  }
  return files
}

/** Three small files in one column — enough for a file link to have two ends. */
function threeFiles(): GraphFileGroup[] {
  return ['a.py', 'b.py', 'c.py'].map((path) => ({
    path,
    lang: 'py',
    functions: [fn(`${path.slice(0, 1)}_one`, path)]
  }))
}

describe('layoutGraph', () => {
  test('an empty graph has no canvas at all', () => {
    const layout = layoutGraph([])
    assert.equal(layout.boxes.length, 0)
    assert.equal(layout.width, 0)
    assert.equal(layout.anchors.size, 0)
  })

  test('one box per file, one anchor per function, nothing lost', () => {
    const files = bigRepo()
    const layout = layoutGraph(files)
    assert.equal(layout.boxes.length, 78)
    const total = flattenFunctions(files).length
    assert.equal(layout.anchors.size, total)
    for (const f of flattenFunctions(files)) assert.ok(layout.anchors.has(f.id))
  })

  test('no two boxes overlap', () => {
    const layout = layoutGraph(bigRepo())
    for (let i = 0; i < layout.boxes.length; i++) {
      for (let j = i + 1; j < layout.boxes.length; j++) {
        const a = layout.boxes[i]
        const b = layout.boxes[j]
        const apart =
          a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y
        assert.ok(apart, `boxes ${a.path} and ${b.path} overlap`)
      }
    }
  })

  test('every anchor sits inside its own box', () => {
    const layout = layoutGraph(bigRepo())
    for (const [, anchor] of layout.anchors) {
      const box = layout.boxes[anchor.boxIndex]
      assert.equal(anchor.left, box.x)
      assert.equal(anchor.right, box.x + BOX_W)
      assert.ok(anchor.y > box.y && anchor.y < box.y + box.height)
    }
  })

  test('a column fills before the next one starts', () => {
    const files: GraphFileGroup[] = []
    for (let i = 0; i < 6; i++) {
      files.push({
        path: `f${i}.py`,
        lang: 'py',
        functions: Array.from({ length: 30 }, (_, j) => fn(`a${i}_${j}`, `f${i}.py`))
      })
    }
    const layout = layoutGraph(files)
    const columns = new Set(layout.boxes.map((b) => b.x))
    assert.ok(columns.size >= 2, 'six tall boxes should not fit in one column')
    assert.ok(layout.boxes[0].x === 0 && layout.boxes[0].y === 0)
  })

  test('a file taller than a whole column still gets drawn whole', () => {
    const huge = Array.from({ length: 400 }, (_, j) => fn(`big_${j}`, 'huge.py'))
    const layout = layoutGraph([{ path: 'huge.py', lang: 'py', functions: huge }])
    assert.equal(layout.boxes.length, 1)
    assert.equal(layout.boxes[0].height, boxHeight(400))
    assert.ok(layout.boxes[0].height > COLUMN_H)
    assert.equal(layout.anchors.size, 400)
  })

  test('every file knows which box is its own', () => {
    const files = bigRepo()
    const layout = layoutGraph(files)
    assert.equal(layout.boxOf.size, layout.boxes.length)
    for (const file of files) {
      const at = layout.boxOf.get(file.path)
      assert.ok(at !== undefined)
      assert.equal(layout.boxes[at].path, file.path)
    }
  })
})

describe('edgePath', () => {
  test('a left-to-right edge leaves the source and lands on the target', () => {
    const d = edgePath({ left: 0, right: 100, y: 10 }, { left: 300, right: 400, y: 50 })
    assert.match(d, /^M 100 10 C /)
    assert.match(d, /300 50$/)
  })

  test('a target that is not to the right routes around instead of cutting back', () => {
    const d = edgePath({ left: 0, right: 100, y: 10 }, { left: 0, right: 100, y: 200 })
    assert.match(d, /100 200$/)
  })

  test('however tall the drop, the detour never invades the next column', () => {
    // pipeline.py's box is 3700px tall. Without the clamp the curve would swing
    // hundreds of pixels right, straight through whatever is drawn there.
    const d = edgePath({ left: 0, right: 100, y: 0 }, { left: 0, right: 100, y: 100000 })
    const control = Number(d.split('C ')[1].split(' ')[0])
    assert.ok(control > 100, 'the detour still leaves the box')
    assert.ok(control <= 100 + 16 + LANE_MAX, `lane ${control} passed the limit`)
  })
})

describe('boxAnchor', () => {
  test('a file link attaches to the middle of the box’s two sides', () => {
    const layout = layoutGraph(threeFiles())
    for (const box of layout.boxes) {
      const anchor = boxAnchor(box)
      assert.equal(anchor.left, box.x)
      assert.equal(anchor.right, box.x + box.width)
      assert.equal(anchor.y, box.y + box.height / 2)
    }
  })
})

describe('edgeWidth', () => {
  test('heavier is never thinner, and nothing leaves the range', () => {
    let last = 0
    for (let w = 0; w <= 300; w++) {
      const width = edgeWidth(w, 300)
      assert.ok(width >= EDGE_W_MIN && width <= EDGE_W_MAX, `${w} -> ${width}`)
      assert.ok(width >= last, `${w} came out thinner than ${w - 1}`)
      last = width
    }
  })

  test('the heaviest link reaches the maximum', () => {
    assert.equal(edgeWidth(300, 300), EDGE_W_MAX)
  })

  test('nothing to compare against is the minimum, not a NaN', () => {
    for (const [w, heaviest] of [
      [1, 1],
      [0, 0],
      [5, 0],
      [0, 10]
    ]) {
      const width = edgeWidth(w, heaviest)
      assert.ok(Number.isFinite(width))
      assert.equal(width, EDGE_W_MIN)
    }
  })
})

describe('fileLines', () => {
  const edges: GraphFileEdge[] = [
    { from: 'a.py', to: 'b.py', weight: 9 },
    { from: 'b.py', to: 'c.py', weight: 4 },
    { from: 'c.py', to: 'a.py', weight: 1 },
    { from: 'a.py', to: 'gone.py', weight: 99 }
  ]

  test('a pair naming a file with no box is dropped without a word', () => {
    const layout = layoutGraph(threeFiles())
    const links = fileLines(edges, layout)
    assert.equal(links.total, 3)
    assert.equal(links.shown, 3)
    assert.ok(!links.lines.some((line) => line.to === 'gone.py'))
  })

  test('the heaviest links are the ones drawn, and the count says so', () => {
    const layout = layoutGraph(threeFiles())
    const links = fileLines(edges, layout, 2)
    assert.equal(links.total, 3)
    assert.equal(links.shown, 2)
    assert.deepEqual(
      links.lines.map((line) => line.weight),
      [9, 4]
    )
    assert.equal(links.lines[0].width, EDGE_W_MAX)
    assert.ok(links.lines[1].width < links.lines[0].width)
  })

  test('each curve leaves its own box and reaches the other one', () => {
    const layout = layoutGraph(threeFiles())
    for (const line of fileLines(edges, layout).lines) {
      const from = layout.boxes[line.fromBox]
      const to = layout.boxes[line.toBox]
      assert.equal(from.path, line.from)
      assert.equal(to.path, line.to)
      assert.ok(line.d.startsWith(`M ${from.x + from.width} `))
      assert.equal(line.d, edgePath(boxAnchor(from), boxAnchor(to)))
    }
  })

  test('nothing to draw is an empty answer, not a crash', () => {
    const links = fileLines([], layoutGraph([]))
    assert.deepEqual(links.lines, [])
    assert.equal(links.total, 0)
  })
})

describe('buildAdjacency', () => {
  const edges: GraphEdge[] = [
    { from: 1, to: 2 },
    { from: 1, to: 3 },
    { from: 4, to: 2 }
  ]

  test('both directions stand, and an id nobody named has nothing', () => {
    const adj = buildAdjacency(edges)
    assert.deepEqual(adj.outs.get(1), [2, 3])
    assert.deepEqual((adj.ins.get(2) ?? []).slice().sort(), [1, 4])
    assert.equal(adj.outs.get(2), undefined)
    assert.equal(adj.ins.get(99), undefined)
  })

  test('an empty graph builds an empty adjacency', () => {
    const adj = buildAdjacency([])
    assert.equal(adj.outs.size, 0)
    assert.equal(adj.ins.size, 0)
  })
})

describe('neighbourMarks', () => {
  const adj = buildAdjacency([
    { from: 1, to: 2 },
    { from: 1, to: 3 },
    { from: 4, to: 1 },
    { from: 2, to: 1 }
  ])

  test('which way the call goes is what the mark says', () => {
    const marks = neighbourMarks(adj, 1)
    assert.equal(marks.get(1), 'hover')
    assert.equal(marks.get(3), 'calls')
    assert.equal(marks.get(4), 'callers')
    // 2 is called by 1 *and* calls 1 back.
    assert.equal(marks.get(2), 'both')
  })

  test('a function nobody touches marks only itself', () => {
    const marks = neighbourMarks(adj, 77)
    assert.equal(marks.size, 1)
    assert.equal(marks.get(77), 'hover')
  })

  test('a very popular function stops at the limit', () => {
    const many: GraphEdge[] = []
    for (let i = 2; i < 500; i++) many.push({ from: i, to: 1 })
    const marks = neighbourMarks(buildAdjacency(many), 1, 10)
    // ten neighbours plus the node itself.
    assert.equal(marks.size, 11)
  })
})

describe('mergeMarks', () => {
  test('a selection outranks a hover', () => {
    const base = new Map<number, RowMark>([[1, 'selected']])
    const merged = mergeMarks(base, new Map<number, RowMark>([[1, 'hover']]))
    assert.equal(merged.get(1), 'selected')
  })

  test('calls meeting callers becomes both, whichever side it arrives from', () => {
    const a = mergeMarks(
      new Map<number, RowMark>([[1, 'calls']]),
      new Map<number, RowMark>([[1, 'callers']])
    )
    const b = mergeMarks(
      new Map<number, RowMark>([[1, 'callers']]),
      new Map<number, RowMark>([[1, 'calls']])
    )
    assert.equal(a.get(1), 'both')
    assert.equal(b.get(1), 'both')
  })

  test('neither argument is touched', () => {
    const base = new Map<number, RowMark>([[1, 'near']])
    const extra = new Map<number, RowMark>([[1, 'calls'], [2, 'hover']])
    const merged = mergeMarks(base, extra)
    assert.equal(base.get(1), 'near')
    assert.equal(extra.size, 2)
    assert.equal(merged.get(1), 'calls')
    assert.equal(merged.get(2), 'hover')
  })
})

describe('groupMarks', () => {
  test('a box with nothing marked is not a key at all', () => {
    const layout = layoutGraph(threeFiles())
    const first = [...layout.anchors.keys()][0]
    const grouped = groupMarks(layout.anchors, new Map<number, RowMark>([[first, 'calls']]))
    assert.equal(grouped.size, 1)
    assert.equal(grouped.get(0)?.get(first), 'calls')
    assert.equal(grouped.get(1), undefined)
    assert.equal(grouped.get(2), undefined)
    // An id nobody placed is skipped rather than inventing a box for it.
    const stray = groupMarks(layout.anchors, new Map<number, RowMark>([[first + 90000, 'near']]))
    assert.equal(stray.size, 0)
  })

  test('rows fold under the box that holds them', () => {
    const files = threeFiles()
    const layout = layoutGraph(files)
    const marks = new Map<number, RowMark>()
    for (const file of files) marks.set(file.functions[0].id, 'near')
    const grouped = groupMarks(layout.anchors, marks)
    assert.equal(grouped.size, 3)
    for (const [boxIndex, rows] of grouped) {
      assert.equal(rows.size, 1)
      assert.ok(boxIndex >= 0 && boxIndex < layout.boxes.length)
    }
  })
})

describe('at the size this repository actually is', () => {
  test('1435 functions, 6000 edges: pointing at the busiest one still answers', () => {
    const files = bigRepo(105, 1435)
    const layout = layoutGraph(files)
    assert.equal(layout.boxes.length, 105)

    const ids = flattenFunctions(files).map((f) => f.id)
    const hub = ids[0]
    const edges: GraphEdge[] = []
    for (let i = 1; i <= 150; i++) edges.push({ from: ids[i], to: hub })
    for (let i = 0; i < 6000; i++) {
      edges.push({ from: ids[i % ids.length], to: ids[(i * 7 + 3) % ids.length] })
    }

    const adj = buildAdjacency(edges)
    const marks = neighbourMarks(adj, hub)
    assert.ok(marks.size > 1)
    assert.equal(marks.get(hub), 'hover')

    const grouped = groupMarks(layout.anchors, marks)
    assert.ok(grouped.size > 0)
    // Only the boxes that hold a marked row are keys — that is the contract
    // `FileBox`'s memo relies on to leave the other hundred alone.
    assert.ok(grouped.size <= layout.boxes.length)
    let counted = 0
    for (const [, rows] of grouped) counted += rows.size
    assert.equal(counted, marks.size)
  })
})

// -------------------------------------------------------- scale of detail

describe('lodFor', () => {
  test('the thresholds are inclusive, and each band draws what it says', () => {
    assert.equal(lodFor(0.4), 'file')
    assert.equal(lodFor(LOD_FILE_MAX), 'file')
    assert.equal(lodFor(0.7), 'key')
    assert.equal(lodFor(LOD_KEY_MAX), 'key')
    assert.equal(lodFor(LOD_KEY_MAX + 0.01), 'full')
    assert.equal(lodFor(1), 'full')
    assert.equal(lodFor(1.8), 'full')
  })

  test('a zoom that is not a number reads as the coarsest level, not a crash', () => {
    assert.equal(lodFor(0), 'file')
    assert.equal(lodFor(-1), 'file')
    assert.equal(lodFor(Number.NaN), 'file')
  })
})

describe('zoomIn / zoomOut', () => {
  test('one rung at a time, and both ends hold', () => {
    assert.equal(zoomIn(1), ZOOM_STEPS[ZOOM_STEPS.indexOf(1) + 1])
    assert.equal(zoomOut(1), ZOOM_STEPS[ZOOM_STEPS.indexOf(1) - 1])
    assert.equal(zoomIn(ZOOM_STEPS[ZOOM_STEPS.length - 1]), ZOOM_STEPS[ZOOM_STEPS.length - 1])
    assert.equal(zoomOut(ZOOM_STEPS[0]), ZOOM_STEPS[0])
  })

  test('a zoom that is not on the ladder starts from the nearest rung', () => {
    // 0.63 is nearer 0.7 than 0.5, so climbing from it lands above 0.7.
    assert.equal(zoomIn(0.63), 0.85)
    assert.equal(zoomOut(0.63), 0.5)
  })

  test('twice out of the whole view is the file view — the reason for the two new rungs', () => {
    assert.equal(lodFor(1), 'full')
    assert.equal(lodFor(zoomOut(1)), 'key')
    assert.equal(lodFor(zoomOut(zoomOut(1))), 'key')
    assert.equal(lodFor(zoomOut(zoomOut(zoomOut(1)))), 'file')
  })
})

describe('callerCounts', () => {
  test('each function is counted once per distinct caller', () => {
    const counts = callerCounts([
      { from: 1, to: 2 },
      { from: 3, to: 2 },
      { from: 1, to: 4 }
    ])
    assert.equal(counts.get(2), 2)
    assert.equal(counts.get(4), 1)
    // Nobody calls 1, and an id nobody calls is not a key at all.
    assert.equal(counts.get(1), undefined)
    assert.equal(counts.size, 2)
  })

  test('no edges is an empty count, not a crash', () => {
    assert.equal(callerCounts([]).size, 0)
  })
})

describe('visibleRows', () => {
  const counts = new Map<number, number>()

  test('the full level hands back the very array it was given', () => {
    const rows = [fn('a', 'x.py'), fn('b', 'x.py')]
    // Referential identity, not deep equality: this is what `FileBox`'s memo
    // rests on, and a copy here would quietly wake a hundred boxes a frame.
    assert.equal(visibleRows(rows, 'full', counts), rows)
  })

  test('the file level is always the same empty array', () => {
    const first = visibleRows([fn('a', 'x.py')], 'file', counts)
    const second = visibleRows([fn('b', 'y.py')], 'file', counts)
    assert.equal(first.length, 0)
    assert.equal(first, second)
  })

  test('a file shorter than the cut is handed straight back', () => {
    const rows = Array.from({ length: KEY_FN_PER_FILE }, (_, i) => fn(`a${i}`, 'x.py'))
    assert.equal(visibleRows(rows, 'key', counts), rows)
  })

  test('the middle level keeps the most-called, in the order they are written', () => {
    const rows = Array.from({ length: 10 }, (_, i) => fn(`a${i}`, 'x.py'))
    const weights = new Map<number, number>()
    // The last three are the busiest, in reverse order of appearance.
    weights.set(rows[9].id, 30)
    weights.set(rows[8].id, 20)
    weights.set(rows[7].id, 10)
    const kept = visibleRows(rows, 'key', weights, 3)
    assert.equal(kept.length, 3)
    assert.deepEqual(
      kept.map((each) => each.name),
      ['a7', 'a8', 'a9']
    )
  })

  test('nobody calls anybody: the file’s own order decides', () => {
    const rows = Array.from({ length: 10 }, (_, i) => fn(`a${i}`, 'x.py'))
    const kept = visibleRows(rows, 'key', counts, 3)
    assert.deepEqual(
      kept.map((each) => each.name),
      ['a0', 'a1', 'a2']
    )
  })
})

describe('layoutGraph at each level', () => {
  test('the file level places boxes and no rows at all', () => {
    const files = bigRepo(105, 1435)
    const layout = layoutGraph(files, 'file')
    assert.equal(layout.boxes.length, 105)
    // No anchors means no function edges to draw — the requirement, in numbers.
    assert.equal(layout.anchors.size, 0)
    for (const box of layout.boxes) {
      assert.equal(box.height, FILE_BOX_H)
      assert.equal(box.functions.length, 0)
      assert.equal(box.hidden, box.total)
    }
    assert.equal(layout.boxOf.size, 105)
  })

  test('the counts a header prints are the file’s own, whatever the level', () => {
    const files: GraphFileGroup[] = [
      {
        path: 'a.py',
        lang: 'py',
        functions: [
          fn('one', 'a.py'),
          fn('two', 'a.py', { hasSpec: false, summary: '' }),
          fn('test_three', 'a.py', { isTest: true })
        ]
      }
    ]
    for (const lod of ['file', 'key', 'full'] as const) {
      const box = layoutGraph(files, lod).boxes[0]
      assert.equal(box.total, 3)
      assert.equal(box.specTotal, 2)
      assert.equal(box.specCovered, 1)
    }
  })

  test('the middle level draws the cut rows and counts the rest', () => {
    const files = bigRepo(105, 1435)
    const counts = callerCounts([])
    const layout = layoutGraph(files, 'key', counts)
    for (const box of layout.boxes) {
      assert.ok(box.functions.length <= KEY_FN_PER_FILE)
      assert.equal(box.hidden, box.total - box.functions.length)
    }
    // Only the drawn rows have anchors; every file still has a box.
    let drawn = 0
    for (const box of layout.boxes) drawn += box.functions.length
    assert.equal(layout.anchors.size, drawn)
    assert.equal(layout.boxOf.size, files.length)
    for (const file of files) assert.ok(layout.boxOf.has(file.path))
  })

  test('zooming out really does shrink the map — the whole point of the slice', () => {
    const files = bigRepo(105, 1435)
    const full = layoutGraph(files, 'full')
    const file = layoutGraph(files, 'file')
    assert.ok(
      file.width * 3 < full.width,
      `file view ${file.width} is not a third of ${full.width}`
    )
    assert.ok(file.height <= COLUMN_H, `file view is ${file.height} tall`)
  })

  test('the default arguments are the view that was there before', () => {
    const files = bigRepo()
    const before = layoutGraph(files)
    assert.equal(before.anchors.size, flattenFunctions(files).length)
    assert.equal(before.boxes[0].functions, files[0].functions)
  })
})

describe('boxHeight at each level', () => {
  test('the file level is one fixed card, however big the file', () => {
    assert.equal(boxHeight(0, 'file'), FILE_BOX_H)
    assert.equal(boxHeight(207, 'file', 207), FILE_BOX_H)
  })

  test('a cut list is one line taller, for the "+N more" it has to say', () => {
    assert.equal(boxHeight(6, 'key', 0) + ROW_H, boxHeight(6, 'key', 40))
  })

  test('the old call is the old answer', () => {
    assert.equal(boxHeight(400), 30 + 400 * 18 + 8)
  })
})

describe('fitChars', () => {
  test('a bigger label fits fewer characters', () => {
    assert.ok(fitChars(BOX_W, 12) > fitChars(BOX_W, 30))
  })

  test('however narrow the box, a name is never cut to nothing', () => {
    assert.equal(fitChars(0, 12), 4)
    assert.equal(fitChars(BOX_W, 0), 4)
    assert.ok(fitChars(20, 40) >= 4)
  })
})

// ------------------------------------------------------------- the viewport

describe('centerScroll', () => {
  const view = { width: 800, height: 600 }
  const size = { width: 4000, height: 3000 }

  test('the point asked for ends up in the middle', () => {
    const at = centerScroll({ x: 1000, y: 900 }, 1, view, size)
    assert.equal(at.left + view.width / 2, 1000)
    assert.equal(at.top + view.height / 2, 900)
  })

  test('the top left corner does not scroll to a negative place', () => {
    const at = centerScroll({ x: 0, y: 0 }, 1, view, size)
    assert.deepEqual(at, { left: 0, top: 0 })
  })

  test('the far corner does not scroll past what there is', () => {
    const at = centerScroll({ x: 99999, y: 99999 }, 1, view, size)
    assert.equal(at.left, size.width - view.width)
    assert.equal(at.top, size.height - view.height)
  })

  test('a canvas smaller than the window has nowhere to scroll', () => {
    const at = centerScroll({ x: 50, y: 50 }, 1, view, { width: 100, height: 80 })
    assert.deepEqual(at, { left: 0, top: 0 })
  })

  test('the zoom is what user units are measured in', () => {
    const at = centerScroll({ x: 1000, y: 900 }, 0.5, view, size)
    assert.equal(at.left + view.width / 2, 500)
  })
})

describe('viewportOf', () => {
  test('half the zoom is twice the canvas on screen', () => {
    const port = viewportOf({ left: 100, top: 50, width: 800, height: 600 }, 0.5)
    assert.deepEqual(port, { x: 200, y: 100, w: 1600, h: 1200 })
  })

  test('a zoom of zero is read as one rather than dividing by it', () => {
    const port = viewportOf({ left: 10, top: 20, width: 30, height: 40 }, 0)
    assert.deepEqual(port, { x: 10, y: 20, w: 30, h: 40 })
  })
})

describe('nearestBoxPath', () => {
  test('the middle of a box is that box', () => {
    const layout = layoutGraph(threeFiles())
    for (const box of layout.boxes) {
      assert.equal(nearestBoxPath(layout, boxCenter(box, { dx: 0, dy: 0 })), box.path)
    }
  })

  test('a point off the canvas still names the box nearest to it', () => {
    const layout = layoutGraph(threeFiles())
    assert.equal(nearestBoxPath(layout, { x: -9999, y: -9999 }), layout.boxes[0].path)
    const last = layout.boxes[layout.boxes.length - 1]
    assert.equal(nearestBoxPath(layout, { x: 9999, y: 99999 }), last.path)
  })

  test('an empty canvas names nothing', () => {
    assert.equal(nearestBoxPath(layoutGraph([]), { x: 0, y: 0 }), null)
  })
})

describe('boxCenter', () => {
  test('a dragged box’s middle moves with it', () => {
    const box = layoutGraph(threeFiles()).boxes[0]
    assert.deepEqual(boxCenter(box, { dx: 10, dy: 20 }), {
      x: box.x + 10 + box.width / 2,
      y: box.y + 20 + box.height / 2
    })
  })
})

describe('minimapFit', () => {
  test('a wide canvas fits the width, a tall one the height', () => {
    const wide = minimapFit({ width: 4000, height: 800 })
    assert.equal(Math.round(wide.width), MINIMAP_W)
    assert.ok(wide.height <= MINIMAP_H)

    const tall = minimapFit({ width: 800, height: 6000 })
    assert.equal(Math.round(tall.height), MINIMAP_H)
    assert.ok(tall.width <= MINIMAP_W)
  })

  test('nothing ever leaves the box the map is allowed', () => {
    for (const size of [
      { width: 6808, height: 1400 },
      { width: 2044, height: 1400 },
      { width: 50, height: 20 }
    ]) {
      const fit = minimapFit(size)
      assert.ok(fit.width <= MINIMAP_W + 0.001)
      assert.ok(fit.height <= MINIMAP_H + 0.001)
    }
  })

  test('an empty canvas is a zero-sized map, not a NaN', () => {
    const fit = minimapFit({ width: 0, height: 0 })
    assert.deepEqual(fit, { scale: 0, width: 0, height: 0 })
    assert.ok(Number.isFinite(minimapFit({ width: 100, height: 0 }).scale))
  })
})

// ---------------------------------------------------- panning and the wheel

describe('panScroll', () => {
  test('the canvas follows the hand: dragging right shows what is on the left', () => {
    const at = panScroll({ left: 300, top: 200 }, { dx: 40, dy: 25 })
    assert.deepEqual(at, { left: 260, top: 175 })
  })

  test('a pan that has not moved is where it began', () => {
    assert.deepEqual(panScroll({ left: 300, top: 200 }, { dx: 0, dy: 0 }), { left: 300, top: 200 })
  })

  test('client pixels on both sides — the zoom is not in this sum', () => {
    // Whatever the scale, one pixel of hand is one pixel of scroll; it is the
    // *contents* that are already drawn at the zoom.
    assert.deepEqual(panScroll({ left: 0, top: 0 }, { dx: -100, dy: -50 }), { left: 100, top: 50 })
  })
})

describe('pointAt', () => {
  test('what is under the pointer is the scroll plus the offset, in user units', () => {
    assert.deepEqual(pointAt({ left: 200, top: 100 }, { x: 50, y: 20 }, 1), { x: 250, y: 120 })
  })

  test('half the zoom is twice the distance into the canvas', () => {
    assert.deepEqual(pointAt({ left: 200, top: 100 }, { x: 50, y: 20 }, 0.5), { x: 500, y: 240 })
  })

  test('a zoom of zero is read as one rather than dividing by it', () => {
    assert.deepEqual(pointAt({ left: 10, top: 20 }, { x: 1, y: 2 }, 0), { x: 11, y: 22 })
  })
})

describe('anchorScroll', () => {
  const view = { width: 800, height: 600 }
  const size = { width: 4000, height: 3000 }

  test('the point asked for lands where it was asked to land', () => {
    const at = anchorScroll({ x: 1000, y: 900 }, { x: 120, y: 400 }, 1, view, size)
    assert.equal(at.left + 120, 1000)
    assert.equal(at.top + 400, 900)
  })

  test('the top left corner does not scroll to a negative place', () => {
    assert.deepEqual(anchorScroll({ x: 0, y: 0 }, { x: 700, y: 500 }, 1, view, size), {
      left: 0,
      top: 0
    })
  })

  test('the far corner does not scroll past what there is', () => {
    const at = anchorScroll({ x: 99999, y: 99999 }, { x: 10, y: 10 }, 1, view, size)
    assert.equal(at.left, size.width - view.width)
    assert.equal(at.top, size.height - view.height)
  })

  test('a canvas smaller than the window has nowhere to go', () => {
    const at = anchorScroll({ x: 50, y: 50 }, { x: 0, y: 0 }, 1, view, { width: 100, height: 80 })
    assert.deepEqual(at, { left: 0, top: 0 })
  })

  test('centerScroll is this, anchored at the middle', () => {
    for (const point of [
      { x: 1000, y: 900 },
      { x: 0, y: 0 },
      { x: 99999, y: 99999 }
    ]) {
      for (const zoom of [0.4, 1, 1.8]) {
        assert.deepEqual(
          centerScroll(point, zoom, view, size),
          anchorScroll(point, { x: view.width / 2, y: view.height / 2 }, zoom, view, size)
        )
      }
    }
  })

  test('reading a point and asking for it back is the scroll it came from', () => {
    // The zoom-about-a-point invariant, away from the clamps: this is what makes
    // a wheel notch keep what is under the cursor under the cursor.
    const scroll = { left: 900, top: 700 }
    const at = { x: 320, y: 240 }
    const point = pointAt(scroll, at, 1.25)
    assert.deepEqual(anchorScroll(point, at, 1.25, view, size), scroll)
  })
})

describe('zoomBy', () => {
  test('a notch up zooms in and a notch down zooms out', () => {
    assert.ok(zoomBy(1, -100) > 1)
    assert.ok(zoomBy(1, 100) < 1)
  })

  test('a notch is the same proportion wherever the ladder is standing', () => {
    const near = zoomBy(1, -100) / 1
    const far = zoomBy(0.5, -100) / 0.5
    assert.ok(Math.abs(near - far) < 1e-9, `${near} is not ${far}`)
  })

  test('a hundred notches either way stay inside the ladder', () => {
    let up = 1
    let down = 1
    for (let i = 0; i < 100; i++) {
      up = zoomBy(up, -100)
      down = zoomBy(down, 100)
    }
    assert.equal(up, ZOOM_MAX)
    assert.equal(down, ZOOM_MIN)
    assert.equal(ZOOM_MIN, ZOOM_STEPS[0])
    assert.equal(ZOOM_MAX, ZOOM_STEPS[ZOOM_STEPS.length - 1])
  })

  test('a zoom already off the ladder is brought back onto it', () => {
    assert.equal(zoomBy(9, 0), ZOOM_MAX)
    assert.equal(zoomBy(0.01, 0), ZOOM_MIN)
  })

  test('lines and pages move further than pixels do', () => {
    assert.ok(zoomBy(1, -3, 1) > zoomBy(1, -3, 0))
    assert.ok(zoomBy(1, -3, 2) > zoomBy(1, -3, 1))
  })

  test('a delta that is not a number is the zoom it was', () => {
    assert.equal(zoomBy(1, Number.NaN), 1)
    assert.equal(zoomBy(1, Number.POSITIVE_INFINITY), 1)
  })

  test('the far view is reachable by wheel, in numbers', () => {
    // Six notches out of 100% crosses LOD_FILE_MAX — the level change is
    // something the wheel can actually get to, not only the buttons.
    let zoom = 1
    for (let i = 0; i < 6; i++) zoom = zoomBy(zoom, 100)
    assert.ok(zoom <= LOD_FILE_MAX, `${zoom} never got past ${LOD_FILE_MAX}`)
    assert.equal(lodFor(zoom), 'file')
  })
})

describe('resolveAnchor', () => {
  const files = threeFiles()
  const byId = new Map(flattenFunctions(files).map((each) => [each.id, each]))
  const noBox = new Map<number, Offset>()

  test('a drawn row is its own row', () => {
    const layout = layoutGraph(files, 'full')
    const id = files[1].functions[0].id
    const at = resolveAnchor(layout, byId, noBox, id)
    assert.deepEqual(at, layout.anchors.get(id))
  })

  test('a row this level does not draw falls back to its file’s box', () => {
    const layout = layoutGraph(files, 'file')
    const id = files[1].functions[0].id
    assert.equal(layout.anchors.has(id), false)
    const box = layout.boxes[layout.boxOf.get('b.py') as number]
    assert.deepEqual(resolveAnchor(layout, byId, noBox, id), boxAnchor(box))
  })

  test('a function this index never had is null, not a guess', () => {
    const layout = layoutGraph(files, 'file')
    assert.equal(resolveAnchor(layout, byId, noBox, 999999), null)
  })

  test('either way, a dragged box takes its anchor with it', () => {
    const id = files[2].functions[0].id
    for (const lod of ['full', 'file'] as const) {
      const layout = layoutGraph(files, lod)
      const at = layout.boxOf.get('c.py') as number
      const byBox = new Map<number, Offset>([[at, { dx: 15, dy: -5 }]])
      const moved = resolveAnchor(layout, byId, byBox, id)
      const still = resolveAnchor(layout, byId, noBox, id)
      assert.ok(moved && still)
      assert.equal(moved.left, still.left + 15)
      assert.equal(moved.y, still.y - 5)
    }
  })
})

describe('matchFunctions', () => {
  const functions = [
    fn('run_pipeline', 'aidev/pipeline.py'),
    fn('run_pipeline_stage', 'aidev/pipeline.py'),
    fn('rerun', 'aidev/cli.py'),
    fn('Slice.run_pipeline', 'aidev/epic.py', { name: 'run_pipeline', qualname: 'Slice.run_pipeline' }),
    fn('build', 'aidev/graph/build.py')
  ]

  test('a blank query matches nothing at all', () => {
    assert.deepEqual(matchFunctions(functions, ''), [])
    assert.deepEqual(matchFunctions(functions, '   '), [])
  })

  test('exact name first, then prefix, then anywhere', () => {
    const names = matchFunctions(functions, 'run_pipeline').map((f) => f.qualname)
    assert.equal(names[0], 'run_pipeline')
    assert.ok(names.includes('run_pipeline_stage'))
    assert.ok(names.includes('Slice.run_pipeline'))
  })

  test('case is ignored and partial matches count', () => {
    assert.ok(matchFunctions(functions, 'PIPE').length >= 2)
    assert.equal(matchFunctions(functions, 'RUN')[0].name.startsWith('run'), true)
  })

  test('the limit is honoured and the order is stable', () => {
    const once = matchFunctions(functions, 'run', 2)
    const twice = matchFunctions(functions, 'run', 2)
    assert.equal(once.length, 2)
    assert.deepEqual(
      once.map((f) => f.id),
      twice.map((f) => f.id)
    )
  })

  test('a path fragment finds a file’s functions when no name matches', () => {
    assert.ok(matchFunctions(functions, 'graph/build').length > 0)
  })
})

describe('isDrag', () => {
  test('a hand that barely moved was pointing, not dragging', () => {
    assert.equal(isDrag(0, 0), false)
    assert.equal(isDrag(DRAG_SLOP, DRAG_SLOP), false)
    assert.equal(isDrag(-DRAG_SLOP, -DRAG_SLOP), false)
  })

  test('past the slop on either axis, in either direction, it is a drag', () => {
    assert.equal(isDrag(DRAG_SLOP + 1, 0), true)
    assert.equal(isDrag(0, DRAG_SLOP + 1), true)
    assert.equal(isDrag(-(DRAG_SLOP + 1), 0), true)
    assert.equal(isDrag(0, -(DRAG_SLOP + 1)), true)
  })

  test('the slop can be said out loud', () => {
    assert.equal(isDrag(5, 0, 10), false)
    assert.equal(isDrag(5, 0, 1), true)
  })
})

describe('offsetOf', () => {
  test('a box nobody touched is at the origin, and always the same origin', () => {
    const at = offsetOf(NO_OFFSETS, 'a.py')
    assert.deepEqual(at, { dx: 0, dy: 0 })
    // The same object every time: this is read once per link per frame, and a
    // fresh allocation there is 240 of them a frame for no new fact.
    assert.equal(offsetOf(NO_OFFSETS, 'b.py'), at)
  })

  test('a box that was dragged says where it went', () => {
    const offsets = withOffset(NO_OFFSETS, 'a.py', { dx: 12, dy: -4 })
    assert.deepEqual(offsetOf(offsets, 'a.py'), { dx: 12, dy: -4 })
  })
})

describe('withOffset', () => {
  test('the map handed in is never the map handed back', () => {
    const first = withOffset(NO_OFFSETS, 'a.py', { dx: 1, dy: 2 })
    assert.equal(NO_OFFSETS.size, 0)
    assert.equal(first.size, 1)
    const second = withOffset(first, 'b.py', { dx: 3, dy: 4 })
    assert.equal(first.size, 1)
    assert.equal(second.size, 2)
  })

  test('dragging the same box twice leaves where it ended, not the way there', () => {
    let offsets: Offsets = NO_OFFSETS
    offsets = withOffset(offsets, 'a.py', { dx: 5, dy: 5 })
    offsets = withOffset(offsets, 'a.py', { dx: 50, dy: 0 })
    assert.equal(offsets.size, 1)
    assert.deepEqual(offsetOf(offsets, 'a.py'), { dx: 50, dy: 0 })
  })
})

describe('clampOffset', () => {
  test('a box cannot be pushed off the top or the left, where it would be cut', () => {
    const layout = layoutGraph(threeFiles())
    const box = layout.boxes[1]
    const at = clampOffset(box, { dx: -9999, dy: -9999 })
    assert.equal(box.x + at.dx, 0)
    assert.equal(box.y + at.dy, 0)
  })

  test('right and down are free — that is what "move it anywhere" means', () => {
    const box = layoutGraph(threeFiles()).boxes[0]
    assert.deepEqual(clampOffset(box, { dx: 4000, dy: 9000 }), { dx: 4000, dy: 9000 })
  })
})

describe('shiftAnchor', () => {
  const anchor = { left: 10, right: 110, y: 50 }

  test('both sides and the row move by exactly the offset', () => {
    assert.deepEqual(shiftAnchor(anchor, { dx: 7, dy: -3 }), { left: 17, right: 117, y: 47 })
  })

  test('an untouched box hands back the anchor it was given', () => {
    assert.equal(shiftAnchor(anchor, { dx: 0, dy: 0 }), anchor)
  })
})

describe('offsetsByBox', () => {
  test('only the dragged boxes are keys, and a path with no box is dropped', () => {
    const layout = layoutGraph(threeFiles())
    let offsets: Offsets = withOffset(NO_OFFSETS, 'b.py', { dx: 20, dy: 5 })
    offsets = withOffset(offsets, 'gone.py', { dx: 1, dy: 1 })
    const byBox = offsetsByBox(layout, offsets)
    assert.equal(byBox.size, 1)
    const at = layout.boxOf.get('b.py') as number
    assert.deepEqual(byBox.get(at), { dx: 20, dy: 5 })
  })

  test('at this repository’s size, one dragged box costs one entry', () => {
    const layout = layoutGraph(bigRepo(105, 1435))
    const offsets = withOffset(NO_OFFSETS, layout.boxes[40].path, { dx: 300, dy: 100 })
    assert.equal(offsetsByBox(layout, offsets).size, 1)
    assert.equal(offsetsByBox(layout, NO_OFFSETS).size, 0)
  })
})

describe('canvasSize', () => {
  test('nothing dragged is the placement’s own size, to the pixel', () => {
    const layout = layoutGraph(bigRepo(105, 1435))
    assert.deepEqual(canvasSize(layout, NO_OFFSETS), {
      width: layout.width,
      height: layout.height
    })
  })

  test('a box dragged right and down takes the canvas with it', () => {
    const layout = layoutGraph(threeFiles())
    const box = layout.boxes[0]
    const size = canvasSize(layout, withOffset(NO_OFFSETS, box.path, { dx: 900, dy: 700 }))
    assert.equal(size.width, box.x + 900 + box.width)
    assert.equal(size.height, box.y + 700 + box.height)
  })

  test('a box dragged left never shrinks the canvas onto the others', () => {
    const layout = layoutGraph(threeFiles())
    const last = layout.boxes[layout.boxes.length - 1]
    const size = canvasSize(layout, withOffset(NO_OFFSETS, last.path, { dx: 0, dy: -last.y }))
    assert.equal(size.width, layout.width)
    assert.equal(size.height, layout.height)
  })
})

describe('fileLines with dragged boxes', () => {
  const edges: GraphFileEdge[] = [
    { from: 'a.py', to: 'b.py', weight: 9 },
    { from: 'b.py', to: 'c.py', weight: 4 }
  ]

  test('a link out of a dragged box leaves it where it now is', () => {
    const layout = layoutGraph(threeFiles())
    const box = layout.boxes[layout.boxOf.get('a.py') as number]
    const offsets = withOffset(NO_OFFSETS, 'a.py', { dx: 40, dy: 25 })
    const drawn = fileLines(edges, layout, MAX_FILE_EDGES, offsets).lines.filter(
      (each) => each.from === 'a.py'
    )
    assert.equal(drawn.length, 1)
    const moved = shiftAnchor(boxAnchor(box), { dx: 40, dy: 25 })
    assert.ok(drawn[0].d.startsWith(`M ${moved.right} ${moved.y} `))
  })

  test('a link into a dragged box lands on it where it now is', () => {
    const layout = layoutGraph(threeFiles())
    const target = layout.boxes[layout.boxOf.get('c.py') as number]
    const offsets = withOffset(NO_OFFSETS, 'c.py', { dx: 0, dy: 60 })
    const drawn = fileLines(edges, layout, MAX_FILE_EDGES, offsets).lines.filter(
      (each) => each.to === 'c.py'
    )
    assert.equal(drawn.length, 1)
    const from = boxAnchor(layout.boxes[layout.boxOf.get('b.py') as number])
    assert.equal(drawn[0].d, edgePath(from, shiftAnchor(boxAnchor(target), { dx: 0, dy: 60 })))
  })

  test('with nothing dragged the curves are the ones that were there before', () => {
    const layout = layoutGraph(threeFiles())
    const before = fileLines(edges, layout)
    const after = fileLines(edges, layout, MAX_FILE_EDGES, NO_OFFSETS)
    assert.deepEqual(
      after.lines.map((line) => line.d),
      before.lines.map((line) => line.d)
    )
  })
})

// out-test/domains/graph-view -> desktop/, then into the renderer's styles.
// Tied to tsconfig.test.json's rootDir/outDir on purpose: if those move, this
// fails loudly rather than silently stopping checking anything.
const THEME = readFileSync(resolve(__dirname, '../../../src/renderer/src/styles/theme.css'), 'utf8')

/**
 * Is this `var(--color-…)` token actually declared in the theme?
 *
 * @param token  the exact string the layout hands to a `stroke` or a `fill`
 */
function defined(token: string): boolean {
  const name = token.slice('var('.length, -1)
  return THEME.includes(name + ':')
}

describe('edge style, one table for the edges, the row marks and the legend', () => {
  test('an outgoing edge is drawn as calls, an incoming one as callers', () => {
    assert.equal(edgeStyle(false), EDGE_STYLES.calls)
    assert.equal(edgeStyle(true), EDGE_STYLES.callers)
  })

  test('the two kinds differ by more than colour', () => {
    assert.notEqual(EDGE_STYLES.calls.stroke, EDGE_STYLES.callers.stroke)
    assert.equal(EDGE_STYLES.calls.dash, null)
    assert.ok(EDGE_STYLES.callers.dash)
  })

  test('each kind names its own arrowhead', () => {
    assert.notEqual(EDGE_STYLES.calls.marker, EDGE_STYLES.callers.marker)
    assert.ok(EDGE_STYLES.calls.marker.length > 0)
    assert.ok(EDGE_STYLES.callers.marker.length > 0)
  })

  test('a row mark is painted the colour of the edge it stands for', () => {
    assert.equal(markColour('calls'), EDGE_STYLES.calls.stroke)
    assert.equal(markColour('selected'), EDGE_STYLES.calls.stroke)
    assert.equal(markColour('callers'), EDGE_STYLES.callers.stroke)
  })

  test('the marks that stand for no edge are still neutral', () => {
    assert.equal(markColour('hover'), 'var(--color-fg-dim)')
    assert.equal(markColour('near'), 'var(--color-fg-mute)')
  })

  test('every colour the graph asks for exists in the theme', () => {
    const marks: RowMark[] = ['selected', 'calls', 'callers', 'both', 'hover', 'near']
    assert.ok(defined(EDGE_STYLES.calls.stroke))
    assert.ok(defined(EDGE_STYLES.callers.stroke))
    for (const mark of marks) {
      assert.ok(defined(markColour(mark)), mark + ' asks for a token theme.css does not define')
    }
  })
})
