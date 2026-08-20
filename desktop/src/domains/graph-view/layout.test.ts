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

import {
  BOX_W,
  COLUMN_H,
  DRAG_SLOP,
  EDGE_W_MAX,
  EDGE_W_MIN,
  LANE_MAX,
  MAX_FILE_EDGES,
  NO_OFFSETS,
  boxAnchor,
  boxHeight,
  buildAdjacency,
  canvasSize,
  clampOffset,
  edgePath,
  edgeWidth,
  fileLines,
  flattenFunctions,
  groupMarks,
  isDrag,
  layoutGraph,
  matchFunctions,
  mergeMarks,
  neighbourMarks,
  offsetOf,
  offsetsByBox,
  shiftAnchor,
  withOffset,
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
