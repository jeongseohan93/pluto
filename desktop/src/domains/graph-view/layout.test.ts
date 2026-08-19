/**
 * The placement and the search, at the size this repository actually is.
 *
 * 78 files and 1335 functions is the measured shape of Pluto's own graph, so
 * that is what the layout is checked at: every function has somewhere to be,
 * and no two boxes are drawn on top of each other.
 */
import { describe, test } from 'node:test'
import assert from 'node:assert/strict'

import { BOX_W, COLUMN_H, boxHeight, edgePath, flattenFunctions, layoutGraph, matchFunctions } from './layout'
import type { GraphFileGroup, GraphFunction } from './types'

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

/** A repository the size of this one: 78 files, 1335 functions. */
function bigRepo(): GraphFileGroup[] {
  const files: GraphFileGroup[] = []
  let made = 0
  for (let i = 0; i < 78; i++) {
    const path = `pkg${String(i).padStart(2, '0')}/module_${i}.py`
    const count = i === 0 ? 207 : Math.max(1, Math.round((1335 - 207) / 77))
    const functions: GraphFunction[] = []
    for (let j = 0; j < count && made < 1335; j++) {
      functions.push(fn(`fn_${i}_${j}`, path))
      made += 1
    }
    files.push({ path, lang: 'py', functions })
  }
  return files
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
