import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../../packages/alcor-secretary/src/category-manager.jsx', import.meta.url), 'utf8')
const expression = source.match(/^export const appleOperationMessage = ([\s\S]+?)\r?\n\r?\nconst colors/m)[1]
const message = Function('return (' + expression + ')')()
const intent = { version: 1, plan_fingerprint: 'a'.repeat(64) }

test('core operation messages separate unconfirmed, confirmed, not-sent, sending and unknown', () => {
  for (const document of [{ _confirmed_intent: intent }, JSON.stringify({ _confirmed_intent: intent })]) {
    assert.match(message({ action: 'delete', state: 'pending', document }), /已确认，等待同步/)
    assert.match(message({ action: 'delete', state: 'pending', deletion_phase: 'not_sent', document }), /尚未发送/)
    assert.match(message({ action: 'delete', state: 'sending', deletion_phase: 'not_sent', document }), /正在向 Apple 提交/)
    assert.match(message({ action: 'delete', state: 'uncertain', deletion_phase: 'not_sent', document }), /不要重复提交/)
  }
  for (const document of [null, '{broken', {}, { _confirmed_intent: { version: 1 } }]) {
    assert.match(message({ action: 'delete', state: 'pending', document }), /等待本次确认/)
  }
  assert.match(message({ action: 'complete', state: 'pending' }), /^等待同步/)
  assert(source.includes("operation.state === 'pending' && <Button"))
  assert(!source.includes('失败会保留数据，可重试或取消'))
})
