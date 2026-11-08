import test from 'node:test'
import assert from 'node:assert/strict'
import { renamedEnv } from '../../scripts/product/env.mjs'
test('new development settings take precedence, preserve legacy fallback and reject conflicts', () => {
  assert.equal(renamedEnv('ALCOR_TEST', 'STELLA_TEST', { ALCOR_TEST: 'a' }), 'a')
  assert.equal(renamedEnv('ALCOR_TEST', 'STELLA_TEST', { STELLA_TEST: 'b' }), 'b')
  assert.equal(renamedEnv('ALCOR_TEST', 'STELLA_TEST', { ALCOR_TEST: 'a', STELLA_TEST: 'a' }), 'a')
  assert.throws(() => renamedEnv('ALCOR_TEST', 'STELLA_TEST', { ALCOR_TEST: 'a', STELLA_TEST: 'b' }), /Conflicting/)
})
