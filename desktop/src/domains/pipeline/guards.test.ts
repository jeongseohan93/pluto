/**
 * ★ The guard that exists because of the 2026-08-18 gen2b accident.
 *
 * The Done Criteria ask for "merge 실패 → discard 방지 가드 동작 확인". This is
 * that check, in the form a machine can repeat: the button's enabled state is a
 * pure function, so the accident can be replayed here rather than by hand.
 */
import { describe, test } from 'node:test'
import assert from 'node:assert/strict'

import { discardGuard } from './guards'
import type { CommandResult, SliceMerge } from './types'
import type { SliceState } from '../../shared/ide'

function slice(overrides: Partial<SliceState> = {}): SliceState {
  return {
    id: 's1',
    dir: '/repo/.aidev/slices/s1',
    status: 'done',
    waitingStage: null,
    stages: [],
    gates: [],
    updatedAt: null,
    reason: null,
    testVerdict: null,
    epic: null,
    live: null,
    merge: null,
    quota: null,
    stopped: null,
    unreadable: false,
    ...overrides
  }
}

function merge(overrides: Partial<SliceMerge> = {}): SliceMerge {
  return {
    status: 'merged',
    at: '2026-08-19T10:00:00+09:00',
    branch: 'slice/s1',
    base: 'master',
    commit: '1234567890abcdef',
    conflicts: [],
    push: null,
    ...overrides
  }
}

function mergeRun(overrides: Partial<CommandResult> = {}): CommandResult {
  return { kind: 'merge', sliceId: 's1', exitCode: 0, error: null, at: 1, ...overrides }
}

describe('discardGuard', () => {
  test('★ a merge this session watched fail disables discard', () => {
    const guard = discardGuard(slice(), mergeRun({ exitCode: 2 }))
    assert.equal(guard.allowed, false)
    assert.equal(guard.tone, 'block')
    assert.match(guard.reason, /never merged/)
  })

  test('★ a merge that never even started (spawn error) disables discard', () => {
    const guard = discardGuard(slice(), mergeRun({ exitCode: null, error: 'ENOENT' }))
    assert.equal(guard.allowed, false)
    assert.equal(guard.tone, 'block')
  })

  test('a recorded conflict disables discard and names the files', () => {
    const guard = discardGuard(
      slice({ merge: merge({ status: 'conflict', commit: null, conflicts: ['aidev/pipeline.py'] }) }),
      null
    )
    assert.equal(guard.allowed, false)
    assert.equal(guard.tone, 'block')
    assert.match(guard.reason, /aidev\/pipeline\.py/)
  })

  test('a merged slice may be discarded, with no second click', () => {
    const guard = discardGuard(slice({ status: 'merged', merge: merge() }), null)
    assert.equal(guard.allowed, true)
    assert.equal(guard.confirm, false)
    assert.equal(guard.tone, 'ok')
    assert.match(guard.reason, /master/)
  })

  test('merged with a failed push is allowed, but says what is missing', () => {
    const guard = discardGuard(
      slice({
        status: 'merged',
        merge: merge({ push: { status: 'failed', remote: 'origin', error: 'no remote' } })
      }),
      null
    )
    assert.equal(guard.allowed, true)
    assert.equal(guard.tone, 'warn')
    assert.match(guard.reason, /only the backup/)
  })

  test('the disk wins over the exit code: --push failing exits non-zero, the merge stands', () => {
    const guard = discardGuard(
      slice({
        status: 'merged',
        merge: merge({ push: { status: 'failed', remote: 'origin', error: 'no remote' } })
      }),
      mergeRun({ exitCode: 1 })
    )
    assert.equal(guard.allowed, true)
    assert.equal(guard.tone, 'warn')
  })

  test('a slice that was never merged is allowed, behind two clicks', () => {
    const guard = discardGuard(slice({ status: 'failed' }), null)
    assert.equal(guard.allowed, true)
    assert.equal(guard.confirm, true)
    assert.equal(guard.tone, 'warn')
    assert.match(guard.reason, /never been merged/)
  })

  test('a failed merge of a different slice does not lock this one', () => {
    const guard = discardGuard(slice(), mergeRun({ sliceId: 'other', exitCode: 2 }))
    assert.equal(guard.allowed, true)
    assert.equal(guard.confirm, true)
  })

  test('a failed discard is not a failed merge', () => {
    const guard = discardGuard(slice({ merge: merge() }), {
      kind: 'discard',
      sliceId: 's1',
      exitCode: 2,
      error: null,
      at: 1
    })
    assert.equal(guard.allowed, true)
  })

  test('no slice selected is not a button at all', () => {
    assert.equal(discardGuard(null, null).allowed, false)
  })
})
