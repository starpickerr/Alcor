import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
import * as NativeExecutor from '@deepseek-ai/dsh-tool-executor-native'
import { bindScopeParent, createScope, scopeTarget } from '@deepseek-ai/dsh-scope'
import * as McpClient from '@deepseek-ai/dsh-mcp-client'
import { fileURLToPath } from 'node:url'
import { createHash } from 'node:crypto'
import { apply } from '../../packages/alcor-secretary/src/tools.js'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'
import { recordSchema } from '../../packages/alcor-organizer/src/records.js'

test('trusted business tools write durable page objects and retain retry receipts', async context => {
  const directory = await mkdtemp(join(tmpdir(), 'stella-tool-business-'))
  const worker = new BusinessWorker(directory, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  const ctx = new Context(); ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: simulatedTurn, lastTurn: simulatedTurn }) }); let simulatedTurn = 0
  context.after(async () => { await ctx.fiber.dispose(); await worker.close(); await rm(directory, { recursive: true, force: true }) })
  await ctx.plugin(SystemPrompt, {})
  await ctx.plugin(Tools)
  await ctx.plugin(NativeExecutor)
  ctx.provide('alcorSecretary', { mcpAllowed: () => false })
  const forwarded = []
  ctx.provide('alcorOrganizer', { business: async input => {
    forwarded.push(input)
    if (input.domain === 'briefing' || input.method === 'run_workflow') return { accepted: true }
    return worker.call('invoke', { domain: input.domain, method: input.method, arguments: input.arguments, request_id: input.requestId })
  } })
  const preset = {}
  const agent = { session: { id: 'trusted-user', header: {} } }
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  const call = async (input, id = 'business-durable-call') => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id, name: 'alcor_business', arguments: JSON.stringify(input) }] })).results[0].result
  simulatedTurn++; await ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind: 'user' } }] }, async () => {})
  const input = { domain: 'tasks', method: 'create_todo', arguments: { payload: { title: 'Created through Agent' } } }
  const first = await call(input)
  assert.equal(first.isError, false, JSON.stringify(first))
  assert.equal((await call(input)).isError, false)
  const records = await worker.call('records')
  assert.equal(records.length, 1)
  assert.equal(recordSchema.parse(records[0]).title, 'Created through Agent')
  assert.equal((await call({ ...input, arguments: { payload: { title: 'Different request' } } })).isError, true)
  agent.session.header = { origin: 'subagent', parentSession: 'synthetic-parent' }
  assert.equal((await call({ domain: 'briefing', method: 'generate', arguments: { date: '2026-11-05' } }, 'generate-once')).isError, true)
  assert.equal((await call({ domain: 'briefing', method: 'generate', arguments: { date: '2026-11-05' } }, 'generate-once')).isError, true)
  assert(!forwarded.some(input => input.domain === 'briefing'))
  agent.session.header = {}
  assert.equal((await call({ domain: 'briefing', method: 'configure_sources', arguments: { sources: [] } })).isError, true)
})

test('Stella inherits native tool visibility and host denials, including later tools', async context => {
  const ctx = new Context(); ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: simulatedTurn, lastTurn: simulatedTurn }) }); let simulatedTurn = 0
  context.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(NativeExecutor)
  ctx.provide('alcorSecretary', { mcpAllowed: () => false })
  let invocations = 0
  const register = name => ctx.tools.register({ name, description: 'Synthetic host tool', parameters: {},
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    execute: async () => { invocations++; return 'host-result' } })
  register('bash'); register('working_directory')
  const secretary = { session: { header: {} } }; const preset = {}
  bindScopeParent(secretary, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt'] }))
  const names = agent => ctx.tools.schemas(agent).map(tool => tool.name).sort()
  assert.deepEqual(names(secretary), ['alcor_memory_correct', 'alcor_memory_followup', 'alcor_memory_forget', 'alcor_memory_recall', 'alcor_memory_reflect', 'alcor_memory_remember', 'alcor_overview', 'bash', 'working_directory'])
  register('later_installed_executor')
  assert(names(secretary).includes('later_installed_executor'))
  const call = (agent, name) => ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'host-probe', name, arguments: '{}' }] })
  assert.equal((await call(secretary, 'bash')).results[0].result.isError, false)
  let hostScope
  await ctx.plugin(Object.assign(inner => { hostScope = createScope(inner, secretary) }, { inject: ['tools'] }))
  context.after(() => hostScope.dispose())
  const restriction = hostScope.ctx.tools.restrict({ deny: ['bash'] })
  assert(!names(secretary).includes('bash')); assert(names({}).includes('bash'))
  assert.equal((await call(secretary, 'bash')).results[0].result.isError, true)
  assert.equal(invocations, 1)
  restriction()
  assert(names(secretary).includes('bash'))
  const guard = ctx.tools.guard(execution => execution.name === 'later_installed_executor' ? 'DSH host denied' : undefined)
  const rejected = (await call(secretary, 'later_installed_executor')).results[0].result
  assert.equal(rejected.isError, true); assert(JSON.stringify(rejected).includes('DSH host denied'))
  assert.equal(invocations, 1); guard()
})

test('native MCP uses DSH policy directly while preserving personal turn and disconnect boundaries', async context => {
  const ctx = new Context(); ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: simulatedTurn, lastTurn: simulatedTurn }) }); let simulatedTurn = 0
  context.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(NativeExecutor)
  ctx.provide('alcorSecretary', { mcpAllowed: () => false })
  const preset = {}; const secretary = { session: { header: {} } }
  bindScopeParent(secretary, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary'] }))
  const fixture = fileURLToPath(new URL('./fixtures/mcp-server.mjs', import.meta.url))
  const connection = await ctx.plugin(McpClient, { transport: 'stdio', serverName: 'elearning', command: process.execPath, args: [fixture], failOnStartupError: true })
  const name = 'mcp__elearning__list_courses'
  const call = async (agent, signal = AbortSignal.timeout(5000)) => (await ctx.tools.execute({ agent, signal, calls: [{ type: 'tool-call', id: 'probe', name, arguments: '{}' }] })).results[0].result
  const turn = (agent, kind) => (simulatedTurn++, ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind } }] }, async () => {}))
  assert(ctx.tools.schemas(secretary).some(tool => tool.name === name))
  assert.equal((await call(secretary)).isError, true)
  await turn(secretary, 'user')
  const result = await call(secretary)
  assert.equal(result.isError, false); assert(JSON.stringify(result).includes('synthetic-course'))
  const child = { session: { header: { origin: 'subagent', parentSession: 'owner' } } }
  bindScopeParent(child, secretary); await turn(child, 'user')
  assert.equal((await call(child)).isError, true)
  await turn(secretary, 'schedule'); assert.equal((await call(secretary)).isError, true)
  await turn(secretary, 'user')
  const cancelled = new AbortController(); cancelled.abort()
  assert.equal((await call(secretary, cancelled.signal)).isError, true)
  const stopGate = ctx.on('tools/pre-execute', async (_execution, next) => {
    const decision = await next()
    return { kind: 'deny', reason: 'DSH host revoked permission' }
  })
  assert.equal((await call(secretary)).isError, true); stopGate()
  let hostScope
  await ctx.plugin(Object.assign(inner => { hostScope = createScope(inner, secretary) }, { inject: ['tools'] }))
  context.after(() => hostScope.dispose())
  const denied = hostScope.ctx.tools.restrict({ deny: [name] })
  assert.equal(ctx.tools.executable(name, secretary), undefined)
  assert.equal((await call(secretary)).isError, true); denied()
  assert.equal((await call(secretary)).isError, false)
  await connection.dispose()
  assert.equal(ctx.tools.executable(name, secretary), undefined)
  assert.equal((await call(secretary)).isError, true)
})

test('scheduled briefings use saved sources, deny delegated/unknown turns and reuse a delivery identity', async context => {
  const ctx = new Context(); ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: simulatedTurn, lastTurn: simulatedTurn }) }); let simulatedTurn = 0
  context.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {})
  await ctx.plugin(Tools)
  await ctx.plugin(NativeExecutor)
  const saved = []
  ctx.provide('alcorSecretary', { mcpAllowed: () => false })
  ctx.provide('alcorOrganizer', {
    business: async () => [{ source: 'alcor_tasks', accountId: 'local' }, { source: 'elearning', accountId: 'school' }],
    localBriefingSources: async () => assert.fail('Scheduled briefings must use the shared saved-source selection'),
    generateBriefing: async input => { saved.push(input); return { id: input.requestId, date: input.date, body: 'Synthetic saved briefing' } },
    startBriefing: async input => ({ requestId: input.requestId, phase: 'starting', sessionId: 'independent-root' }),
    taskReview: async () => ({ open: [{ title: 'Synthetic task' }], completed: 0 }),
  })
  const preset = {}
  const agent = { session: { header: {} } }
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => { apply(createScope(inner, preset).ctx) }, { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  const turn = (agent, kind) => (simulatedTurn++, ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind } }] }, async () => {}))
  const call = async (agent, name) => (await ctx.tools.execute({ agent, signal: new AbortController().signal, calls: [{ type: 'tool-call', id: 'business-probe', name, arguments: '{}' }] })).results[0].result
  assert.equal((await call(agent, 'alcor_generate_briefing')).isError, true)
  await turn(agent, 'schedule')
  assert.equal((await call(agent, 'alcor_generate_briefing')).isError, false)
  assert.equal((await call(agent, 'alcor_generate_briefing')).isError, false)
  assert.equal(saved.length, 2)
  assert.equal(saved[0].requestId, saved[1].requestId)
  await turn(agent, 'schedule')
  assert.equal((await call(agent, 'alcor_generate_briefing')).isError, false)
  assert.notEqual(saved[0].requestId,saved[2].requestId)
  assert.equal(Object.hasOwn(saved[0], 'sources'), false)
  assert.equal(saved[0].trigger, 'scheduled')
  const child = { session: { header: { origin: 'subagent', parentSession: 'parent' } } }
  bindScopeParent(child, agent)
  await turn(child, 'user')
  for (const name of ['alcor_generate_briefing', 'alcor_review_tasks']) assert.equal((await call(child, name)).isError, true)
  assert.equal(saved.length, 3)
  await turn(agent, 'unknown')
  assert.equal((await call(agent, 'alcor_review_tasks')).isError, true)
  await turn(agent, 'user')
  const started = await call(agent, 'alcor_generate_briefing')
  assert.equal(started.isError, false)
  assert(JSON.stringify(started).includes('independent-root'))
  assert(!JSON.stringify(started).includes('\"saved\":true'))
  assert.equal(saved.length, 3, 'Manual chat starts a task; only scheduled calls wait for business completion')
  const result = await call(agent, 'alcor_review_tasks')
  assert.equal(result.isError, false)
  assert(JSON.stringify(result).includes('Synthetic task'))
})

test('bill import is callable in user turns, writes ledger data directly, and refuses delegated/unknown calls', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'stella-bill-tool-'))
  const worker = new BusinessWorker(dir, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  const ctx = new Context(); ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: simulatedTurn, lastTurn: simulatedTurn }) }); let simulatedTurn = 0
  t.after(async () => { await ctx.fiber.dispose(); await worker.close(); await rm(dir, { recursive: true, force: true }) })
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(NativeExecutor)
  ctx.provide('alcorSecretary', { mcpAllowed: () => false })
  const csv = '交易时间,收/支,金额,交易状态,交易订单号,商品说明\n2026-11-01 12:00:00,支出,3.45,交易成功,synthetic-order,午餐'
  let reads = 0
  ctx.provide('fs', { resolve: async path => path, stat: async () => ({ type: 'file', size: 100 }), readBytes: async () => { reads++; return Buffer.from(csv) } })
  ctx.provide('alcorOrganizer', { business: request => worker.call('invoke', { domain: request.domain, method: request.method, arguments: request.arguments }) })
  const preset = {}; const agent = { ctx, session: { id: 'bill-user', header: {} } }
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer', 'fs'] }))
  const turn = (agent, kind) => (simulatedTurn++, ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind } }] }, async () => {}))
  const call = async agent => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'bill-import', name: 'alcor_import_bills', arguments: JSON.stringify({ paths: ['/allowed/支付宝.csv'] }) }] })).results[0].result
  const names = ctx.tools.schemas(agent).map(tool => tool.name)
  for (const name of ['overview', 'business', 'import_bills', 'import_dates', 'export_bills', 'generate_briefing', 'review_tasks']) {
    assert(names.includes('alcor_' + name)); assert(!names.includes('stella_' + name))
  }
  assert.equal((await call(agent)).isError, true)
  await turn(agent, 'schedule'); assert.equal((await call(agent)).isError, true)
  assert.equal(reads, 0)
  await turn(agent, 'user')
  const imported = await call(agent)
  assert.equal(imported.isError, false, JSON.stringify(imported))
  assert.equal((await worker.call('records'))[0].amount, '3.45')
  assert.equal((await call(agent)).isError, false)
  assert.equal((await worker.call('records')).length, 1)
  const child = { ctx, session: { header: { origin: 'subagent', parentSession: 'bill-user' } } }
  bindScopeParent(child, agent); await turn(child, 'user')
  const before = reads
  assert.equal((await call(child)).isError, true)
  assert.equal(reads, before)
  ctx.fs.resolve = async () => { throw new Error('DSH denied') }
  assert.equal((await call(agent)).isError, true)
  assert.equal((await worker.call('records')).length, 1)
})

test('page tools bind collaboration to the live user session and refuse recursive automation', async t => {
  const ctx = new Context(); ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: simulatedTurn, lastTurn: simulatedTurn }) }); let simulatedTurn = 0
  t.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(NativeExecutor)
  ctx.provide('alcorSecretary', { mcpAllowed: () => false })
  const forwarded = []
  ctx.provide('alcorOrganizer', { business: async input => { forwarded.push(input); return { accepted: true } } })
  const preset = {}; const agent = { ctx, session: { id: 'live-parent', header: {} } }
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  const turn = kind => (simulatedTurn++, ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind } }] }, async () => {}))
  const call = async (domain, method, args = {}) => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'page-tool-call', name: 'alcor_business', arguments: JSON.stringify({ domain, method, arguments: args }) }] })).results[0].result
  await turn('schedule')
  assert.equal((await call('automation', 'create')).isError, true)
  assert.equal((await call('agent_hub', 'start_job')).isError, true)
  assert.equal((await call('agent_hub', 'cancel_job', { id: 'job' })).isError, true)
  assert.equal((await call('agent_hub', 'retry_job', { id: 'job', stepId: 'step' })).isError, true)
  assert.equal(forwarded.length, 0)
  await turn('user')
  assert.equal((await call('automation', 'list')).isError, false)
  for (const method of ['run_workflow','start_job','cancel_job','retry_job','post_message']) assert.equal((await call('agent_hub',method)).isError,true)
  assert.equal(forwarded.length,1,'Stella must not forward developer collaboration calls')
})

test('local tool obeys host scope, dynamic policy, approval and revocation', async t => {
  const ctx = new Context(); ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: simulatedTurn, lastTurn: simulatedTurn }) }); let simulatedTurn = 0
  t.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(NativeExecutor)
  ctx.provide('alcorSecretary', {})
  let calls = 0
  ctx.provide('alcorOrganizer', { taskReview: async () => { calls++; return { open: [] } } })
  const preset = {}; const agent = { session: { id: 'policy-test', header: {} } }
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  simulatedTurn++; await ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind: 'user' } }] }, async () => {})
  const name = 'alcor_review_tasks'
  const call = async () => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'policy-probe', name, arguments: '{}' }] })).results[0].result
  let scoped
  await ctx.plugin(Object.assign(inner => { scoped = createScope(inner, agent) }, { inject: ['tools'] }))
  t.after(() => scoped.dispose())
  let denied = scoped.ctx.tools.restrict({ deny: [name] })
  assert.equal((await call()).isError, true); assert.equal(calls, 0); denied()
  let revoke = true
  const stopGuard = ctx.tools.guard(execution => execution.name === name && revoke ? 'revoked' : undefined)
  assert.equal((await call()).isError, true); assert.equal(calls, 0)
  revoke = false
  let decision = 'deny'
  const stopGate = ctx.on('tools/pre-execute', async (execution, next) => execution.name !== name ? next() : decision === 'allow' ? next() : { kind: decision, reason: 'host policy' })
  assert.equal((await call()).isError, true); assert.equal(calls, 0)
  decision = 'ask'
  assert.equal((await call()).isError, true); assert.equal(calls, 0, 'missing approval must fail closed')
  let approvalOutcome = 'rejected'
  ctx.provide('approval', { request: async () => { if (approvalOutcome === 'revoke') { revoke = true; return 'allowed-once' }; return approvalOutcome } })
  assert.equal((await call()).isError, true); assert.equal(calls, 0)
  approvalOutcome = 'cancelled'
  assert.equal((await call()).isError, true); assert.equal(calls, 0)
  approvalOutcome = 'revoke'
  assert.equal((await call()).isError, true); assert.equal(calls, 0, 'guard rechecks revocation after approval')
  revoke = false; approvalOutcome = 'allowed-once'
  assert.equal((await call()).isError, false); assert.equal(calls, 1)
  stopGate(); stopGuard()
})

test('PTC nested renamed tool uses the same session guard and host denial', async t => {
  const ctx = new Context(); ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: simulatedTurn, lastTurn: simulatedTurn }) }); let simulatedTurn = 0
  t.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools)
  ctx.provide('ptcRuntime', {
    language: 'typescript', programFormat: 'function-body', isolation: 'synthetic', executionInstructions: '',
    resolve: request => request,
    run: async request => {
      try { return { logs: [], value: await request.bindings[0].functions.alcor_review_tasks({}) } }
      catch (error) { return { logs: [], error: { kind: 'exception', message: error.message } } }
    },
    release: async () => {},
  })
  ctx.provide('workingDirectory', { ensure: async () => tmpdir() })
  const PtcExecutor = await import('@deepseek-ai/dsh-tool-executor-ptc')
  await ctx.plugin(PtcExecutor)
  ctx.provide('alcorSecretary', {})
  let calls = 0
  ctx.provide('alcorOrganizer', { taskReview: async () => { calls++; return { open: [] } } })
  const preset = {}; const agent = { ctx, session: { id: 'ptc-policy-test', header: {}, append: () => 1 } }
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  const turn = kind => (simulatedTurn++, ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind } }] }, async () => {}))
  const call = async () => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'ptc-policy-probe', name: 'run_code', arguments: JSON.stringify({ code: 'synthetic fixture', description: 'Read tasks' }) }] })).results[0].result
  assert.equal((await call()).isError, true); assert.equal(calls, 0)
  await turn('user')
  const deny = ctx.tools.guard(execution => execution.name === 'alcor_review_tasks' ? 'revoked' : undefined)
  assert.equal((await call()).isError, true); assert.equal(calls, 0); deny()
  assert.equal((await call()).isError, false); assert.equal(calls, 1)
  await turn('unknown')
  assert.equal((await call()).isError, true); assert.equal(calls, 1)
})

test('all renamed personal and scheduled tools keep their source guards', async t => {
  const ctx = new Context(); ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: simulatedTurn, lastTurn: simulatedTurn }) }); let simulatedTurn = 0; t.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(NativeExecutor)
  ctx.provide('alcorSecretary', {}); ctx.provide('alcorOrganizer', {})
  const preset = {}; const agent = { session: { id: 'source-user', header: {} } }
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  const turn = (who, kind) => (simulatedTurn++, ctx.waterfall(scopeTarget(who, who), 'agent/pre-step', { agent: who, messages: [{ source: { kind } }] }, async () => {}))
  const files = ['alcor_import_bills', 'alcor_import_dates', 'alcor_export_bills']
  const business = ['alcor_generate_briefing', 'alcor_review_tasks']
  const denied = (who, name) => Boolean(ctx.tools.checkGuards({ name, agent: who }))
  for (const name of [...files, ...business]) assert(denied(agent, name))
  await turn(agent, 'schedule')
  for (const name of files) assert(denied(agent, name))
  assert(!denied(agent, 'alcor_generate_briefing')); assert(denied(agent, 'alcor_review_tasks'))
  await turn(agent, 'user')
  for (const name of [...files, ...business]) assert(!denied(agent, name))
  const child = { session: { header: { origin: 'subagent', parentSession: 'source-user' } } }
  bindScopeParent(child, agent); await turn(child, 'user')
  for (const name of [...files, ...business]) assert(denied(child, name))
  await turn(agent, 'unknown')
  for (const name of [...files, ...business]) assert(denied(agent, name))
})

for (const mode of ['native', 'ptc']) test(`${mode}: all personal and scheduled guards bind provenance to the open host turn`, async t => {
  const ctx = new Context(); t.after(() => ctx.fiber.dispose())
  let hostTurn = 1, requested, input
  ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: hostTurn, lastTurn: hostTurn }) })
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools)
  if (mode === 'ptc') {
    ctx.provide('ptcRuntime', { language: 'typescript', programFormat: 'function-body', isolation: 'synthetic', executionInstructions: '', resolve: request => request,
      run: async request => { try { return { logs: [], value: await request.bindings[0].functions[requested](input) } } catch (error) { return { logs: [], error: { kind: 'exception', message: error.message } } } }, release: async () => {} })
    ctx.provide('workingDirectory', { ensure: async () => tmpdir() }); await ctx.plugin(await import('@deepseek-ai/dsh-tool-executor-ptc'))
  } else await ctx.plugin(NativeExecutor)
  let effects = 0
  ctx.provide('alcorSecretary', {})
  ctx.provide('fs', { resolve: async path => path, stat: async () => ({ type: 'file', size: 1 }), readBytes: async () => Buffer.from('x'), writeText: async () => { effects++ } })
  ctx.provide('alcorOrganizer', { business: async request => { effects++; return request.method === 'list_sources' ? [] : request.method === 'export_ledger_csv' ? { content: 'x' } : { accepted: true } }, localBriefingSources: async () => [],
    taskReview: async () => { effects++; return {} }, startBriefing: async () => { effects++; return {} }, generateBriefing: async () => { effects++; return { id: 'synthetic', date: '2026-01-01', body: '' } } })
  ctx.tools.register({ name: 'mcp__synthetic__read', description: 'Synthetic', parameters: {}, output: { schema: { type: 'object', additionalProperties: true }, render: () => [{ type: 'text', text: '{}' }] }, execute: async () => { effects++; return {} } })
  const preset = {}, agent = { ctx, session: { id: 'turn-source', header: {}, append: () => 1 } }; bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer', 'fs'] }))
  const step = (who, sources) => ctx.waterfall(scopeTarget(who, who), 'agent/pre-step', { agent: who, messages: sources.map(kind => ({ source: { kind } })) }, async () => {})
  const cases = [
    ['mcp__synthetic__read', {}], ['alcor_import_bills', { paths: ['/synthetic.csv'] }],
    ['alcor_import_dates', { path: '/synthetic.csv' }], ['alcor_export_bills', { path: '/synthetic.csv', start: '2026-01-01', end: '2026-01-02' }],
    ['alcor_generate_briefing', {}], ['alcor_review_tasks', {}],
    ['alcor_business', { domain: 'automation', method: 'list' }], ['alcor_business', { domain: 'briefing', method: 'generate', arguments: { date: '2026-01-01' } }],
    ['alcor_business', { domain: 'bills', method: 'import_files', arguments: { files: [] } }],
  ]
  let seq = 0
  const call = async (who, [name, args]) => { requested = name; input = args
    const block = mode === 'ptc' ? { type: 'tool-call', id: `turn-${++seq}`, name: 'run_code', arguments: JSON.stringify({ code: 'synthetic', description: 'Check provenance' }) }
      : { type: 'tool-call', id: `turn-${++seq}`, name, arguments: JSON.stringify(args) }
    return (await ctx.tools.execute({ agent: who, signal: AbortSignal.timeout(5000), calls: [block] })).results[0].result
  }
  await step(agent, ['user']); await step(agent, [])
  for (const item of cases) assert.equal((await call(agent, item)).isError, false, `${mode} same user turn: ${item[0]}`)
  hostTurn++; await step(agent, []); const before = effects
  for (const item of cases) assert.equal((await call(agent, item)).isError, true, `${mode} stale turn: ${item[0]}`)
  assert.equal(effects, before)
  hostTurn++; await step(agent, ['schedule'])
  for (let i = 0; i < cases.length; i++) assert.equal((await call(agent, cases[i])).isError, ![4].includes(i), `${mode} scheduled: ${cases[i][0]}`)
  await step(agent, ['user']); const mixedEffects = effects
  for (const item of cases) assert.equal((await call(agent, item)).isError, true, `${mode} schedule then user cannot upgrade: ${item[0]}`)
  await step(agent, ['user']); assert.equal((await call(agent, cases[0])).isError, true); assert.equal(effects, mixedEffects)
  hostTurn++; await step(agent, ['user']); await step(agent, ['schedule'])
  assert.equal((await call(agent, cases[4])).isError, true, `${mode} user then schedule is mixed`)
  const child = { ctx, session: { id: 'child', header: { origin: 'subagent', parentSession: 'turn-source' }, append: () => 1 } }; bindScopeParent(child, preset); await step(child, ['user'])
  const afterScheduled = effects
  for (const item of cases) assert.equal((await call(child, item)).isError, true, `${mode} delegated: ${item[0]}`)
  assert.equal(effects, afterScheduled)
  hostTurn++; await step(agent, ['user', 'schedule']); for (const item of cases) assert.equal((await call(agent, item)).isError, true, `${mode} mixed source: ${item[0]}`)
})
