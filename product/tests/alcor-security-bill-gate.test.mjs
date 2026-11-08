import test from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools, { defineTool } from '@deepseek-ai/dsh-tools'
import Approval from '@deepseek-ai/dsh-user-approval'
import * as Native from '@deepseek-ai/dsh-tool-executor-native'
import * as Ptc from '@deepseek-ai/dsh-tool-executor-ptc'
import { tmpdir } from 'node:os'
import { createBillDeleteGate } from '../../packages/alcor-secretary/src/security-bill-gate.js'

async function fixture(t, mode = 'native') {
  const ctx = new Context(); t.after(() => ctx.fiber.dispose())
  ctx.provide('sessionProjections', { register: async () => ({ stateOf: () => ({ turnOpen: true, policy: null }) }) })
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(Approval, {})
  let ptcInput
  if (mode === 'ptc') {
    ctx.provide('ptcRuntime', { language: 'typescript', programFormat: 'function-body', isolation: 'synthetic', executionInstructions: '', resolve: value => value,
      run: async request => { try { return { logs: [], value: await request.bindings[0].functions.alcor_business(ptcInput) } } catch (error) { return { logs: [], error: { kind: 'exception', message: error.message } } } }, release: async () => {} })
    ctx.provide('workingDirectory', { ensure: async () => tmpdir() }); await ctx.plugin(Ptc)
  } else await ctx.plugin(Native)
  const state = { turn: 1, host: 'allow', user: 'allowed-once', actor: true, reads: 0, writes: 0, asks: [], events: [], beforeAnswer: async () => {}, beforeCommit: async () => {}, item: { id: 'synthetic-id', name: 'Synthetic item', revision: 1, amount_minor: 123, currency: 'CNY', occurred_at: '2026-11-06T09:00:00Z' }, receipt: null }
  const agent = { ctx, session: { id: 'synthetic-session', header: {}, append: (type, data) => { state.events.push({ type, data }); return state.events.length } } }
  const business = async request => { state.reads++; return request.method === 'get_ledger' ? state.item : state.receipt }
  const actorStillAuthorized = execution => state.actor && ctx.tools.executable(execution.name, execution.agent, execution.toolSet) !== undefined && ctx.tools.checkGuards(execution) === undefined
  const gate = createBillDeleteGate({ business, getApproval: () => state.provider === undefined ? ctx.approval : state.provider, actorStillAuthorized, trustedTurnOf: () => state.turn,
    commitApproved: async (request, authorize) => { await state.beforeCommit(); if (!authorize()) throw new Error('revoked'); if (request.replay) { if (state.receipt?.result_id !== request.id) throw new Error('receipt conflict'); return { replayed: true, id: request.id, receipt: state.receipt, targetConflict: !!state.item } } if (state.item?.revision !== request.revision) throw new Error('revision conflict'); state.writes++; state.item = null; state.receipt = { method: 'delete_ledger', status: 'succeeded', result_id: request.id }; return { deleted: true, id: request.id } } })
  ctx.tools.register(defineTool({ name: 'alcor_business', description: 'Synthetic business',
    parameters: { domain: { type: 'string', required: true }, method: { type: 'string', required: true }, arguments: { type: 'object', additionalProperties: true } },
    output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] }, execute: (_args, execution) => gate.execute(execution) }))
  ctx.on('tools/pre-execute', (execution, next) => gate.preExecute(execution, next), { prepend: true })
  ctx.on('tools/pre-execute', async (execution, next) => execution.name !== 'alcor_business' ? next()
    : state.host === 'ask' ? { kind: 'ask', reason: 'Host reason', displayReason: { en: 'Host English', zh: '宿主说明' } }
      : state.host === 'deny' ? { kind: 'deny', reason: 'Host denial' }
        : state.host === 'cancel' ? { kind: 'cancel' } : next())
  ctx.on('tools/result', execution => gate.release(execution))
  ctx.on('approval/request', async (request, next) => { if (state.user === 'no-channel') return next(); state.asks.push(request); await state.beforeAnswer(request); return state.user })
  const call = async (callId = 'synthetic-call', signal = AbortSignal.timeout(5000)) => {
    ptcInput = { domain: 'bills', method: 'delete_ledger', arguments: { id: 'synthetic-id' } }
    const block = mode === 'ptc' ? { type: 'tool-call', id: callId, name: 'run_code', arguments: JSON.stringify({ code: 'synthetic', description: 'Delete one bill' }) }
      : { type: 'tool-call', id: callId, name: 'alcor_business', arguments: JSON.stringify(ptcInput) }
    return (await ctx.tools.execute({ agent, signal, calls: [block] })).results[0].result
  }
  return { ctx, state, call, agent }
}


for (const mode of ['native', 'ptc']) test(`${mode}: bill deletion is unavailable regardless of automatic approval outcome`, async t => {
  for (const host of ['allow', 'ask', 'deny', 'cancel']) {
    const f = await fixture(t, mode); f.state.host = host
    for (const user of ['allowed-once', 'allowed-for-session', 'rejected', 'cancelled', 'no-channel']) {
      f.state.user = user
      assert.equal((await f.call()).isError, true)
      assert.equal(f.state.writes, 0); assert.equal(f.state.reads, 0); assert.equal(f.state.asks.length, 0)
    }
  }
})
