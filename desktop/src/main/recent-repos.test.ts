import { after, describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

import { addRecent, readRecents, RECENT_LIMIT } from './recent-repos'

const roots: string[] = []

after(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true })
})

function recentsFile(): string {
  const root = mkdtempSync(join(tmpdir(), 'pluto-recents-'))
  roots.push(root)
  return join(root, 'pluto-repos.json')
}

describe('recent repositories', () => {
  test('a file that is not there yet reads as empty', () => {
    assert.deepEqual(readRecents(recentsFile()), [])
  })

  test('a broken file reads as empty rather than throwing', () => {
    const file = recentsFile()
    writeFileSync(file, '{"recent":', 'utf8')
    assert.deepEqual(readRecents(file), [])
  })

  test('most recently opened first', () => {
    const file = recentsFile()
    addRecent(file, 'C:\\a')
    addRecent(file, 'C:\\b')
    assert.deepEqual(readRecents(file), ['C:\\b', 'C:\\a'])
  })

  test('re-opening a repo moves it to the front instead of duplicating it', () => {
    const file = recentsFile()
    addRecent(file, 'C:\\a')
    addRecent(file, 'C:\\b')
    assert.deepEqual(addRecent(file, 'C:\\a'), ['C:\\a', 'C:\\b'])
  })

  test('the same folder in different case is one entry (Windows)', () => {
    const file = recentsFile()
    addRecent(file, 'C:\\Repo')
    assert.deepEqual(addRecent(file, 'c:\\repo'), ['c:\\repo'])
  })

  test(`at most ${RECENT_LIMIT} are kept`, () => {
    const file = recentsFile()
    for (let i = 0; i < RECENT_LIMIT + 5; i++) addRecent(file, `C:\\repo-${i}`)
    const recent = readRecents(file)
    assert.equal(recent.length, RECENT_LIMIT)
    assert.equal(recent[0], `C:\\repo-${RECENT_LIMIT + 4}`)
  })

  test('entries that are not strings are dropped', () => {
    const file = recentsFile()
    writeFileSync(file, JSON.stringify({ recent: ['C:\\a', 3, null, '', 'C:\\b'] }), 'utf8')
    assert.deepEqual(readRecents(file), ['C:\\a', 'C:\\b'])
  })
})
