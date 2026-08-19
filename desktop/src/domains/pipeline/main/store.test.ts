/**
 * The two reads the pipeline domain added, against the files the tool writes.
 *
 * Every fixture below is the shape `pipeline.py` actually produces — a slice
 * directory with state.json / runs.json / progress.md, and a run directory with
 * telemetry.json. The point of the tolerance tests is that a machine which does
 * not have the tool's `data/` on it (any machine but the one that ran the
 * slice) still gets a panel rather than an exception.
 */
import { after, describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

import { listRequirements, readSliceFailure } from './store'

const roots: string[] = []

after(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true })
})

function tempRoot(): string {
  const root = mkdtempSync(join(tmpdir(), 'pluto-slice-'))
  roots.push(root)
  return root
}

function writeJson(file: string, value: unknown): void {
  mkdirSync(join(file, '..'), { recursive: true })
  writeFileSync(file, JSON.stringify(value, null, 2), 'utf8')
}

/** A repo with one slice, whose files the caller supplies. */
function repoWithSlice(
  id: string,
  files: { state?: unknown; runs?: unknown; progress?: string }
): string {
  const root = tempRoot()
  const dir = join(root, '.aidev', 'slices', id)
  mkdirSync(dir, { recursive: true })
  if (files.state !== undefined) writeJson(join(dir, 'state.json'), files.state)
  if (files.runs !== undefined) writeJson(join(dir, 'runs.json'), files.runs)
  if (files.progress !== undefined) writeFileSync(join(dir, 'progress.md'), files.progress, 'utf8')
  return root
}

describe('listRequirements', () => {
  test('a repo with no tasks/ is an empty launcher, not an error', () => {
    assert.deepEqual(listRequirements(tempRoot()), [])
  })

  test('markdown only, by name, with the first heading as the title', () => {
    const root = tempRoot()
    mkdirSync(join(root, 'tasks'), { recursive: true })
    writeFileSync(join(root, 'tasks', 'b-second.md'), '---\napproval: plan\n---\n# Second\nbody\n')
    writeFileSync(join(root, 'tasks', 'a-first.md'), '# First thing\n')
    writeFileSync(join(root, 'tasks', 'notes.txt'), 'ignored')
    mkdirSync(join(root, 'tasks', 'sub'), { recursive: true })

    const found = listRequirements(root)
    assert.deepEqual(
      found.map((f) => f.path),
      ['tasks/a-first.md', 'tasks/b-second.md']
    )
    assert.equal(found[0].title, 'First thing')
    assert.equal(found[1].title, 'Second')
    assert.ok(found[0].bytes > 0)
  })

  test('a file with no heading falls back to its name', () => {
    const root = tempRoot()
    mkdirSync(join(root, 'tasks'), { recursive: true })
    writeFileSync(join(root, 'tasks', 'plain.md'), 'no heading here\n')
    assert.equal(listRequirements(root)[0].title, 'plain.md')
  })
})

describe('readSliceFailure', () => {
  test('an unreadable or absent slice is null, not a throw', () => {
    assert.equal(readSliceFailure(tempRoot(), 'nope'), null)
    assert.equal(readSliceFailure(tempRoot(), '../escape'), null)
  })

  test('the engine’s quota flag makes it a usage limit', () => {
    const root = repoWithSlice('s1', {
      state: {
        status: 'quota_wait',
        stage_order: ['plan', 'implement'],
        stages: { plan: { status: 'done' }, implement: { status: 'failed' } },
        quota: {
          waiting: true,
          resume_at: new Date(Date.now() + 90_000).toISOString(),
          retries: 2,
          source: 'reset time from the error'
        }
      },
      runs: {
        runs: [
          { stage: 'implement', run_id: 'r7', status: 'failed', exit_code: 1, quota: true, summary: 'limit' }
        ]
      }
    })
    const failure = readSliceFailure(root, 's1')
    assert.ok(failure)
    assert.equal(failure.cause, 'usage_limit')
    assert.equal(failure.stage, 'implement')
    assert.equal(failure.runId, 'r7')
    assert.equal(failure.quotaWait?.retries, 2)
    assert.ok((failure.quotaWait?.secondsLeft ?? 0) > 60)
  })

  test('a past reset time counts down to zero, never below it', () => {
    const root = repoWithSlice('s1', {
      state: {
        status: 'quota_wait',
        stages: { implement: { status: 'failed' } },
        quota: { waiting: true, resume_at: '2020-01-01T00:00:00+09:00', retries: 1, source: 'x' }
      },
      runs: { runs: [{ stage: 'implement', quota: true }] }
    })
    assert.equal(readSliceFailure(root, 's1')?.quotaWait?.secondsLeft, 0)
  })

  test('telemetry result_subtype makes it a turn death', () => {
    const root = repoWithSlice('s1', {
      state: {
        status: 'failed',
        reason: 'implement ran out of turns',
        stages: { implement: { status: 'failed' } }
      },
      runs: { runs: [{ stage: 'implement', run_dir: '', status: 'failed', summary: 'max_turns' }] }
    })
    const failure = readSliceFailure(root, 's1')
    assert.equal(failure?.cause, 'turns')
    assert.equal(failure?.reason, 'implement ran out of turns')
  })

  test('the subtype is read from the run directory when it is on this machine', () => {
    const root = tempRoot()
    const runDir = join(root, 'runs', 'r1')
    writeJson(join(runDir, 'telemetry.json'), { exact: { result_subtype: 'error_max_turns' } })
    const dir = join(root, '.aidev', 'slices', 's1')
    mkdirSync(dir, { recursive: true })
    writeJson(join(dir, 'state.json'), {
      status: 'failed',
      stages: { implement: { status: 'failed' } }
    })
    writeJson(join(dir, 'runs.json'), {
      runs: [{ stage: 'implement', run_dir: runDir, status: 'failed', summary: 'nothing useful' }]
    })

    const failure = readSliceFailure(root, 's1')
    assert.equal(failure?.resultSubtype, 'error_max_turns')
    assert.equal(failure?.cause, 'turns')
    assert.match(failure?.causeEvidence ?? '', /error_max_turns/)
  })

  test('a run_dir this machine does not have costs the subtype and nothing else', () => {
    const root = repoWithSlice('s1', {
      state: { status: 'failed', reason: 'boom', stages: { implement: { status: 'failed' } } },
      runs: {
        runs: [
          {
            stage: 'implement',
            run_dir: '/no/such/place/r1',
            status: 'failed',
            exit_code: 3,
            summary: 'ImportError'
          }
        ]
      }
    })
    const failure = readSliceFailure(root, 's1')
    assert.equal(failure?.resultSubtype, null)
    assert.equal(failure?.cause, 'other')
    assert.equal(failure?.exitCode, 3)
  })

  test('progress.md is quoted, and its absence is stated rather than faked', () => {
    const withNote = repoWithSlice('s1', {
      state: { status: 'failed', stages: { implement: { status: 'failed' } } },
      progress: '## done\n- half of it\n'
    })
    assert.equal(readSliceFailure(withNote, 's1')?.progress.exists, true)
    assert.match(readSliceFailure(withNote, 's1')?.progress.text ?? '', /half of it/)

    const without = repoWithSlice('s2', {
      state: { status: 'failed', stages: { implement: { status: 'failed' } } }
    })
    assert.equal(readSliceFailure(without, 's2')?.progress.exists, false)
  })

  test('the last failed stage in declared order is the one that died', () => {
    const root = repoWithSlice('s1', {
      state: {
        status: 'failed',
        stage_order: ['plan', 'implement', 'test'],
        stages: {
          plan: { status: 'done' },
          implement: { status: 'failed' },
          test: { status: 'failed' }
        }
      },
      runs: { runs: [{ stage: 'implement' }, { stage: 'test', run_id: 'rt' }] }
    })
    const failure = readSliceFailure(root, 's1')
    assert.equal(failure?.stage, 'test')
    assert.equal(failure?.runId, 'rt')
  })

  test('a stopped auto-recovery is carried through', () => {
    const root = repoWithSlice('s1', {
      state: {
        status: 'failed',
        stages: { implement: { status: 'failed' } },
        recovery: {
          stopped: { at: 'now', stage: 'implement', pin: 'extensions', detail: '연장 한도 소진' }
        }
      }
    })
    assert.equal(readSliceFailure(root, 's1')?.stopped?.pin, 'extensions')
  })

  test('a truncated runs.json does not take the panel down', () => {
    const root = tempRoot()
    const dir = join(root, '.aidev', 'slices', 's1')
    mkdirSync(dir, { recursive: true })
    writeJson(join(dir, 'state.json'), { status: 'failed', stages: {} })
    writeFileSync(join(dir, 'runs.json'), '{"runs": [{"stage": "impl', 'utf8')
    const failure = readSliceFailure(root, 's1')
    assert.ok(failure)
    assert.equal(failure.cause, 'other')
    assert.equal(failure.runId, null)
  })
})
