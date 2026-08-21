/**
 * The editor's one guard, and the template it opens with.
 *
 * The refusals below are the whole of "tasks/ 경로 탈출 금지": each is asserted
 * on its own rather than in a loop, so a failure names the spelling that got
 * through. The template is checked against the *parser's* rules, not against
 * its own text — a front matter that `aidev/pipeline.py` would read differently
 * than we meant is the only way this file can be wrong.
 */
import { describe, test } from 'node:test'
import assert from 'node:assert/strict'

import {
  MIN_REQUIREMENT_CHARS,
  REQUIREMENT_TEMPLATE,
  normalizeRequirementName,
  requirementCommitMessage,
  requirementNameProblem,
  requirementPathFor
} from './requirement'

describe('normalizeRequirementName', () => {
  test('the extension goes on once', () => {
    assert.equal(normalizeRequirementName('foo'), 'foo.md')
    assert.equal(normalizeRequirementName('foo.md'), 'foo.md')
  })

  test('surrounding space is not part of a filename', () => {
    assert.equal(normalizeRequirementName(' foo '), 'foo.md')
  })

  test('nothing typed stays nothing', () => {
    assert.equal(normalizeRequirementName(''), '')
    assert.equal(normalizeRequirementName('   '), '')
  })
})

describe('requirementPathFor', () => {
  test('the names the launcher already offers', () => {
    assert.equal(requirementPathFor('foo'), 'tasks/foo.md')
    assert.equal(requirementPathFor('v0.2.5-x'), 'tasks/v0.2.5-x.md')
    assert.equal(requirementPathFor('a_b.md'), 'tasks/a_b.md')
  })

  test('nothing that reaches out of tasks/ is a name', () => {
    assert.equal(requirementPathFor('../x'), null)
    assert.equal(requirementPathFor('a/b'), null)
    assert.equal(requirementPathFor('specs/x'), null)
    assert.equal(requirementPathFor('tasks/x'), null)
    assert.equal(requirementPathFor('/abs'), null)
    assert.equal(requirementPathFor('C:\\x'), null)
    assert.equal(requirementPathFor('.hidden'), null)
    assert.equal(requirementPathFor('-lead'), null)
    assert.equal(requirementPathFor(''), null)
  })

  test('the extension the CLI matches is the lowercase one', () => {
    assert.equal(requirementPathFor('foo.MD'), null)
  })
})

describe('requirementNameProblem', () => {
  test('an acceptable name has no problem to state', () => {
    assert.equal(requirementNameProblem('smoke-req'), '')
  })

  test('every refusal says which rule it was', () => {
    assert.match(requirementNameProblem(''), /입력/)
    assert.match(requirementNameProblem('a/b'), /폴더/)
    assert.match(requirementNameProblem('../x'), /폴더/)
    assert.match(requirementNameProblem('foo.MD'), /\.md/)
    assert.match(requirementNameProblem('.hidden'), /영문/)
  })
})

describe('REQUIREMENT_TEMPLATE', () => {
  /** `_FRONT_MATTER_RE` in `aidev/pipeline.py`, as this test needs it. */
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(REQUIREMENT_TEMPLATE)

  test('the front matter block opens and closes', () => {
    assert.ok(match, 'the template does not start with a --- block')
  })

  test('it declares exactly what the requirement asked for', () => {
    const block = match?.[1] ?? ''
    assert.match(block, /^approval: plan$/m)
    assert.match(block, /^max_turns: implement=120$/m)
  })

  test('the test_commands example is a comment, so it turns nothing on', () => {
    const block = match?.[1] ?? ''
    const line = block.split('\n').find((raw) => raw.includes('test_commands'))
    assert.ok(line, 'the template has no test_commands example')
    // parse_front_matter skips lines starting with '#': the example is visible
    // to a human and invisible to the parser until somebody uncomments it.
    assert.ok(line.trim().startsWith('#'), `test_commands is live, not an example: ${line}`)
  })

  test('the four sections the requirement named are all there', () => {
    for (const heading of ['## 배경', '## 요구사항', '## 하지 않는 것', '## Done Criteria']) {
      assert.ok(REQUIREMENT_TEMPLATE.includes(`\n${heading}`), `missing ${heading}`)
    }
  })

  test('the body alone already passes guard_requirement', () => {
    const body = REQUIREMENT_TEMPLATE.slice(match?.[0].length ?? 0)
    assert.ok(body.trim() !== '', 'front matter with no body')
    assert.ok(body.split(/\s+/).join('').length >= MIN_REQUIREMENT_CHARS)
  })

  test('LF only — a template must not be the first CRLF in the file', () => {
    assert.ok(!REQUIREMENT_TEMPLATE.includes('\r'))
  })
})

describe('requirementCommitMessage', () => {
  test('the stem, not the whole filename', () => {
    assert.equal(requirementCommitMessage('tasks/req-editor.md'), 'task: req-editor')
    assert.equal(requirementCommitMessage('tasks/v0.2.5-x.md'), 'task: v0.2.5-x')
  })
})
