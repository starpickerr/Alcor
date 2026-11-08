import test from 'node:test'
import assert from 'node:assert/strict'
import { isApplePlanOperation } from '../../packages/alcor-secretary/src/security-apple-export-gate.js'
import { requiresUnimplementedAppleApproval as blocked } from '../../packages/alcor-secretary/src/security-policy.js'
const call = (method, args = {}) => ({ name: 'alcor_business', arguments: { domain: 'apple', method, arguments: args } })
test('Apple category/manual-delete boundaries remain closed; item choices require the live plan gate', () => {
  for (const method of ['one_time','transfer_batch']) assert.equal(blocked(call(method, { direction: 'export', confirm: true, token: 'model-supplied' })), false)
  for (const choice of ['keep_alcor','keep_apple']) {
    assert.equal(blocked(call('resolve_category_conflict', { choice })), true)
    assert.equal(blocked(call('resolve_conflict', { choice })), false)
    assert.equal(isApplePlanOperation(call('resolve_conflict', { choice })), true)
  }
  for (const method of ['sync','start_sync']) for (const field of ['confirm_deletions','confirm_local_deletions']) for (const value of [true, 'yes', 1, null]) assert.equal(blocked(call(method, { [field]: value })), true)
  for (const request of [call('one_time', { direction: 'import' }),call('transfer_batch', { direction: 'import' }),call('sync'),call('start_sync'),call('set_auto'),call('resolve_category_conflict', { choice: 'acknowledge' })]) assert.equal(blocked(request), false)
  assert.equal(blocked({ name: 'unrelated', arguments: { domain: 'apple', method: 'one_time', arguments: { direction: 'export' } } }), false)
})
