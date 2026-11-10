import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'
import { Context } from '@deepseek-ai/cordis'
import { bindScopeParent, createScope, scopeTarget } from '@deepseek-ai/dsh-scope'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
import Approval from '@deepseek-ai/dsh-user-approval'
import * as Native from '@deepseek-ai/dsh-tool-executor-native'
import * as Ptc from '@deepseek-ai/dsh-tool-executor-ptc'
import { apply } from '../../packages/alcor-secretary/src/tools.js'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

for (const mode of ['native', 'ptc']) test(`${mode}: real secretary tool gate reaches real worker owner and honors host decisions`, async t => {
  const dir = await mkdtemp(join(tmpdir(), 'alcor-bill-runtime-'))
  const worker = new BusinessWorker(dir, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  const ctx = new Context(); t.after(async () => { await ctx.fiber.dispose(); await worker.close(); await rm(dir, { recursive: true, force: true }) })
  ctx.provide('sessionProjections', { register: async () => ({ stateOf: () => ({ turnOpen: true, policy: null }) }) })
  let hostTurn = 1
  ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: hostTurn, lastTurn: hostTurn }) })
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(Approval, {})
  let ptcInput
  if (mode === 'ptc') {
    ctx.provide('ptcRuntime', { language: 'typescript', programFormat: 'function-body', isolation: 'synthetic', executionInstructions: '', resolve: value => value,
      run: async request => { try { return { logs: [], value: await request.bindings[0].functions.alcor_business(ptcInput) } } catch (e) { return { logs: [], error: { kind: 'exception', message: e.message } } } }, release: async () => {} })
    ctx.provide('workingDirectory', { ensure: async () => tmpdir() }); await ctx.plugin(Ptc)
  } else await ctx.plugin(Native)
  const state = { answer: 'allowed-once', beforeAnswer: async () => {}, beforeDispatch: async () => {}, asks: 0, sent: 0, details: [], host: 'allow' }
  const invoke = (method, args) => worker.call('invoke', { domain: 'bills', method, arguments: args })
  ctx.provide('alcorSecretary', {})
  const out = await mkdtemp(join(process.cwd(), '.bill-build-'))
  t.after(() => rm(out, { recursive: true, force: true }))
  const file = join(out, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  const Organizer = (await import(pathToFileURL(file).href)).default
  const organizer = Object.create(Organizer.prototype)
  organizer.ctx = ctx; organizer.active = true; organizer.queue = Promise.resolve(); organizer.businessWorker = worker
  ctx.provide('alcorOrganizer', organizer)
  const agent = { ctx, session: { id: 'synthetic-runtime', header: {}, append: () => 1 } }; const preset = {}; bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  const turn = kind => ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: kind ? [{ source: { kind } }] : [] }, async () => {})
  ctx.on('tools/pre-execute', async (execution, next) => execution.name === 'alcor_business' && state.host === 'deny' ? { kind: 'deny', reason: 'Host denied' } : next())
  ctx.on('approval/request', async (request, next) => { state.asks++; state.details.push(request.reason); await state.beforeAnswer(); return state.answer === 'no-channel' ? next() : state.answer })
  const make = () => invoke('create_ledger', { name: 'Synthetic runtime', amount_minor: 123, currency: 'CNY', entry_type: 'expense', occurred_at: '2026-11-06T09:00:00Z', category: 'Test' })
  const call = async (id, callId = 'call-1') => {
    ptcInput = { domain: 'bills', method: 'delete_ledger', arguments: { id } }
    const block = mode === 'ptc' ? { type: 'tool-call', id: callId, name: 'run_code', arguments: JSON.stringify({ code: 'synthetic', description: 'Delete bill' }) } : { type: 'tool-call', id: callId, name: 'alcor_business', arguments: JSON.stringify(ptcInput) }
    return (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [block] })).results[0].result
  }
  const stale = await make(); await turn('user'); hostTurn++; await turn(null)
  const priorAsks = state.asks; assert.equal((await call(stale.id, 'stale-turn')).isError, true); assert.equal(state.asks, priorAsks)
  const first = await make(); await turn('schedule'); assert.equal((await call(first.id)).isError, true)
  hostTurn++; await turn('user'); state.host = 'deny'; assert.equal((await call(first.id, 'host-deny')).isError, true); state.host = 'allow'
  ctx.approval.config.policy = 'never'; assert.equal((await call(first.id, 'never-policy')).isError, true); ctx.approval.config.policy = 'ask'
  state.answer = 'no-channel'; assert.equal((await call(first.id, 'no-channel')).isError, true)
  state.answer = 'rejected'; assert.equal((await call(first.id, 'reject')).isError, true)
  assert.equal((await invoke('get_ledger', { id: first.id })).name, 'Synthetic runtime')
  state.answer = 'allowed-for-session'; assert.equal((await call(first.id, 'session')).isError, true)
  state.answer = 'allowed-once'; state.beforeAnswer = async () => { hostTurn++ }
  assert.equal((await call(first.id, 'turn-changed')).isError, true)
  assert.equal((await invoke('get_ledger', { id: first.id })).name, 'Synthetic runtime')
  await turn('user')
  state.beforeAnswer = async () => { await invoke('update_ledger', { id: first.id, patch: { name: 'Changed during approval' }, expectedRevision: first.revision }) }
  assert.equal((await call(first.id, 'changed')).isError, true)
  assert.equal((await invoke('get_ledger', { id: first.id })).name, 'Changed during approval')
  state.beforeAnswer = async () => {}
  const result = await call(first.id, 'approved')
  assert.match(state.details.at(-1), /永久删除/); assert.match(state.details.at(-1), /Synthetic runtime|Changed during approval/); assert.match(state.details.at(-1), /CNY/); assert.match(state.details.at(-1), /无法撤销/); assert.match(state.details.at(-1), new RegExp(first.id))
  assert.equal(result.isError, false, JSON.stringify(result))
  assert.equal(await invoke('get_ledger', { id: first.id }), null)
  assert.equal((await call(first.id, 'approved')).isError, false, 'same operation is a receipt-only replay')
})
