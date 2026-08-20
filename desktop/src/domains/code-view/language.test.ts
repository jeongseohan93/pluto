/**
 * `monacoLanguage` — the extension table, and the cases that must answer `''`.
 *
 * This function is also the read whitelist, so the negatives matter as much as
 * the positives: everything it does not name is a file the app refuses to open.
 */
import { describe, test } from 'node:test'
import assert from 'node:assert/strict'

import { monacoLanguage } from './language'

describe('monacoLanguage', () => {
  test('names the four languages the graph indexes', () => {
    assert.equal(monacoLanguage('aidev/pipeline.py'), 'python')
    assert.equal(monacoLanguage('desktop/src/shared/ide.ts'), 'typescript')
    assert.equal(monacoLanguage('desktop/src/renderer/src/App.tsx'), 'typescript')
    assert.equal(monacoLanguage('scripts/build.js'), 'javascript')
  })

  test('the rest of the table', () => {
    assert.equal(monacoLanguage('a.jsx'), 'javascript')
    assert.equal(monacoLanguage('a.mjs'), 'javascript')
    assert.equal(monacoLanguage('a.cjs'), 'javascript')
    assert.equal(monacoLanguage('package.json'), 'json')
    assert.equal(monacoLanguage('README.md'), 'markdown')
    assert.equal(monacoLanguage('theme.css'), 'css')
    assert.equal(monacoLanguage('index.html'), 'html')
    assert.equal(monacoLanguage('run.sh'), 'shell')
    assert.equal(monacoLanguage('ci.yml'), 'yaml')
    assert.equal(monacoLanguage('ci.yaml'), 'yaml')
    assert.equal(monacoLanguage('pyproject.toml'), 'ini')
    assert.equal(monacoLanguage('notes.txt'), 'plaintext')
  })

  test('the extension is read case-insensitively', () => {
    assert.equal(monacoLanguage('AIDEV/PIPELINE.PY'), 'python')
    assert.equal(monacoLanguage('A.Tsx'), 'typescript')
  })

  test('only the last extension counts', () => {
    assert.equal(monacoLanguage('archive.tar.gz'), '')
    assert.equal(monacoLanguage('component.test.ts'), 'typescript')
  })

  test('a dot in a directory is not an extension', () => {
    assert.equal(monacoLanguage('.aidev/graph/dirty'), '')
    assert.equal(monacoLanguage('some.dir/Makefile'), '')
    assert.equal(monacoLanguage('some.dir\\Makefile'), '')
  })

  test('a leading dot types nothing — it names the file', () => {
    assert.equal(monacoLanguage('.gitignore'), '')
    assert.equal(monacoLanguage('.env'), '')
  })

  test('anything unnameable is refused rather than guessed', () => {
    assert.equal(monacoLanguage(''), '')
    assert.equal(monacoLanguage('Makefile'), '')
    assert.equal(monacoLanguage('logo.png'), '')
    assert.equal(monacoLanguage('graph.db'), '')
    assert.equal(monacoLanguage(undefined as unknown as string), '')
    assert.equal(monacoLanguage(42 as unknown as string), '')
  })
})
