/**
 * `readSource` against a real directory — chiefly the refusals.
 *
 * This is the one function that turns a string from the renderer into a file
 * read, so what is being checked here is mostly what it will *not* do: leave
 * the root, open a `.png`, or hand back four megabytes of something binary.
 */
import { after, describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

import { MAX_BYTES, readSource } from './source-store'

const roots: string[] = []

after(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true })
})

/** A throwaway repository with one Python file in it, the way the graph sees it. */
function repoWithSource(): string {
  const root = mkdtempSync(join(tmpdir(), 'pluto-source-'))
  roots.push(root)
  mkdirSync(join(root, 'aidev'), { recursive: true })
  writeFileSync(join(root, 'aidev', 'pipeline.py'), 'def run():\n    return 1\n', 'utf8')
  return root
}

describe('readSource', () => {
  test('reads a file the graph would name, with its language and its size', () => {
    const root = repoWithSource()
    const file = readSource(root, 'aidev/pipeline.py')
    assert.equal(file.ok, true)
    assert.equal(file.path, 'aidev/pipeline.py')
    assert.equal(file.lang, 'python')
    assert.match(file.text, /def run\(\)/)
    // 'def run():', '    return 1' and the empty string after the last newline.
    assert.equal(file.lines, 3)
    assert.equal(file.bytes, 24)
  })

  test('a Windows-spelled path is the same path', () => {
    const root = repoWithSource()
    assert.equal(readSource(root, 'aidev\\pipeline.py').ok, true)
  })

  test('traversal is refused by shape, before the disk is touched', () => {
    const root = repoWithSource()
    for (const bad of [
      '../../etc/passwd',
      '..\\..\\windows\\win.ini',
      'aidev/../../outside.py',
      'aidev/../../../pipeline.py'
    ]) {
      const file = readSource(root, bad)
      assert.equal(file.ok, false, bad)
      assert.equal(file.problem, 'unreadable', bad)
    }
  })

  test('an absolute path is not a relative one', () => {
    const root = repoWithSource()
    for (const bad of ['/etc/passwd', 'C:\\Windows\\win.ini', 'c:/windows/win.ini', '//host/share/x.py']) {
      const file = readSource(root, bad)
      assert.equal(file.ok, false, bad)
      assert.equal(file.problem, 'unreadable', bad)
    }
  })

  test('an empty path is a refusal, not a read of the root', () => {
    const root = repoWithSource()
    assert.equal(readSource(root, '').problem, 'unreadable')
    assert.equal(readSource(root, '   ').problem, 'unreadable')
    assert.equal(readSource(root, undefined as unknown as string).problem, 'unreadable')
  })

  test('an extension this app has no highlighter for is not opened', () => {
    const root = repoWithSource()
    writeFileSync(join(root, 'logo.png'), 'not really a png', 'utf8')
    const file = readSource(root, 'logo.png')
    assert.equal(file.ok, false)
    assert.equal(file.problem, 'unsupported')
    assert.equal(file.path, 'logo.png')
  })

  test('a file that is not there says so, and says where it looked', () => {
    const root = repoWithSource()
    const file = readSource(root, 'aidev/gone.py')
    assert.equal(file.problem, 'missing')
    assert.match(file.detail ?? '', /aidev\/gone\.py/)
  })

  test('a directory is not a file', () => {
    const root = repoWithSource()
    // The name has to pass the extension gate first, or this would be
    // `unsupported` and prove nothing about the stat.
    mkdirSync(join(root, 'pkg.py'))
    assert.equal(readSource(root, 'pkg.py').problem, 'missing')
  })

  test('past the size ceiling the viewer declines rather than stalls', () => {
    const root = repoWithSource()
    writeFileSync(join(root, 'huge.py'), 'x'.repeat(MAX_BYTES + 1), 'utf8')
    const file = readSource(root, 'huge.py')
    assert.equal(file.problem, 'too-large')
    assert.match(file.detail ?? '', new RegExp(String(MAX_BYTES)))
  })

  test('a NUL in the head means this is not text', () => {
    const root = repoWithSource()
    writeFileSync(join(root, 'blob.py'), Buffer.from([0x64, 0x65, 0x00, 0x66]))
    assert.equal(readSource(root, 'blob.py').problem, 'binary')
  })

  test('a BOM is dropped so line one is line one', () => {
    const root = repoWithSource()
    writeFileSync(join(root, 'bom.py'), '\uFEFFdef run():\n', 'utf8')
    const file = readSource(root, 'bom.py')
    assert.equal(file.ok, true)
    assert.ok(file.text.startsWith('def run()'))
  })

  test("CRLF survives: the line numbers have to be the file's own", () => {
    const root = repoWithSource()
    writeFileSync(join(root, 'crlf.py'), 'a = 1\r\nb = 2\r\n', 'utf8')
    const file = readSource(root, 'crlf.py')
    assert.ok(file.text.includes('\r\n'))
    assert.equal(file.lines, 3)
  })
})
