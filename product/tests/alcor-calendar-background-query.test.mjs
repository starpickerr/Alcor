import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import { RemoteError } from '@deepseek-ai/dsh-typert-protocol'
import { bindScopeParent, createScope, scopeTarget } from '@deepseek-ai/dsh-scope'
import Storage from '@deepseek-ai/dsh-storage'
import * as Json from '@deepseek-ai/dsh-storage-json'
import * as Domain from '@deepseek-ai/dsh-storage-domain'
import Schedule from '@deepseek-ai/dsh-schedule'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
import * as Native from '@deepseek-ai/dsh-tool-executor-native'
import * as Ptc from '@deepseek-ai/dsh-tool-executor-ptc'
import { apply } from '../../packages/alcor-secretary/src/tools.js'

for (const mode of ['native','ptc']) test(`${mode}: real scheduler delivery cannot refresh Apple through calendar query`, async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-calendar-schedule-')), ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose(); await rm(root, { recursive: true, force: true }) })
  const agents = new Map(), messages = [], calls = [], state = { turn: 1 }
  ctx.provide('agents', { withoutInitiator: fn => fn(), turnBoundaryOf: () => ({ openTurnStartSeq: state.turn, lastTurn: state.turn }) })
  ctx.provide('sessions', { flush: async () => true }); ctx.provide('sessionPersistence', {})
  ctx.provide('sessionController', { create: async request => agents.set(request.sessionId, { ctx, session: { id: request.sessionId, header: {}, append: () => 1 }, followup: message => messages.push(message), whenIdle: () => Promise.resolve() }) })
  ctx.on('schedule/resolve-agent', async id => { if (!agents.has(id)) throw new RemoteError('session/not-found', 'Synthetic session absent'); return agents.get(id) })
  await ctx.plugin(Storage); await ctx.plugin(Json, { root }); await ctx.plugin(Domain, { backend: 'json' }); await ctx.plugin(Schedule, {}); await ctx.schedule.initialized
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools)
  let ptcInput
  if (mode === 'ptc') {
    ctx.provide('ptcRuntime', { language: 'typescript', programFormat: 'function-body', isolation: 'synthetic', executionInstructions: '', resolve: x => x,
      run: async request => { try { return { logs: [], value: await request.bindings[0].functions.alcor_business(ptcInput) } } catch (e) { return { logs: [], error: { kind: 'exception', message: e.message } } } }, release: async () => {} })
    ctx.provide('workingDirectory', { ensure: async () => root }); await ctx.plugin(Ptc)
  } else await ctx.plugin(Native)
  ctx.provide('alcorSecretary', {})
  ctx.provide('alcorOrganizer', { business: async () => { calls.push('refresh'); return [] }, localCalendarQuery: async () => { calls.push('local'); return [] } })
  const preset = {}; await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools','systemPrompt','alcorSecretary','alcorOrganizer'] }))
  const scheduled = { record: { id:'synthetic-schedule', kind:'at', title:'Synthetic', prompt:'Read local calendar', scheduledAt:'2099-01-01T00:00:00.000Z' }, status:'active', updatedAt:'2026-01-01T00:00:00.000Z', target:{ kind:'new_once', sessionId:'synthetic-session', create:{agentPreset:'alcor'} }, involvedSessionIds:[], deliveryHistory:{ records:[], earlierRecordsUnavailable:false } }
  await ctx.storageDomain.get('schedule').table('tasks').put(scheduled.record.id, scheduled); await ctx.schedule.run({ id: scheduled.record.id })
  assert.equal(messages.length, 1); assert.equal(messages[0].source.kind, 'schedule')
  const agent = agents.get('synthetic-session'); bindScopeParent(agent, preset)
  const step = source => ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: source ? [{ source: { kind: source } }] : [] }, async () => {})
  await ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages }, async () => {})
  const call = async () => { ptcInput = { domain:'calendar', method:'list_events', arguments:{ start:'2026-01-01', end:'2026-01-02' } }
    const block = mode==='ptc' ? { type:'tool-call', id:'synthetic', name:'run_code', arguments:JSON.stringify({code:'synthetic',description:'Read local calendar'}) } : { type:'tool-call', id:'synthetic', name:'alcor_business', arguments:JSON.stringify(ptcInput) }
    return (await ctx.tools.execute({ agent, signal:AbortSignal.timeout(5000), calls:[block] })).results[0].result }
  assert.equal((await call()).isError, true); assert.deepEqual(calls,[])
  state.turn++; await step('user'); assert.equal((await call()).isError, false); assert.deepEqual(calls,['refresh'])
  agent.session.header = { origin:'subagent', parentSession:'parent' }; assert.equal((await call()).isError, true); assert.deepEqual(calls,['refresh'])
  agent.session.header = {}; state.turn++; await step(null); assert.equal((await call()).isError, true); assert.deepEqual(calls,['refresh'])
})
