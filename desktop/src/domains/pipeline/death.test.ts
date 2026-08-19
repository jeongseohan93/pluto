/**
 * The cause of death, checked against `aidev/recovery.py`'s own rules.
 *
 * The port is only worth having if it answers exactly what the engine answers,
 * so these cases are the Python docstrings' cases: a limit wins over
 * everything, then a turn death by either witness, then anything else.
 */
import { describe, test } from 'node:test'
import assert from 'node:assert/strict'

import { CAUSE_OTHER, CAUSE_QUOTA, CAUSE_TURNS, causeEvidence, classifyCause, isTurnDeath } from './death'

describe('isTurnDeath', () => {
  test('the exact result subtypes', () => {
    assert.equal(isTurnDeath('error_max_turns', ''), true)
    assert.equal(isTurnDeath('max_turns', ''), true)
    assert.equal(isTurnDeath(' ERROR_MAX_TURNS ', ''), true)
  })

  test('the looser spellings that only reach us through the log', () => {
    assert.equal(isTurnDeath(null, 'hit the Maximum number of turns'), true)
    assert.equal(isTurnDeath(null, 'subtype=error_max_turns'), true)
  })

  test('a plain error is not a turn death', () => {
    assert.equal(isTurnDeath('error_during_execution', 'boom'), false)
    assert.equal(isTurnDeath(null, ''), false)
    assert.equal(isTurnDeath(undefined, 'turns remaining: 4'), false)
  })
})

describe('classifyCause', () => {
  test('a usage limit wins over a turn death', () => {
    assert.equal(classifyCause('error_max_turns', 'max_turns', true), CAUSE_QUOTA)
  })

  test('turn death when the engine did not call it a limit', () => {
    assert.equal(classifyCause('error_max_turns', '', false), CAUSE_TURNS)
  })

  test('anything else is the ordinary failure path', () => {
    assert.equal(classifyCause(null, 'ImportError: no module named x', false), CAUSE_OTHER)
  })
})

describe('causeEvidence', () => {
  test('names the reading that decided, and never claims more than it has', () => {
    assert.match(causeEvidence(CAUSE_QUOTA, null, true), /runs\.json/)
    assert.match(causeEvidence(CAUSE_QUOTA, null, false), /state\.json/)
    assert.match(causeEvidence(CAUSE_TURNS, 'error_max_turns', false), /error_max_turns/)
    assert.match(causeEvidence(CAUSE_TURNS, null, false), /turn-limit marker/)
    assert.match(causeEvidence(CAUSE_OTHER, null, false), /neither/)
  })
})
