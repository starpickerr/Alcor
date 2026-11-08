import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools, { defineTool } from '@deepseek-ai/dsh-tools'
import Approval from '@deepseek-ai/dsh-user-approval'
import * as Native from '@deepseek-ai/dsh-tool-executor-native'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'
import { createLocalDeleteGate } from '../../packages/alcor-secretary/src/security-local-delete-gate.js'

async function fixture(t) {
  const output = await mkdtemp(join(process.cwd(), '.alcor-delete-test-'))
  const root = await mkdtemp(join(tmpdir(), 'alcor-delete-test-'))
  const worker = new BusinessWorker(root, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose(); await worker.close(); await rm(root, { recursive: true, force: true }); await rm(output, { recursive: true, force: true }) })
  const file = join(output, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  const Organizer = (await import(pathToFileURL(file).href)).default
  const organizer = Object.create(Organizer.prototype)
  organizer.active = true; organizer.queue = Promise.resolve(); organizer.businessWorker = worker
  ctx.provide('sessionProjections', { register: async () => ({ stateOf: () => ({ turnOpen: true, policy: null }) }) })
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(Approval, {}); await ctx.plugin(Native)
  const state = { actor: true, turn: 1, outcome: 'allowed-once', asks: [], host: 'allow', beforeAnswer: async () => {} }
  organizer.ctx = { get: name => name === 'alcorAppleSync' ? state.apple : undefined }
  const agent = { ctx, session: { id: 'synthetic-session', header: {}, append() {} } }
  const gate = createLocalDeleteGate({ prepare: (request, authorize) => organizer.prepareApprovedDelete(request, authorize),
    commit: (request, authorize) => organizer.commitApprovedDelete(request, authorize), getApproval: () => ctx.approval,
    trustedTurnOf: () => state.turn, actorStillAuthorized: execution => state.actor && ctx.tools.checkGuards(execution) === undefined })
  ctx.tools.register(defineTool({ name: 'alcor_business', description: 'Synthetic business',
    parameters: { domain: { type: 'string', required: true }, method: { type: 'string', required: true }, arguments: { type: 'object', additionalProperties: true } },
    output: { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] }, execute: (_args, execution) => gate.execute(execution) }))
  ctx.on('tools/pre-execute', (execution, next) => gate.preExecute(execution, next), { prepend: true })
  ctx.on('tools/pre-execute', (execution, next) => state.host === 'deny' ? { kind: 'deny', reason: 'Host denial' } : state.host === 'ask' ? { kind: 'ask', reason: 'Host reason' } : next())
  ctx.on('tools/result', execution => gate.release(execution))
  ctx.on('approval/request', async request => { state.asks.push(request.reason); await state.beforeAnswer(); return state.outcome })
  const call = async (domain, method, args, callId = 'synthetic-call') => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: callId, name: 'alcor_business', arguments: JSON.stringify({ domain, method, arguments: args }) }] })).results[0].result
  return { state, worker, call, organizer }
}

test('local gate requests the exact local plan, but rejection, cancellation and host denial preserve it', async t => {
  for (const outcome of ['rejected', 'cancelled', 'unavailable', 'allowed', 'host-deny']) {
    const f = await fixture(t)
    const row = await f.worker.call('invoke', { domain: 'tasks', method: 'create_todo', arguments: { payload: { title: 'Synthetic parent' } } })
    if (outcome === 'host-deny') f.state.host = 'deny'; else f.state.outcome = outcome
    assert.equal((await f.call('tasks', 'delete_todo', { todo_id: row.id })).isError, true)
    assert.equal(f.state.asks.length, outcome === 'host-deny' ? 0 : 1)
    if (f.state.asks.length) { assert.match(f.state.asks[0], /Synthetic parent/); assert.match(f.state.asks[0], /本地版本/) }
    assert.equal((await f.worker.call('invoke', { domain: 'tasks', method: 'get_todo', arguments: { todo_id: row.id } })).id, row.id)
  }
})

test('reversible local future cutoff retains host decisions, snapshot CAS and undo', async t => {
  for (const scenario of ['allow', 'reject', 'host-deny', 'actor', 'change']) {
    const f = await fixture(t)
    const invoke = (method, args) => f.worker.call('invoke', { domain: 'calendar', method, arguments: args })
    const row = await invoke('create_event', { title: 'Synthetic series', start_at: '2026-11-01T09:00:00Z', end_at: '2026-11-01T10:00:00Z', repeat_frequency: 'daily', repeat_timezone: 'UTC' })
    if (scenario === 'reject') f.state.outcome = 'rejected'
    if (scenario === 'host-deny') f.state.host = 'deny'
    if (scenario === 'actor') f.state.beforeAnswer = async () => { f.state.actor = false }
    if (scenario === 'change') f.state.beforeAnswer = async () => { await invoke('update_event', { id: row.id, patch: { title: 'Changed' } }) }
    const result = await f.call('calendar', 'delete_event', { id: row.id, scope: 'future', occurrence_on: '2026-11-03' })
    assert.equal(result.isError, scenario !== 'allow', JSON.stringify(result))
    const saved = await invoke('get_event', { id: row.id })
    assert.equal(saved.repeat_until, scenario === 'allow' ? '2026-11-02' : null)
    if (scenario === 'allow') {
      assert.match(f.state.asks[0], /保留原系列/)
      await invoke('update_event', { id: row.id, patch: { repeat_until: null } })
      assert.equal((await invoke('get_event', { id: row.id })).repeat_until, null)
    }
  }
})
