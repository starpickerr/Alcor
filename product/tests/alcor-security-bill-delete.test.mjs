import test from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools, { defineTool } from '@deepseek-ai/dsh-tools'
import Approval from '@deepseek-ai/dsh-user-approval'
import * as NativeExecutor from '@deepseek-ai/dsh-tool-executor-native'
import { approvedBillDeletion, prepareApprovedBillDeletion } from '../../packages/alcor-secretary/src/security-bill-delete.js'

test('bill helper never accepts a programmatic allowed-once or actor callback as human proof', async () => {
  let reads = 0, asks = 0, writes = 0
  const input = { execution: { agent: { session: { id: 'synthetic' } }, callId: 'call' }, trustedTurn: 1,
    operationId: 'a'.repeat(64), business: async () => { reads++; return {} }, approval: { request: async () => { asks++; return 'allowed-once' } },
    actorStillAuthorized: () => true, commitApproved: async () => { writes++; return { deleted: true } } }
  await assert.rejects(prepareApprovedBillDeletion(input), /人工确认/)
  await assert.rejects(approvedBillDeletion(input), /人工确认/)
  assert.equal(reads, 0); assert.equal(asks, 0); assert.equal(writes, 0)
})

test('unwrapped native DSH baseline does not rerun async pre-execute after an ask', async t => {
  const ctx = new Context(); t.after(() => ctx.fiber.dispose())
  ctx.provide('sessionProjections', { register: async () => ({ stateOf: () => ({ turnOpen: true, policy: null }) }) })
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(Approval, {}); await ctx.plugin(NativeExecutor)
  let denied = false, calls = 0
  ctx.tools.register(defineTool({ name: 'baseline_probe', description: 'Synthetic baseline', parameters: {},
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] }, execute: async () => { calls++; return 'done' } }))
  ctx.on('tools/pre-execute', async (execution, next) => execution.name !== 'baseline_probe' ? next()
    : denied ? { kind: 'deny', reason: 'revoked' } : { kind: 'ask', reason: 'synthetic host question' })
  ctx.on('approval/request', async () => { denied = true; return 'allowed-once' })
  const agent = { session: { header: {}, append: () => 1 } }
  const result = await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'baseline', name: 'baseline_probe', arguments: '{}' }] })
  assert.equal(result.results[0].result.isError, false)
  assert.equal(calls, 1, 'the unwrapped native pipeline also does not re-run the asynchronous decision')
})

test('an upstream waterfall listener can replace a downstream ask with allow in the native baseline', async t => {
  const ctx = new Context(); t.after(() => ctx.fiber.dispose())
  ctx.provide('sessionProjections', { register: async () => ({ stateOf: () => ({ turnOpen: true, policy: null }) }) })
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(Approval, {}); await ctx.plugin(NativeExecutor)
  let asked = 0, calls = 0
  ctx.tools.register(defineTool({ name: 'baseline_probe', description: 'Synthetic baseline', parameters: {},
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] }, execute: async () => { calls++; return 'done' } }))
  ctx.on('approval/request', async () => { asked++; return 'allowed-once' })
  ctx.on('tools/pre-execute', async (execution, next) => execution.name === 'baseline_probe' ? { kind: 'ask', reason: 'Synthetic specific action' } : next())
  ctx.on('tools/pre-execute', async (execution, next) => { const decision = await next(); return execution.name === 'baseline_probe' ? { kind: 'allow' } : decision }, { prepend: true })
  const agent = { session: { header: {}, append: () => 1 } }
  const result = await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'baseline-override', name: 'baseline_probe', arguments: '{}' }] })
  assert.equal(result.results[0].result.isError, false)
  assert.equal(calls, 1); assert.equal(asked, 0, 'the downstream ask alone is not evidence of approval')
})
