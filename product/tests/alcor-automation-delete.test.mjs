import test, { before, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'
import { z } from 'zod'
import { Context } from '@deepseek-ai/cordis'
import { TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import Typert from '@deepseek-ai/dsh-typert-registry'
import Gateway from '@deepseek-ai/dsh-api-gateway'
import RemoteAccess from '@deepseek-ai/dsh-api-remote-access'
import { HostConnectionService } from '@deepseek-ai/dsh-client-connection'
import { remote as organizerRemote } from '../../packages/alcor-organizer/src/remote.js'
import { createAutomationPageRemote } from '../../packages/alcor-organizer/src/page-tools.js'
import { bindScopeParent, createScope, scopeTarget } from '@deepseek-ai/dsh-scope'
import Storage from '@deepseek-ai/dsh-storage'
import * as Json from '@deepseek-ai/dsh-storage-json'
import * as Domain from '@deepseek-ai/dsh-storage-domain'
import Schedule from '@deepseek-ai/dsh-schedule'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
import Approval from '@deepseek-ai/dsh-user-approval'
import * as Native from '@deepseek-ai/dsh-tool-executor-native'
import * as Ptc from '@deepseek-ai/dsh-tool-executor-ptc'
import * as NativeScheduleTools from '@deepseek-ai/dsh-tool-schedule'
import { apply } from '../../packages/alcor-secretary/src/tools.js'

let Organizer, output
before(async () => {
  output = await mkdtemp(join(process.cwd(), '.alcor-automation-build-'))
  const outfile = join(output, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  Organizer = (await import(pathToFileURL(outfile))).default
})
after(async () => { if (output) await rm(output, { recursive: true, force: true }) })
const stamp = '2026-11-07T00:00:00.000Z'
const ownership = Domain.defineDomain({ name: 'synthetic_ownership', version: 1, tables: { schedules: Domain.domainTable(z.object({ id: z.string() })) } })
const deferred = () => { let resolve; return { promise: new Promise(r => { resolve = r }), resolve } }

async function fixture(t, mode = 'native') {
  const root = await mkdtemp(join(tmpdir(), 'alcor-schedule-synthetic-'))
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose(); await rm(root, { recursive: true, force: true }) })
  const state = { turn: 1, answer: 'allowed-once', host: 'allow', asks: [], events: [], beforeAnswer: async () => {} }
  ctx.provide('agents', { withoutInitiator: fn => fn(), get: () => undefined, turnBoundaryOf: () => ({ openTurnStartSeq: state.turn, lastTurn: state.turn }) })
  ctx.provide('sessions', {}); ctx.provide('sessionPersistence', {})
  ctx.provide('sessionProjections', { register: async () => ({ stateOf: () => ({ turnOpen: true, policy: null }) }) })
  await ctx.plugin(Storage); await ctx.plugin(Json, { root }); await ctx.plugin(Domain, { backend: 'json' }); await ctx.plugin(Schedule, {})
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(Approval, {})
  let ptcInput, ptcName = 'alcor_business'
  if (mode === 'ptc') {
    ctx.provide('ptcRuntime', { language: 'typescript', programFormat: 'function-body', isolation: 'synthetic', executionInstructions: '', resolve: value => value,
      run: async request => { try { return { logs: [], value: await request.bindings[0].functions[ptcName](ptcInput) } } catch (error) { return { logs: [], error: { kind: 'exception', message: error.message } } } }, release: async () => {} })
    ctx.provide('workingDirectory', { ensure: async () => root }); await ctx.plugin(Ptc)
  } else await ctx.plugin(Native)
  // Use the actual Organizer methods on a real Service without starting unrelated business workers.
  class TestOrganizer extends TypertRemoteService {
    static inject = ['schedule']
    constructor(ctx) { super(ctx, 'alcorOrganizer'); this.active = true; this.queue = Promise.resolve() }
  }
  for (const name of ['assertActive', 'serialize', 'automation', 'approvedAutomationDelete', 'business', 'editSchedule']) {
    Object.defineProperty(TestOrganizer.prototype, name, Object.getOwnPropertyDescriptor(Organizer.prototype, name))
  }
  await ctx.plugin(TestOrganizer)
  const owner = ctx.alcorOrganizer
  owner.domain = await ctx.storageDomain.open(ownership)
  ctx.provide('alcorSecretary', {})
  const agent = { ctx, options: { subagentDepth: 0 }, session: { id: 'synthetic-session', header: {}, append: (type, data) => { state.events.push({ type, data }); return state.events.length } } }
  const preset = {}; bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  ctx.on('tools/pre-execute', (execution, next) => execution.name !== 'alcor_business' || state.host === 'allow' ? next()
    : { kind: state.host, reason: 'Native host policy', ...(state.host === 'ask' ? { displayReason: { en: 'Native host reason' } } : {}) })
  ctx.on('approval/request', async (request, next) => { state.asks.push(request); await state.beforeAnswer(request); return state.answer === 'no-channel' ? next() : state.answer })
  const turn = kind => ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: kind ? [{ source: { kind } }] : [] }, async () => {})
  await turn('user')
  const tasks = ctx.storageDomain.get('schedule').table('tasks')
  const seed = async (id = 'synthetic-task', own = true) => {
    await tasks.put(id, { record: { id, kind: 'at', title: `Synthetic ${id}`, prompt: 'Synthetic task only', scheduledAt: '2099-01-01T00:00:00.000Z' }, status: 'ended', updatedAt: stamp, target: { kind: 'new', create: {} }, involvedSessionIds: [], deliveryHistory: { records: [], earlierRecordsUnavailable: false, earlierRecordsPruned: false } })
    if (own) await owner.domain.table('schedules').put(id, { id })
    return id
  }
  const call = async (args = { id: 'synthetic-task', action: 'delete' }, options = {}) => {
    const { method = 'edit', callId = 'synthetic-call', signal = AbortSignal.timeout(5000), actor = agent, name = 'alcor_business' } = options
    ptcName = name
    ptcInput = name === 'alcor_business' ? { domain: 'automation', method, arguments: args } : args
    const block = mode === 'ptc' ? { type: 'tool-call', id: callId, name: 'run_code', arguments: JSON.stringify({ code: 'synthetic', description: 'Delete synthetic schedule' }) }
      : { type: 'tool-call', id: callId, name, arguments: JSON.stringify(ptcInput) }
    return (await ctx.tools.execute({ agent: actor, signal, calls: [block] })).results[0].result
  }
  return { ctx, owner, agent, state, tasks, seed, call, turn }
}

async function gatewayFixture(t) {
  const f = await fixture(t)
  await f.ctx.plugin(Typert); await f.ctx.plugin(RemoteAccess); await f.ctx.plugin(Gateway, {})
  f.ctx.typert.register({ package: '@alcor/organizer', face: 'host', schemas: [], invocations: organizerRemote.descriptors, model: { services: [], events: [], objects: [] } })
  // Real admission + Gateway; only authentication material is synthetic, never a user's cookie.
  const auth = { valid: true, isAuthenticated() { return this.valid }, authenticateApi() {} }
  await f.ctx.plugin(ctx => { new HostConnectionService(ctx, [], auth) })
  const request = { headers: { host: 'localhost', origin: 'http://localhost' } }
  const admission = f.ctx.connection.admit(request)
  assert.equal(admission.peer.principal.kind, 'owner')
  auth.valid = false; assert.equal(f.ctx.connection.admit(request).rejection, 401); auth.valid = true
  assert.equal(f.ctx.connection.admit({ headers: { host: 'attacker.invalid', origin: 'http://attacker.invalid' } }).rejection, 403)
  const invoke = (input, peer = admission.peer, signal) => f.ctx.typertGateway.invoke({ namespace: 'alcorOrganizer', method: 'automation', args: { request: input }, peer, signal })
  const api = { automation: async input => ({ ok: true, value: await invoke(input) }) }
  return { ...f, invoke, api, peer: admission.peer }
}

for (const mode of ['native', 'ptc']) test(`${mode}: regression preserves a real pendingDelivery hidden by public view and history`, async t => {
  const f = await fixture(t, mode); await f.seed()
  const before = await f.ctx.schedule.view({ id: 'synthetic-task' })
  await assert.rejects(f.ctx.schedule.run({ id: 'synthetic-task' }), /no Session creation provider/)
  const after = await f.ctx.schedule.view({ id: 'synthetic-task' })
  assert.deepEqual(after, before)
  assert.deepEqual((await f.ctx.schedule.history({ id: 'synthetic-task', limit: 1 })).records, [])
  const syntheticStored = structuredClone(f.tasks.get('synthetic-task'))
  assert(syntheticStored.pendingDelivery?.targetSessionId)
  assert.equal((await f.call()).isError, true)
  assert.deepEqual(f.tasks.get('synthetic-task'), syntheticStored)
})

for (const mode of ['native', 'ptc']) {
  test(`${mode}: empty history never permits deletion, even with automatic host approval`, async t => {
    const f = await fixture(t, mode); await f.seed()
    const stored = structuredClone(f.tasks.get('synthetic-task'))
    for (const host of ['allow', 'ask', 'deny', 'cancel']) {
      f.state.host = host
      for (const answer of ['allowed-once', 'allowed-for-session', 'rejected', 'cancelled', 'no-channel']) {
        f.state.answer = answer
        assert.equal((await f.call()).isError, true)
        assert.equal((await f.call({}, { method: 'deleteInactive', callId: 'batch' })).isError, true)
        assert.deepEqual(f.tasks.get('synthetic-task'), stored)
      }
    }
    assert.equal(f.state.asks.length, 0)
  })
  test(`${mode}: deletion refusal does not pause, while explicit editing and pause remain available`, async t => {
    const f = await fixture(t, mode); await f.seed()
    const initial = structuredClone(f.tasks.get('synthetic-task'))
    assert.equal((await f.call()).isError, true)
    assert.deepEqual(f.tasks.get('synthetic-task'), initial)
    const edited = await f.call({ id: 'synthetic-task', action: 'update', patch: { title: 'Explicit edit' } }, { callId: 'edit' })
    assert.equal(edited.isError, false); assert.equal((await f.ctx.schedule.view({ id: 'synthetic-task' })).title, 'Explicit edit')
    const paused = await f.call({ id: 'synthetic-task', action: 'update', patch: { active: false } }, { callId: 'pause' })
    assert.equal(paused.isError, false); assert.equal((await f.ctx.schedule.view({ id: 'synthetic-task' })).status, 'paused')
    assert.equal((await f.call({}, { method: 'list', callId: 'list' })).isError, false)
    assert.equal((await f.call({ id: 'synthetic-task', limit: 1 }, { method: 'history', callId: 'history' })).isError, false)
    f.ctx.provide('sessionController', { create: async () => assert.fail('No actual session creation expected') })
    const resumed = await f.call({ id: 'synthetic-task', action: 'update', patch: { active: true } }, { callId: 'resume' })
    assert.equal(resumed.isError, false); assert.equal((await f.ctx.schedule.view({ id: 'synthetic-task' })).status, 'active')
    assert.equal(f.state.asks.length, 0)
  })
  test(`${mode}: scheduled and delegated callers cannot delete or obtain approval`, async t => {
    const f = await fixture(t, mode); await f.seed(); const stored = structuredClone(f.tasks.get('synthetic-task'))
    for (const kind of ['schedule', 'mixed']) { f.state.turn++; await f.turn(kind); assert.equal((await f.call()).isError, true) }
    f.agent.session.header.origin = 'subagent'; assert.equal((await f.call()).isError, true)
    assert.deepEqual(f.tasks.get('synthetic-task'), stored); assert.equal(f.state.asks.length, 0)
  })
}

test('real owner Gateway, business, compatibility route and host callback all preserve pending and empty rows', async t => {
  const f = await gatewayFixture(t)
  for (const kind of ['empty', 'pending', 'record', 'pruned', 'unknown']) {
    await f.seed(kind)
    if (kind === 'pending') await assert.rejects(f.ctx.schedule.run({ id: kind }), /no Session creation provider/)
    if (['record', 'pruned', 'unknown'].includes(kind)) {
      const row = f.tasks.get(kind)
      await f.tasks.put(kind, { ...row, deliveryHistory: { records: kind === 'record' ? [{ scheduledAt: stamp, deliveredAt: stamp, messageId: 'synthetic-delivery' }] : [], earlierRecordsUnavailable: kind === 'unknown', earlierRecordsPruned: kind === 'pruned' } })
    }
    const stored = structuredClone(f.tasks.get(kind)), target = { id: kind, expectedUpdatedAt: stamp }
    for (const scope of ['alcor', 'all']) {
      await assert.rejects(f.invoke({ scope, action: 'edit', request: { ...target, action: 'delete' } }), /暂不支持删除自动化/)
      await assert.rejects(f.invoke({ scope, action: 'deleteInactive', targets: [target] }), /暂不支持删除自动化/)
      assert.throws(() => f.owner.approvedAutomationDelete({ scope, targets: [target] }, () => true, AbortSignal.timeout(5000)), /暂不支持删除自动化/)
    }
    for (const [method, request] of [
      ['business', { domain: 'automation', method: 'edit', arguments: { ...target, action: 'delete' } }],
      ['editSchedule', { ...target, action: 'delete' }],
    ]) await assert.rejects(f.ctx.typertGateway.invoke({ namespace: 'alcorOrganizer', method, args: { request }, peer: f.peer }), /暂不支持删除自动化/)
    assert.deepEqual(f.tasks.get(kind), stored)
  }
  await assert.rejects(f.invoke({ scope: 'all', action: 'deleteInactive', targets: [] }), /暂不支持删除自动化/)
})

test('deletion does not rely on any read result and never turns itself into a pause', async t => {
  const f = await fixture(t); await f.seed(); const stored = structuredClone(f.tasks.get('synthetic-task'))
  let accesses = 0
  for (const name of ['view', 'history', 'catalog', 'edit']) f.ctx.schedule[name] = () => { accesses++; throw new Error('Synthetic inaccessible host') }
  const result = await f.call(); assert.equal(result.isError, true); assert.match(JSON.stringify(result), /可选择暂停/)
  await assert.rejects(f.owner.automation({ scope: 'alcor', action: 'edit', request: { id: 'synthetic-task', action: 'delete' } }), /暂不支持删除自动化/)
  assert.throws(() => f.owner.approvedAutomationDelete({}, () => true), /暂不支持删除自动化/)
  assert.equal(accesses, 0); assert.deepEqual(f.tasks.get('synthetic-task'), stored)
})

test('explicit owner pause and edit preserve a real pending reservation', async t => {
  const f = await gatewayFixture(t); await f.seed()
  await assert.rejects(f.ctx.schedule.run({ id: 'synthetic-task' }), /no Session creation provider/)
  const reserved = structuredClone(f.tasks.get('synthetic-task').pendingDelivery)
  const paused = await f.invoke({ scope: 'alcor', action: 'edit', request: { id: 'synthetic-task', action: 'update', patch: { active: false } } })
  assert.equal(paused.status, 'paused'); assert.deepEqual(f.tasks.get('synthetic-task').pendingDelivery, reserved)
  const edited = await f.invoke({ scope: 'alcor', action: 'edit', request: { id: 'synthetic-task', action: 'update', patch: { title: 'Explicit updated title' } } })
  assert.equal(edited.title, 'Explicit updated title'); assert.deepEqual(f.tasks.get('synthetic-task').pendingDelivery, reserved)
})

test('Alcor page refuses deletion without confirmation UI or Remote writes; ordinary edit is forwarded', async () => {
  const calls = []; let confirmations = 0
  const adapter = createAutomationPageRemote({ api: { automation: async request => { calls.push(request); return { ok: true, value: {} } } }, scope: 'alcor', schedule: {}, confirm: async () => { confirmations++; return true } })
  adapter.activate()
  await assert.rejects(adapter.remote.edit({ id: 'synthetic', action: 'delete' }), /可选择暂停/)
  await assert.rejects(adapter.remote.deleteInactive(), /暂不支持删除自动化/)
  adapter.dispose(); await assert.rejects(adapter.remote.deleteInactive(), /暂不支持删除自动化/)
  assert.equal(confirmations, 0); assert.deepEqual(calls, [])
  adapter.activate(); await adapter.remote.edit({ id: 'synthetic', action: 'update', patch: { active: false } })
  assert.deepEqual(calls, [{ scope: 'alcor', action: 'edit', request: { id: 'synthetic', action: 'update', patch: { active: false } } }])
})

for (const mode of ['native', 'ptc']) test(`${mode}: Alcor native-tool bypass is denied while non-Alcor deletion and ordinary updates remain unchanged`, async t => {
  const f = await fixture(t, mode); await f.seed(); await f.ctx.plugin(NativeScheduleTools)
  const call = (args, actor = f.agent) => f.call(args, { name: 'schedule_edit', actor })
  const stored = structuredClone(f.tasks.get('synthetic-task'))
  const denied = await call({ id: 'synthetic-task', action: 'delete' })
  assert.equal(denied.isError, true); assert.deepEqual(f.tasks.get('synthetic-task'), stored)
  const update = await call({ id: 'synthetic-task', action: 'update', title: 'Normal update' })
  assert.equal(update.isError, false)
  const other = { ctx: f.ctx, options: { subagentDepth: 0 }, session: { id: 'non-alcor-session', header: {}, append: () => 1 } }
  const outside = await call({ id: 'synthetic-task', action: 'delete' }, other)
  assert.equal(outside.isError, false); assert.equal(f.tasks.get('synthetic-task'), undefined)
})

test('native Schedule Remote used by host UI is unchanged, without claiming its owner peer proves a human click', async t => {
  const f = await gatewayFixture(t)
  const { TYPERT } = await import('@deepseek-ai/dsh-schedule/typert')
  f.ctx.typert.register(TYPERT)
  await f.seed('native-ui'); await assert.rejects(f.ctx.schedule.run({ id: 'native-ui' }), /no Session creation provider/)
  assert(f.tasks.get('native-ui').pendingDelivery)
  const result = await f.ctx.typertGateway.invoke({ namespace: 'schedule', method: 'edit', args: { request: { id: 'native-ui', action: 'delete', expectedUpdatedAt: stamp } }, peer: f.peer })
  assert.equal(result.deleted, true); assert.equal(f.tasks.get('native-ui'), undefined)
})

test('standalone automation gate cannot prepare or execute deletion through injected approval callbacks', async () => {
  const { createAutomationDeleteGate } = await import('../../packages/alcor-secretary/src/security-automation-gate.js')
  let calls = 0
  const gate = createAutomationDeleteGate({ catalog: () => { calls++; return [] }, getApproval: () => ({ request: async () => { calls++; return 'allowed-once' } }), commitApproved: () => { calls++; return {} } })
  const execution = { name: 'alcor_business', arguments: { domain: 'automation', method: 'deleteInactive', arguments: {} } }
  assert.equal((await gate.preExecute(execution, async () => { calls++; return { kind: 'allow' } })).kind, 'deny')
  await assert.rejects(gate.execute(execution), /暂不支持删除自动化/)
  assert.equal(calls, 0)
  assert.deepEqual(await gate.preExecute({ name: 'other' }, async () => ({ kind: 'allow' })), { kind: 'allow' })
})


test('shared Alcor page lists all native schedules while new templates retain Alcor ownership', async () => {
  const calls=[]
  const adapter=createAutomationPageRemote({api:{automation:async request=>{calls.push(request);return {ok:true,value:[]}}},scope:'all',createScope:'alcor',schedule:{}})
  await adapter.remote.catalog()
  const request={title:'Synthetic shared template',prompt:'No external action',timing:{kind:'daily',daily:{time:'08:00',time_zone:'Asia/Shanghai'}}}
  await adapter.remote.create(request)
  assert.deepEqual(calls,[{scope:'all',action:'catalog'},{scope:'alcor',action:'create',request}])
  await assert.rejects(adapter.remote.edit({id:'synthetic',action:'delete'}),/暂不支持删除/)
  assert.equal(calls.length,2)
})
