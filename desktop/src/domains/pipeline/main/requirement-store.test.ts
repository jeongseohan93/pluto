/**
 * The requirement editor's read and write, against real directories.
 *
 * Two things are worth the temp repositories. One: no spelling outside
 * `tasks/<name>.md` may reach the disk, in either direction — the umbrella
 * specs under `tasks/specs/` are the case the requirement names, and they are
 * refused even when the file is really there. Two: the commit takes the one
 * file it was given and nothing else, which is why `noise.txt` is staged in the
 * repository before every commit test.
 *
 * The git half skips itself where git is not installed, the same way
 * `workspace.py` `git_available` does — a machine without git still runs the
 * rest of the suite.
 */
import { after, describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

import { readRequirement, saveRequirement } from './requirement-store'

const roots: string[] = []

after(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true })
})

function tempRoot(): string {
  const root = mkdtempSync(join(tmpdir(), 'pluto-req-'))
  roots.push(root)
  return root
}

/**
 * A directory git will refuse to work in, whatever it is nested inside.
 *
 * A bare temp directory is not enough: git walks upwards looking for a `.git`,
 * and on a machine where the temp path happens to sit under a repository these
 * tests would both pass for the wrong reason and stage things in somebody's
 * index. A `.git` *file* pointing nowhere stops the walk.
 */
function tempNonRepo(): string {
  const root = tempRoot()
  writeFileSync(join(root, '.git'), 'gitdir: nowhere-at-all\n', 'utf8')
  return root
}

/** Is git on this machine at all? Everything below it is skipped when not. */
function gitAvailable(): boolean {
  try {
    return spawnSync('git', ['--version'], { shell: false, encoding: 'utf8' }).status === 0
  } catch {
    return false
  }
}

const noGit = !gitAvailable()

function git(root: string, args: string[]): string {
  const proc = spawnSync('git', ['-c', 'commit.gpgsign=false', ...args], {
    cwd: root,
    shell: false,
    encoding: 'utf8'
  })
  return `${proc.stdout ?? ''}`
}

/** A repository with one commit already in it, and one staged file of its own. */
function tempRepo(): string {
  const root = tempRoot()
  git(root, ['init', '-q'])
  git(root, ['config', 'user.name', 'test'])
  git(root, ['config', 'user.email', 'test@localhost'])
  writeFileSync(join(root, 'seed.txt'), 'seed\n', 'utf8')
  git(root, ['add', '--', 'seed.txt'])
  git(root, ['-c', 'user.name=test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'seed'])
  // Somebody else's work, already staged. Nothing below may sweep it up.
  writeFileSync(join(root, 'noise.txt'), 'not mine\n', 'utf8')
  git(root, ['add', '--', 'noise.txt'])
  return root
}

describe('readRequirement', () => {
  test('a requirement is read as it is on disk', () => {
    const root = tempRoot()
    mkdirSync(join(root, 'tasks'), { recursive: true })
    writeFileSync(join(root, 'tasks', 'a.md'), '# A\nbody\n', 'utf8')

    const doc = readRequirement(root, 'tasks/a.md')
    assert.equal(doc.ok, true)
    assert.equal(doc.name, 'a.md')
    assert.match(doc.text, /body/)
    assert.ok(doc.bytes > 0)
  })

  test('a BOM an editor left behind is not part of the text', () => {
    const root = tempRoot()
    mkdirSync(join(root, 'tasks'), { recursive: true })
    writeFileSync(join(root, 'tasks', 'bom.md'), '\ufeff# A\n', 'utf8')
    assert.ok(readRequirement(root, 'tasks/bom.md').text.startsWith('# A'))
  })

  test('a requirement that is not there is missing, not a throw', () => {
    const doc = readRequirement(tempRoot(), 'tasks/nope.md')
    assert.equal(doc.ok, false)
    assert.equal(doc.problem, 'missing')
  })

  test('tasks/specs/ is refused even when the file is really there', () => {
    const root = tempRoot()
    mkdirSync(join(root, 'tasks', 'specs'), { recursive: true })
    writeFileSync(join(root, 'tasks', 'specs', 'umbrella.md'), '# umbrella\n', 'utf8')

    const doc = readRequirement(root, 'tasks/specs/umbrella.md')
    assert.equal(doc.ok, false)
    assert.equal(doc.problem, 'refused')
  })

  test('this is not a file read: nothing outside tasks/ opens', () => {
    const root = tempRoot()
    for (const path of [
      '../x.md',
      'tasks/../x.md',
      'aidev/pipeline.py',
      'C:\\x.md',
      '/etc/passwd',
      ''
    ]) {
      const doc = readRequirement(root, path)
      assert.equal(doc.ok, false, `${path} was opened`)
      assert.equal(doc.problem, 'refused', `${path} was refused for the wrong reason`)
    }
  })
})

describe('saveRequirement without git', () => {
  test('the file lands even where nothing can commit it', () => {
    const root = tempNonRepo()
    const save = saveRequirement(root, { path: 'tasks/x.md', text: '# X\n', create: true })

    assert.equal(save.ok, true, save.error ?? '')
    assert.equal(readFileSync(join(root, 'tasks', 'x.md'), 'utf8'), '# X\n')
    assert.equal(save.committed, false)
    // The write succeeded, so the failure is the commit's — never `error`.
    assert.equal(save.error, undefined)
    assert.ok((save.commitError ?? '') !== '', 'a failed commit said nothing about why')
  })

  test('CRLF is normalised and a final newline is guaranteed', () => {
    const root = tempNonRepo()
    saveRequirement(root, { path: 'tasks/x.md', text: '# X\r\nbody', create: true })
    assert.equal(readFileSync(join(root, 'tasks', 'x.md'), 'utf8'), '# X\nbody\n')
  })

  test('nothing outside tasks/ can be written', () => {
    const root = tempNonRepo()
    for (const path of ['../x.md', 'tasks/specs/u.md', 'aidev/pipeline.py', 'C:\\x.md']) {
      const save = saveRequirement(root, { path, text: 'x', create: true })
      assert.equal(save.ok, false, `${path} was written`)
      assert.equal(save.committed, false)
    }
    assert.equal(existsSync(join(root, 'tasks', 'specs')), false)
    assert.equal(existsSync(join(root, 'aidev')), false)
  })

  test('create over an existing file refuses and leaves it alone', () => {
    const root = tempNonRepo()
    mkdirSync(join(root, 'tasks'), { recursive: true })
    writeFileSync(join(root, 'tasks', 'x.md'), 'original\n', 'utf8')

    const save = saveRequirement(root, { path: 'tasks/x.md', text: 'replacement\n', create: true })
    assert.equal(save.ok, false)
    assert.equal(save.alreadyExists, true)
    assert.equal(readFileSync(join(root, 'tasks', 'x.md'), 'utf8'), 'original\n')
  })
})

describe('saveRequirement in a repository', { skip: noGit ? 'git is not installed' : false }, () => {
  test('the file is committed under its own name', () => {
    const root = tempRepo()
    const save = saveRequirement(root, { path: 'tasks/x.md', text: '# X\n', create: true })

    assert.equal(save.ok, true, save.error ?? '')
    assert.equal(save.committed, true, save.commitError ?? '')
    assert.match(git(root, ['log', '-1', '--format=%s']), /^task: x$/m)
    assert.ok((save.commit ?? '').length >= 4, 'no short sha came back')
  })

  test('the commit takes that one file and not what was already staged', () => {
    const root = tempRepo()
    saveRequirement(root, { path: 'tasks/x.md', text: '# X\n', create: true })

    const named = git(root, ['show', '--name-only', '--format=', 'HEAD'])
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line !== '')
    assert.deepEqual(named, ['tasks/x.md'])
    // and the other change is still exactly where its author left it
    assert.match(git(root, ['diff', '--cached', '--name-only']), /noise\.txt/)
  })

  test('saving the same bytes twice is unchanged, not an error', () => {
    const root = tempRepo()
    saveRequirement(root, { path: 'tasks/x.md', text: '# X\n', create: true })
    const again = saveRequirement(root, { path: 'tasks/x.md', text: '# X\n', create: false })

    assert.equal(again.ok, true)
    assert.equal(again.committed, false)
    assert.equal(again.unchanged, true)
    assert.equal(again.commitError, undefined)
  })

  test('editing an existing requirement makes one more commit', () => {
    const root = tempRepo()
    saveRequirement(root, { path: 'tasks/x.md', text: '# X\n', create: true })
    const edited = saveRequirement(root, { path: 'tasks/x.md', text: '# X\nmore\n', create: false })

    assert.equal(edited.committed, true, edited.commitError ?? '')
    assert.equal(git(root, ['rev-list', '--count', 'HEAD']).trim(), '3')
  })
})
