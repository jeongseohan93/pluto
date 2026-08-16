/**
 * The store is the only part of the binding a machine can check, so it is
 * checked against the Python contracts it ports rather than against itself:
 * `read_json_tolerant` (never raises), `read_decision` (first non-comment line
 * decides, anything else is PENDING), and the approval file convention.
 */
import { after, describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

import {
  liveStatusFor,
  listEpics,
  listSlices,
  parseDecision,
  readDecision,
  readJsonTolerant,
  readStageArtifact,
  writeApproval
} from './aidev-store'

const roots: string[] = []

after(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true })
})

function tempRoot(): string {
  const root = mkdtempSync(join(tmpdir(), 'pluto-'))
  roots.push(root)
  return root
}

/** A repo with one slice directory. `state` of `null` writes no state.json. */
function repoWith(slices: Record<string, unknown>): string {
  const root = tempRoot()
  for (const [id, state] of Object.entries(slices)) {
    const dir = join(root, '.aidev', 'slices', id)
    mkdirSync(dir, { recursive: true })
    if (state === null) continue
    writeFileSync(
      join(dir, 'state.json'),
      typeof state === 'string' ? state : JSON.stringify(state, null, 2),
      'utf8'
    )
  }
  return root
}

/** The shape the tool actually writes, taken from a real v0.3 slice. */
function sliceState(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: 1,
    slice_id: 'sample',
    status: 'done',
    repo: 'C:\\repo',
    gates: ['plan'],
    stage_order: ['plan', 'implement', 'test'],
    stages: {
      plan: { status: 'done', attempts: 1, approval: 'approved', run_id: 'r1' },
      implement: { status: 'done', attempts: 2, failures: 0 },
      test: { status: 'done', attempts: 1, verdict: 'pass' }
    },
    quota: { waiting: false, resume_at: null, retries: 0 },
    created_at: '2026-08-15T21:15:06+09:00',
    updated_at: '2026-08-15T21:40:25+09:00',
    test_verdict: 'pass',
    ...overrides
  }
}

describe('listSlices', () => {
  test('a folder with no .aidev is empty, not an error', () => {
    assert.deepEqual(listSlices(tempRoot()), [])
  })

  test('an empty slices root is empty', () => {
    const root = tempRoot()
    mkdirSync(join(root, '.aidev', 'slices'), { recursive: true })
    assert.deepEqual(listSlices(root), [])
  })

  test('reads a real state.json', () => {
    const root = repoWith({ 'a-slice': sliceState({ slice_id: 'a-slice' }) })
    const [slice] = listSlices(root)
    assert.equal(slice.id, 'a-slice')
    assert.equal(slice.status, 'done')
    assert.equal(slice.unreadable, false)
    assert.deepEqual(slice.gates, ['plan'])
    assert.equal(slice.updatedAt, '2026-08-15T21:40:25+09:00')
    assert.equal(slice.testVerdict, 'pass')
    assert.deepEqual(
      slice.stages.map((s) => `${s.name}=${s.status}`),
      ['plan=done', 'implement=done', 'test=done']
    )
    assert.equal(slice.stages[0].approval, 'approved')
  })

  test('truncated JSON is unreadable, not a crash', () => {
    const root = repoWith({ broken: '{"status":' })
    const [slice] = listSlices(root)
    assert.equal(slice.unreadable, true)
    assert.equal(slice.status, '(unreadable)')
    assert.deepEqual(slice.stages, [])
  })

  test('JSON that is not an object is unreadable (read_json_tolerant)', () => {
    const root = repoWith({ arr: '[1, 2]' })
    assert.equal(listSlices(root)[0].unreadable, true)
  })

  test('a slice directory with no state.json is still listed', () => {
    const root = repoWith({ bare: null })
    const [slice] = listSlices(root)
    assert.equal(slice.id, 'bare')
    assert.equal(slice.unreadable, true)
  })

  test('waitingStage is the open gate and nothing else', () => {
    const root = repoWith({
      w: sliceState({ status: 'waiting_approval:plan', updated_at: '2026-08-16T03:00:00+09:00' }),
      r: sliceState({ status: 'running:implement', updated_at: '2026-08-16T02:00:00+09:00' }),
      d: sliceState({ status: 'done', updated_at: '2026-08-16T01:00:00+09:00' })
    })
    const byId = new Map(listSlices(root).map((s) => [s.id, s]))
    assert.equal(byId.get('w')?.waitingStage, 'plan')
    assert.equal(byId.get('r')?.waitingStage, null)
    assert.equal(byId.get('d')?.waitingStage, null)
  })

  test('a stage the build has never heard of is still shown', () => {
    const state = sliceState()
    ;(state.stages as Record<string, unknown>).browser = { status: 'pending' }
    const root = repoWith({ x: state })
    const names = listSlices(root)[0].stages.map((s) => s.name)
    assert.deepEqual(names, ['plan', 'implement', 'test', 'browser'])
  })

  test('a schema 1 slice without workspace or commits parses', () => {
    const root = repoWith({
      old: { schema: 1, slice_id: 'old', status: 'merged', stages: { plan: { status: 'done' } } }
    })
    const [slice] = listSlices(root)
    assert.equal(slice.status, 'merged')
    assert.deepEqual(slice.gates, [])
    assert.deepEqual(
      slice.stages.map((s) => s.name),
      ['plan']
    )
  })

  test('most recently updated first', () => {
    const root = repoWith({
      older: sliceState({ updated_at: '2026-08-14T10:00:00+09:00' }),
      newer: sliceState({ updated_at: '2026-08-16T10:00:00+09:00' }),
      middle: sliceState({ updated_at: '2026-08-15T10:00:00+09:00' })
    })
    assert.deepEqual(
      listSlices(root).map((s) => s.id),
      ['newer', 'middle', 'older']
    )
  })
})

describe('readStageArtifact', () => {
  test('reads plan.md', () => {
    const root = repoWith({ s: sliceState() })
    writeFileSync(join(root, '.aidev', 'slices', 's', 'plan.md'), '# plan\n\nbody\n', 'utf8')
    const artifact = readStageArtifact(root, 's', 'plan')
    assert.equal(artifact.exists, true)
    assert.equal(artifact.text, '# plan\n\nbody\n')
    assert.equal(artifact.stage, 'plan')
  })

  test('a missing artifact still reports where it would be', () => {
    const root = repoWith({ s: sliceState() })
    const artifact = readStageArtifact(root, 's', 'plan')
    assert.equal(artifact.exists, false)
    assert.equal(artifact.text, '')
    assert.ok(artifact.path.endsWith(join('s', 'plan.md')))
  })

  test('an unknown stage falls back to plan.md, then requirement.md', () => {
    const root = repoWith({ s: sliceState() })
    writeFileSync(join(root, '.aidev', 'slices', 's', 'requirement.md'), 'req\n', 'utf8')
    assert.equal(readStageArtifact(root, 's', 'implement').text, 'req\n')
  })
})

describe('path guards', () => {
  const escapes = ['..', '../../x', 'a/b', 'a\\b', 'C:\\x', '.hidden', '']

  test('a slice id that is not one directory name is refused, and nothing moves', () => {
    const root = repoWith({ s: sliceState() })
    const before = listing(root)
    for (const id of escapes) {
      assert.throws(() => writeApproval(root, { sliceId: id, stage: 'plan', decision: 'approved' }))
      assert.throws(() => readStageArtifact(root, id, 'plan'))
    }
    assert.deepEqual(listing(root), before)
  })

  test('a stage name that is not one file name is refused', () => {
    const root = repoWith({ s: sliceState() })
    const before = listing(root)
    for (const stage of escapes) {
      assert.throws(() => writeApproval(root, { sliceId: 's', stage, decision: 'approved' }))
    }
    assert.deepEqual(listing(root), before)
  })
})

describe('writeApproval', () => {
  function repo(): string {
    return repoWith({ s: sliceState({ status: 'waiting_approval:plan' }) })
  }

  function approvalPath(root: string): string {
    return join(root, '.aidev', 'slices', 's', 'approvals', 'plan.md')
  }

  test('approved, with no file there yet', () => {
    const root = repo()
    const result = writeApproval(root, { sliceId: 's', stage: 'plan', decision: 'approved' })
    assert.equal(result.ok, true)
    assert.equal(result.effective, 'approved')
    assert.equal(readFileSync(approvalPath(root), 'utf8'), 'approved\n')
    assert.deepEqual(readDecision(approvalPath(root)), { verdict: 'approved', reason: '' })
  })

  test('rejected carries the reason in the v0.2 form', () => {
    const root = repo()
    const result = writeApproval(root, {
      sliceId: 's',
      stage: 'plan',
      decision: 'rejected',
      reason: 'scope is too wide'
    })
    assert.equal(result.ok, true)
    assert.equal(readFileSync(approvalPath(root), 'utf8'), 'rejected: scope is too wide\n')
    assert.deepEqual(readDecision(approvalPath(root)), {
      verdict: 'rejected',
      reason: 'scope is too wide'
    })
  })

  test('a pasted multi-line reason is folded to the one line read_decision sees', () => {
    const root = repo()
    writeApproval(root, {
      sliceId: 's',
      stage: 'plan',
      decision: 'rejected',
      reason: '  first line\nsecond\tline  \n\n third '
    })
    const text = readFileSync(approvalPath(root), 'utf8')
    assert.equal(text, 'rejected: first line second line third\n')
    assert.equal(text.split('\n').length - 1, 1)
    assert.equal(readDecision(approvalPath(root)).reason, 'first line second line third')
  })

  test('an empty reason still writes a decision read_decision accepts', () => {
    const root = repo()
    writeApproval(root, { sliceId: 's', stage: 'plan', decision: 'rejected', reason: '   ' })
    assert.equal(readFileSync(approvalPath(root), 'utf8'), 'rejected:\n')
    assert.equal(readDecision(approvalPath(root)).verdict, 'rejected')
  })

  test('a Korean reason survives the round trip, with no BOM', () => {
    const root = repo()
    writeApproval(root, {
      sliceId: 's',
      stage: 'plan',
      decision: 'rejected',
      reason: '범위가 요구사항과 다르다'
    })
    const bytes = readFileSync(approvalPath(root))
    assert.notEqual(bytes[0], 0xef) // no UTF-8 BOM
    assert.equal(readDecision(approvalPath(root)).reason, '범위가 요구사항과 다르다')
  })

  test('the comment template is kept and the decision is appended', () => {
    const root = repo()
    const template =
      "# Approval gate: plan   (20260816-x)\n#\n# Write the decision on its own line below. Lines starting with '#' are ignored.\n#\n#   approved\n#   rejected: <reason>\n"
    mkdirSync(join(root, '.aidev', 'slices', 's', 'approvals'), { recursive: true })
    writeFileSync(approvalPath(root), template, 'utf8')

    const result = writeApproval(root, { sliceId: 's', stage: 'plan', decision: 'approved' })
    assert.equal(result.ok, true)
    const text = readFileSync(approvalPath(root), 'utf8')
    assert.ok(text.startsWith(template))
    assert.equal(text.trimEnd().split('\n').pop(), 'approved')
    assert.equal(readDecision(approvalPath(root)).verdict, 'approved')
  })

  test('an already-decided gate is never overwritten', () => {
    const root = repo()
    mkdirSync(join(root, '.aidev', 'slices', 's', 'approvals'), { recursive: true })
    writeFileSync(approvalPath(root), '# gate\napproved\n', 'utf8')

    const result = writeApproval(root, {
      sliceId: 's',
      stage: 'plan',
      decision: 'rejected',
      reason: 'changed my mind'
    })
    assert.equal(result.ok, false)
    assert.equal(result.conflict, 'approved')
    assert.equal(readFileSync(approvalPath(root), 'utf8'), '# gate\napproved\n')
  })

  test('a half-written first line makes the write ineffective, and says so', () => {
    // read_decision stops at the first non-comment line: "hold on" is PENDING
    // forever, whatever we append after it. Reporting ok here would be a lie.
    const root = repo()
    mkdirSync(join(root, '.aidev', 'slices', 's', 'approvals'), { recursive: true })
    writeFileSync(approvalPath(root), '# gate\nhold on\n', 'utf8')

    const result = writeApproval(root, { sliceId: 's', stage: 'plan', decision: 'approved' })
    assert.equal(result.ok, false)
    assert.equal(result.effective, 'pending')
    assert.equal(result.conflict, undefined)
  })

  test('the approvals directory is created when it is missing', () => {
    const root = repo()
    assert.deepEqual(readdirSync(join(root, '.aidev', 'slices', 's')), ['state.json'])
    assert.equal(writeApproval(root, { sliceId: 's', stage: 'plan', decision: 'approved' }).ok, true)
    assert.equal(readDecision(approvalPath(root)).verdict, 'approved')
  })

  test('never writes CRLF', () => {
    const root = repo()
    mkdirSync(join(root, '.aidev', 'slices', 's', 'approvals'), { recursive: true })
    writeFileSync(approvalPath(root), '# windows-edited template\r\n#\r\n', 'utf8')
    writeApproval(root, { sliceId: 's', stage: 'plan', decision: 'approved' })
    assert.equal(readFileSync(approvalPath(root), 'utf8').includes('\r\n'), false)
  })

  test('leaves no .tmp behind', () => {
    const root = repo()
    writeApproval(root, { sliceId: 's', stage: 'plan', decision: 'approved' })
    assert.deepEqual(readdirSync(join(root, '.aidev', 'slices', 's', 'approvals')), ['plan.md'])
  })
})

describe('parseDecision', () => {
  const cases: [string, string, string][] = [
    ['approved\n', 'approved', ''],
    ['approve\n', 'approved', ''],
    ['Approved.\n', 'approved', ''],
    ['APPROVED!\n', 'approved', ''],
    ['rejected: too broad\n', 'rejected', 'too broad'],
    ['reject:nope\n', 'rejected', 'nope'],
    ['rejected\n', 'rejected', ''],
    ['# comment\n\n<!-- html -->\n  approved  \n', 'approved', ''],
    ['\uFEFFapproved\n', 'approved', ''],
    ['maybe later\napproved\n', 'pending', ''],
    ['# only comments\n', 'pending', ''],
    ['', 'pending', '']
  ]

  for (const [text, verdict, reason] of cases) {
    test(`${JSON.stringify(text)} -> ${verdict}`, () => {
      assert.deepEqual(parseDecision(text), { verdict, reason })
    })
  }

  test('a missing file is pending, not an error', () => {
    assert.deepEqual(readDecision(join(tempRoot(), 'nope.md')), { verdict: 'pending', reason: '' })
  })
})

describe('readJsonTolerant', () => {
  test('a missing path is null', () => {
    assert.equal(readJsonTolerant(join(tempRoot(), 'nope.json')), null)
  })

  test('a directory is null', () => {
    assert.equal(readJsonTolerant(tempRoot()), null)
  })
})

describe('liveStatusFor', () => {
  function sliceWithRun(live: unknown | null): string {
    const root = tempRoot()
    const sliceDir = join(root, 'slice')
    const runDir = join(root, 'run')
    mkdirSync(sliceDir, { recursive: true })
    mkdirSync(runDir, { recursive: true })
    writeFileSync(
      join(sliceDir, 'runs.json'),
      JSON.stringify({ runs: [{ run_dir: join(root, 'gone') }, { run_dir: runDir }] }),
      'utf8'
    )
    if (live !== null) writeFileSync(join(runDir, 'live.json'), JSON.stringify(live), 'utf8')
    return sliceDir
  }

  test('a fresh running run is not stale', () => {
    const live = liveStatusFor(sliceWithRun({ status: 'running', updated_at: Date.now() / 1000 }))
    assert.equal(live?.status, 'running')
    assert.equal(live?.stale, false)
  })

  test('a running run that stopped moving is stale after 10s', () => {
    const live = liveStatusFor(
      sliceWithRun({ status: 'running', updated_at: Date.now() / 1000 - 60 })
    )
    assert.equal(live?.stale, true)
    assert.ok((live?.ageSeconds ?? 0) >= 60)
  })

  test('a finished run is never stale, however old', () => {
    const live = liveStatusFor(sliceWithRun({ status: 'done', updated_at: Date.now() / 1000 - 600 }))
    assert.equal(live?.stale, false)
  })

  test('no live.json, no runs.json, and broken runs.json are all null', () => {
    assert.equal(liveStatusFor(sliceWithRun(null)), null)
    assert.equal(liveStatusFor(tempRoot()), null)

    const root = tempRoot()
    writeFileSync(join(root, 'runs.json'), '{"runs":', 'utf8')
    assert.equal(liveStatusFor(root), null)
  })

  test('only a running slice carries live status', () => {
    const root = repoWith({ s: sliceState({ status: 'done' }) })
    assert.equal(listSlices(root)[0].live, null)
  })
})

describe('listEpics', () => {
  test('no epics root is empty', () => {
    assert.deepEqual(listEpics(tempRoot()), [])
  })

  test("an epic projects each slice's own status, not its cached copy", () => {
    const root = repoWith({ 'slice-one': sliceState({ status: 'running:implement' }) })
    const dir = join(root, '.aidev', 'epics', 'ep-1')
    mkdirSync(dir, { recursive: true })
    writeFileSync(
      join(dir, 'state.json'),
      JSON.stringify({
        epic_id: 'ep-1',
        status: 'running:slice',
        current: 0,
        updated_at: '2026-08-16T09:00:00+09:00',
        slices: [
          { index: 1, title: 'first', slice_id: 'slice-one', status: 'pending' },
          { index: 2, title: 'second', status: 'pending' }
        ]
      }),
      'utf8'
    )
    const [epic] = listEpics(root)
    assert.equal(epic.id, 'ep-1')
    assert.equal(epic.status, 'running:slice')
    assert.equal(epic.current, 0)
    assert.equal(epic.slices[0].status, 'running:implement')
    assert.equal(epic.slices[1].status, 'pending')
  })

  test('a broken epic state.json is unreadable, not a crash', () => {
    const root = tempRoot()
    const dir = join(root, '.aidev', 'epics', 'ep-x')
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, 'state.json'), 'not json', 'utf8')
    const [epic] = listEpics(root)
    assert.equal(epic.unreadable, true)
    assert.equal(epic.status, '(unreadable)')
  })
})

/** Every path under a repo, so a test can assert nothing at all changed. */
function listing(root: string): string[] {
  const out: string[] = []
  const walk = (dir: string, prefix: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) =>
      a.name < b.name ? -1 : 1
    )) {
      const rel = prefix ? `${prefix}/${entry.name}` : entry.name
      out.push(rel)
      if (entry.isDirectory()) walk(join(dir, entry.name), rel)
    }
  }
  walk(root, '')
  return out
}
