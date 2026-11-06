import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
import * as NativeExecutor from '@deepseek-ai/dsh-tool-executor-native'
import { bindScopeParent, createScope, scopeTarget } from '@deepseek-ai/dsh-scope'
import * as McpClient from '@deepseek-ai/dsh-mcp-client'
import { fileURLToPath } from 'node:url'
import { apply } from '../../packages/stella-secretary/src/tools.js'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BusinessWorker } from '../../packages/stella-organizer/src/business.js'
import { recordSchema } from '../../packages/stella-organizer/src/records.js'

test('trusted business tools write durable page objects and retain retry receipts', async context => {
  const directory = await mkdtemp(join(tmpdir(), 'stella-tool-business-'))
  const worker = new BusinessWorker(directory, { worker: new URL('../../packages/stella-secretary/business/worker.py', import.meta.url) })
  const ctx = new Context()
  context.after(async () => { await ctx.fiber.dispose(); await worker.close(); await rm(directory, { recursive: true, force: true }) })
  await ctx.plugin(SystemPrompt, {})
  await ctx.plugin(Tools)
  await ctx.plugin(NativeExecutor)
  ctx.provide('stellaSecretary', { mcpAllowed: () => false })
  const forwarded = []
  ctx.provide('stellaOrganizer', { business: async input => {
    forwarded.push(input)
    if (input.domain === 'briefing' || input.method === 'run_workflow') return { accepted: true }
    return worker.call('invoke', { domain: input.domain, method: input.method, arguments: input.arguments, request_id: input.requestId })
  } })
  const preset = {}
  const agent = { session: { id: 'trusted-child', header: { origin: 'subagent' } } }
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'stellaSecretary', 'stellaOrganizer'] }))
  const call = async (input, id = 'business-durable-call') => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id, name: 'stella_business', arguments: JSON.stringify(input) }] })).results[0].result
  const input = { domain: 'tasks', method: 'create_todo', arguments: { payload: { title: 'Created through Agent' } } }
  const first = await call(input)
  assert.equal(first.isError, false, JSON.stringify(first))
  assert.equal((await call(input)).isError, false)
  const records = await worker.call('records')
  assert.equal(records.length, 1)
  assert.equal(recordSchema.parse(records[0]).title, 'Created through Agent')
  assert.equal((await call({ ...input, arguments: { payload: { title: 'Different request' } } })).isError, true)
  assert.equal((await call({ domain: 'briefing', method: 'generate', arguments: { date: '2026-11-05' } }, 'generate-once')).isError, true)
  assert.equal((await call({ domain: 'briefing', method: 'generate', arguments: { date: '2026-11-05' } }, 'generate-once')).isError, true)
  assert(!forwarded.some(input => input.domain === 'briefing'))
  assert.equal((await call({ domain: 'briefing', method: 'configure_sources', arguments: { sources: [] } })).isError, true)
})

test('Stella inherits native tool visibility and host denials, including later tools', async context => {
  const ctx = new Context()
  context.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(NativeExecutor)
  ctx.provide('stellaSecretary', { mcpAllowed: () => false })
  let invocations = 0
  const register = name => ctx.tools.register({ name, description: 'Synthetic host tool', parameters: {},
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    execute: async () => { invocations++; return 'host-result' } })
  register('bash'); register('working_directory')
  const secretary = { session: { header: {} } }; const preset = {}
  bindScopeParent(secretary, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt'] }))
  const names = agent => ctx.tools.schemas(agent).map(tool => tool.name).sort()
  assert.deepEqual(names(secretary), ['bash', 'stella_overview', 'working_directory'])
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
  const ctx = new Context()
  context.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(NativeExecutor)
  ctx.provide('stellaSecretary', { mcpAllowed: () => false })
  const preset = {}; const secretary = { session: { header: {} } }
  bindScopeParent(secretary, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'stellaSecretary'] }))
  const fixture = fileURLToPath(new URL('./fixtures/mcp-server.mjs', import.meta.url))
  const connection = await ctx.plugin(McpClient, { transport: 'stdio', serverName: 'elearning', command: process.execPath, args: [fixture], failOnStartupError: true })
  const name = 'mcp__elearning__list_courses'
  const call = async (agent, signal = AbortSignal.timeout(5000)) => (await ctx.tools.execute({ agent, signal, calls: [{ type: 'tool-call', id: 'probe', name, arguments: '{}' }] })).results[0].result
  const turn = (agent, kind) => ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind } }] }, async () => {})
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

test('local automation tools deny delegated/unknown turns and reuse a daily briefing identity', async context => {
  const ctx = new Context()
  context.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {})
  await ctx.plugin(Tools)
  await ctx.plugin(NativeExecutor)
  const saved = []
  ctx.provide('stellaSecretary', { mcpAllowed: () => false })
  ctx.provide('stellaOrganizer', {
    business: async () => [{ source: 'stella_tasks', accountId: 'local' }, { source: 'elearning', accountId: 'school' }],
    generateBriefing: async input => { saved.push(input); return { id: input.requestId, date: input.date, body: 'Synthetic saved briefing' } },
    startBriefing: async input => ({ requestId: input.requestId, phase: 'starting', sessionId: 'independent-root' }),
    taskReview: async () => ({ open: [{ title: 'Synthetic task' }], completed: 0 }),
  })
  const preset = {}
  const agent = { session: { header: {} } }
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => { apply(createScope(inner, preset).ctx) }, { inject: ['tools', 'systemPrompt', 'stellaSecretary', 'stellaOrganizer'] }))
  const turn = (agent, kind) => ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind } }] }, async () => {})
  const call = async (agent, name) => (await ctx.tools.execute({ agent, signal: new AbortController().signal, calls: [{ type: 'tool-call', id: 'business-probe', name, arguments: '{}' }] })).results[0].result
  assert.equal((await call(agent, 'stella_generate_briefing')).isError, true)
  await turn(agent, 'schedule')
  assert.equal((await call(agent, 'stella_generate_briefing')).isError, false)
  assert.equal((await call(agent, 'stella_generate_briefing')).isError, false)
  assert.equal(saved.length, 2)
  assert.equal(saved[0].requestId, saved[1].requestId)
  assert.deepEqual(saved[0].sources.map(source => source.source), ['stella_tasks'])
  const child = { session: { header: { origin: 'subagent', parentSession: 'parent' } } }
  bindScopeParent(child, agent)
  await turn(child, 'user')
  for (const name of ['stella_generate_briefing', 'stella_review_tasks']) assert.equal((await call(child, name)).isError, true)
  assert.equal(saved.length, 2)
  await turn(agent, 'unknown')
  assert.equal((await call(agent, 'stella_review_tasks')).isError, true)
  await turn(agent, 'user')
  const started = await call(agent, 'stella_generate_briefing')
  assert.equal(started.isError, false)
  assert(JSON.stringify(started).includes('independent-root'))
  assert(!JSON.stringify(started).includes('\"saved\":true'))
  assert.equal(saved.length, 2, 'Manual chat starts a task; only scheduled calls wait for business completion')
  const result = await call(agent, 'stella_review_tasks')
  assert.equal(result.isError, false)
  assert(JSON.stringify(result).includes('Synthetic task'))
})

test('bill import is callable in user turns, writes ledger data directly, and refuses delegated/unknown calls', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'stella-bill-tool-'))
  const worker = new BusinessWorker(dir, { worker: new URL('../../packages/stella-secretary/business/worker.py', import.meta.url) })
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose(); await worker.close(); await rm(dir, { recursive: true, force: true }) })
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(NativeExecutor)
  ctx.provide('stellaSecretary', { mcpAllowed: () => false })
  const csv = '交易时间,收/支,金额,交易状态,交易订单号,商品说明\n2026-11-01 12:00:00,支出,3.45,交易成功,synthetic-order,午餐'
  let reads = 0
  ctx.provide('fs', { resolve: async path => path, stat: async () => ({ type: 'file', size: 100 }), readBytes: async () => { reads++; return Buffer.from(csv) } })
  ctx.provide('stellaOrganizer', { business: request => worker.call('invoke', { domain: request.domain, method: request.method, arguments: request.arguments }) })
  const preset = {}; const agent = { ctx, session: { id: 'bill-user', header: {} } }
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'stellaSecretary', 'stellaOrganizer', 'fs'] }))
  const turn = (agent, kind) => ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind } }] }, async () => {})
  const call = async agent => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'bill-import', name: 'stella_import_bills', arguments: JSON.stringify({ paths: ['/allowed/支付宝.csv'] }) }] })).results[0].result
  assert(ctx.tools.schemas(agent).some(tool => tool.name === 'stella_import_bills'))
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
  const ctx = new Context()
  t.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {}); await ctx.plugin(Tools); await ctx.plugin(NativeExecutor)
  ctx.provide('stellaSecretary', { mcpAllowed: () => false })
  const forwarded = []
  ctx.provide('stellaOrganizer', { business: async input => { forwarded.push(input); return { accepted: true } } })
  const preset = {}; const agent = { ctx, session: { id: 'live-parent', header: {} } }
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'stellaSecretary', 'stellaOrganizer'] }))
  const turn = kind => ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind } }] }, async () => {})
  const call = async (domain, method, args = {}) => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [{ type: 'tool-call', id: 'page-tool-call', name: 'stella_business', arguments: JSON.stringify({ domain, method, arguments: args }) }] })).results[0].result
  await turn('schedule')
  assert.equal((await call('automation', 'create')).isError, true)
  assert.equal((await call('agent_hub', 'start_job')).isError, true)
  assert.equal((await call('agent_hub', 'cancel_job', { id: 'job' })).isError, true)
  assert.equal(forwarded.length, 0)
  await turn('user')
  assert.equal((await call('automation', 'list')).isError, false)
  assert.equal((await call('agent_hub', 'run_workflow', { parentSessionId: 'forged' })).isError, false)
  assert.equal(forwarded.at(-1).arguments.parentSessionId, 'live-parent')
  assert.equal((await call('agent_hub', 'start_job', { title: 'Research', prompt: 'Summarize', parentSessionId: 'forged', id: 'forged' })).isError, false)
  assert.equal(forwarded.at(-1).arguments.parentSessionId, 'live-parent')
  assert.match(forwarded.at(-1).arguments.id, /^[0-9a-f-]{36}$/)
  assert.equal((await call('agent_hub', 'cancel_job', { id: 'job' })).isError, false)
  assert.deepEqual(forwarded.at(-1).arguments, { id: 'job' })
})
