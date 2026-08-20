/**
 * The split arithmetic: the two floors, the row too narrow to honour both, and
 * the round trip from a pointer position back into a legal share.
 *
 * The floors matter more than the happy path. A drag can put the pointer
 * anywhere — outside the row, past either edge — and the panel that survives
 * that has to be one somebody can still read.
 */
import { describe, test } from 'node:test'
import assert from 'node:assert/strict'

import {
  CODE_MIN_PX,
  GRAPH_MIN_PX,
  SPLIT_DEFAULT,
  clampSplit,
  splitFrom
} from './split'

describe('clampSplit', () => {
  test('a roomy row gives back exactly what was asked', () => {
    assert.equal(clampSplit(0.45, 1000), 0.45)
    assert.equal(clampSplit(0.5, 1600), 0.5)
  })

  test('the code panel has a floor', () => {
    assert.equal(clampSplit(0.05, 1000), CODE_MIN_PX / 1000)
  })

  test('so does the graph', () => {
    assert.equal(clampSplit(0.95, 1000), 1 - GRAPH_MIN_PX / 1000)
  })

  test('a row too narrow for both floors is halved, not starved', () => {
    // 500px cannot hold 360 + 320. Every request lands on the same answer.
    for (const asked of [0, 0.1, 0.45, 0.9, 1]) {
      assert.equal(clampSplit(asked, 500), 0.5)
    }
  })

  test('a share that is not a number is the default', () => {
    assert.equal(clampSplit(NaN, 1000), SPLIT_DEFAULT)
    assert.equal(clampSplit(Infinity, 1000), SPLIT_DEFAULT)
  })

  test('an unmeasured row still bounds the share loosely', () => {
    assert.equal(clampSplit(0.9, 0), 0.8)
    assert.equal(clampSplit(0.05, 0), 0.2)
    assert.equal(clampSplit(0.45, NaN), 0.45)
  })

  test('the range never inverts, at any width', () => {
    for (let width = 1; width <= 2000; width += 1) {
      const lo = clampSplit(0, width)
      const hi = clampSplit(1, width)
      assert.ok(lo <= hi, `lo ${lo} > hi ${hi} at ${width}px`)
      assert.ok(lo >= 0 && hi <= 1, `out of 0..1 at ${width}px`)
    }
  })

  test('the default is not clipped on a normal-sized row', () => {
    assert.equal(clampSplit(SPLIT_DEFAULT, 1000), SPLIT_DEFAULT)
  })
})

describe('splitFrom', () => {
  test('the distance from the right edge, as a share', () => {
    assert.equal(splitFrom(700, { right: 1000, width: 1000 }), 0.3)
    assert.equal(splitFrom(1000, { right: 1000, width: 1000 }), 0)
  })

  test('leftward is a wider code panel', () => {
    const row = { right: 1200, width: 900 }
    let previous = -Infinity
    for (let x = 1200; x >= 300; x -= 25) {
      const share = splitFrom(x, row)
      assert.ok(share > previous, `not monotone at x=${x}`)
      previous = share
    }
  })

  test('an unmeasured row answers the default', () => {
    assert.equal(splitFrom(500, { right: 0, width: 0 }), SPLIT_DEFAULT)
  })

  test('a pointer dragged past either edge still clamps into the row', () => {
    const row = { right: 1000, width: 1000 }
    for (const x of [-500, 0, 500, 1000, 1500]) {
      const share = clampSplit(splitFrom(x, row), row.width)
      assert.ok(share >= CODE_MIN_PX / 1000, `code starved at x=${x}: ${share}`)
      assert.ok(share <= 1 - GRAPH_MIN_PX / 1000, `graph starved at x=${x}: ${share}`)
    }
  })
})
