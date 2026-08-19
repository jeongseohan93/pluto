/**
 * The wiring, checked against the five command lines the requirement names.
 *
 * These assertions are deliberately literal. `commandArgv` is the only place a
 * renderer's string becomes a process argument, so what it produces is checked
 * word for word rather than "shaped like" a command line.
 */
import { describe, test } from 'node:test'
import assert from 'node:assert/strict'

import {
  MAX_STAGE_TURNS,
  commandArgv,
  commandLabel,
  commandSliceId,
  isRequirementPath,
  isSafeSegment,
  isStageTurns,
  resultOf
} from './commands'
import type { CommandRequest, CommandState } from './types'

const ROOT = '/repo'

describe('commandArgv', () => {
  test('launch is --requirement, relative to the repo it runs in', () => {
    assert.deepEqual(commandArgv(ROOT, { kind: 'pipeline', requirement: 'tasks/x.md' }), [
      'pipeline',
      '--repo',
      ROOT,
      '--requirement',
      'tasks/x.md'
    ])
  })

  test('resume without a turn budget adds no flag', () => {
    assert.deepEqual(commandArgv(ROOT, { kind: 'resume', sliceId: '20260819-x' }), [
      'pipeline',
      '--repo',
      ROOT,
      '--resume-slice',
      '20260819-x'
    ])
  })

  test('resume with turns raises implement only', () => {
    assert.deepEqual(
      commandArgv(ROOT, { kind: 'resume', sliceId: '20260819-x', implementTurns: 160 }),
      ['pipeline', '--repo', ROOT, '--resume-slice', '20260819-x', '--max-turns-stage', 'implement=160']
    )
  })

  test('merge, and merge with a push', () => {
    assert.deepEqual(commandArgv(ROOT, { kind: 'merge', sliceId: 's1', push: false }), [
      'pipeline',
      '--repo',
      ROOT,
      '--merge',
      's1'
    ])
    assert.deepEqual(commandArgv(ROOT, { kind: 'merge', sliceId: 's1', push: true }), [
      'pipeline',
      '--repo',
      ROOT,
      '--merge',
      's1',
      '--push'
    ])
  })

  test('discard', () => {
    assert.deepEqual(commandArgv(ROOT, { kind: 'discard', sliceId: 's1' }), [
      'pipeline',
      '--repo',
      ROOT,
      '--discard',
      's1'
    ])
  })

  test('graph build is the graph verb, not a pipeline flag', () => {
    assert.deepEqual(commandArgv(ROOT, { kind: 'graph-build' }), ['graph', 'build', '--repo', ROOT])
  })

  test('no repository is a refusal, not a command against the cwd', () => {
    assert.throws(() => commandArgv('', { kind: 'graph-build' }))
  })

  test('an unknown kind is refused rather than defaulted', () => {
    assert.throws(() => commandArgv(ROOT, { kind: 'rm -rf' } as unknown as CommandRequest))
  })
})

describe('the renderer cannot smuggle anything through', () => {
  const badRequirements = [
    '../../etc/passwd',
    'tasks/../../etc/passwd',
    'tasks/x.md; rm -rf /',
    'tasks/x.md && curl evil',
    '/abs/tasks/x.md',
    'tasks/sub/x.md',
    'tasks/x.txt',
    'x.md',
    '',
    'tasks/.md'
  ]
  for (const value of badRequirements) {
    test(`requirement refused: ${JSON.stringify(value)}`, () => {
      assert.equal(isRequirementPath(value), false)
      assert.throws(() => commandArgv(ROOT, { kind: 'pipeline', requirement: value }))
    })
  }

  const badSlices = ['..', '../other', 'a/b', '', '-flag', 'a b', 's1;rm', '.hidden']
  for (const value of badSlices) {
    test(`slice id refused: ${JSON.stringify(value)}`, () => {
      assert.equal(isSafeSegment(value), false)
      assert.throws(() => commandArgv(ROOT, { kind: 'discard', sliceId: value }))
      assert.throws(() => commandArgv(ROOT, { kind: 'merge', sliceId: value, push: false }))
      assert.throws(() => commandArgv(ROOT, { kind: 'resume', sliceId: value }))
    })
  }

  test('a real slice id and a real requirement path pass', () => {
    assert.equal(isSafeSegment('20260819-ui-baseline-graphview'), true)
    assert.equal(isRequirementPath('tasks/ui-baseline-graphview.md'), true)
  })
})

describe('turn budgets', () => {
  test('whole numbers 1..1000 only', () => {
    assert.equal(isStageTurns(1), true)
    assert.equal(isStageTurns(160), true)
    assert.equal(isStageTurns(MAX_STAGE_TURNS), true)
    for (const bad of [0, -1, 1.5, MAX_STAGE_TURNS + 1, NaN, Infinity, '160']) {
      assert.equal(isStageTurns(bad as number), false)
    }
  })

  test('a bad budget refuses the whole command rather than dropping the flag', () => {
    assert.throws(() =>
      commandArgv(ROOT, { kind: 'resume', sliceId: 's1', implementTurns: 0 })
    )
    assert.throws(() =>
      commandArgv(ROOT, { kind: 'resume', sliceId: 's1', implementTurns: 5000 })
    )
  })
})

describe('labels and slice ids', () => {
  test('every kind says what it is', () => {
    assert.match(commandLabel({ kind: 'pipeline', requirement: 'tasks/x.md' }), /tasks\/x\.md/)
    assert.match(commandLabel({ kind: 'resume', sliceId: 's1', implementTurns: 9 }), /implement=9/)
    assert.match(commandLabel({ kind: 'merge', sliceId: 's1', push: true }), /push/)
    assert.match(commandLabel({ kind: 'discard', sliceId: 's1' }), /discard/)
    assert.equal(commandLabel({ kind: 'graph-build' }), 'build graph')
  })

  test('only the slice-shaped commands name a slice', () => {
    assert.equal(commandSliceId({ kind: 'merge', sliceId: 's1', push: false }), 's1')
    assert.equal(commandSliceId({ kind: 'graph-build' }), null)
    assert.equal(commandSliceId({ kind: 'pipeline', requirement: 'tasks/x.md' }), null)
  })
})

describe('resultOf', () => {
  test('carries the kind, the slice and how it ended', () => {
    const state: CommandState = {
      id: 'cmd-1',
      request: { kind: 'merge', sliceId: 's1', push: true },
      label: 'merge s1',
      argv: [],
      startedAt: 1,
      finishedAt: 2,
      running: false,
      exitCode: 1,
      signal: null,
      error: null,
      seq: 0,
      lines: []
    }
    assert.deepEqual(resultOf(state), {
      kind: 'merge',
      sliceId: 's1',
      exitCode: 1,
      error: null,
      at: 2
    })
  })
})
