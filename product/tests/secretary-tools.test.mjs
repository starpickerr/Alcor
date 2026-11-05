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

test('secretary masks inherited tools, including later registrations and descendant calls, without changing DSH', async context => {
  const ctx = new Context()
  context.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {})
  await ctx.plugin(Tools)
  await ctx.plugin(NativeExecutor)
  ctx.provide('stellaSecretary', { mcpAllowed: () => false })
  let invocations = 0
  function register(name) {
    ctx.tools.register({
      name, description: 'Synthetic executor probe', parameters: {},
      output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
      execute: async () => { invocations++; return 'synthetic-execution' },
    })
  }
  register('working_directory')
  register('bash')
  const secretary = { id: 'secretary-agent' }
  const preset = {}
  const parent = bindScopeParent(secretary, preset)
  let presetScope
  let scope
  await ctx.plugin(Object.assign(inner => { presetScope = createScope(inner, preset); apply(presetScope.ctx) }, { inject: ['tools', 'systemPrompt'] }))
  await ctx.plugin(Object.assign(inner => { scope = createScope(inner, secretary) }, { inject: ['tools'] }))
  const dsh = { id: 'dsh-agent' }
  assert.deepEqual(ctx.tools.schemas(secretary).map(tool => tool.name), ['stella_overview'])
  assert.deepEqual(ctx.tools.schemas(dsh).map(tool => tool.name).sort(), ['bash', 'working_directory'])
  register('later_installed_executor')
  assert.deepEqual(ctx.tools.schemas(secretary).map(tool => tool.name), ['stella_overview'])
  const child = { id: 'secretary-child' }
  let childScope
  await ctx.plugin(Object.assign(inner => { childScope = createScope(inner, child, { parent: secretary }) }, { inject: ['tools'] }))
  for (const agent of [secretary, child]) {
    for (const name of ['working_directory', 'bash', 'later_installed_executor', 'run_code']) {
      assert.equal(ctx.tools.executable(name, agent), undefined)
      const batch = await ctx.tools.execute({
        agent, signal: new AbortController().signal,
        calls: [{ type: 'tool-call', id: 'synthetic-forged-call', name, arguments: '{"mode":"dsh","userId":"owner"}' }],
      })
      assert.equal(batch.results[0].result.isError, true)
    }
  }
  assert.equal(invocations, 0)
  const batch = await ctx.tools.execute({ agent: dsh, signal: new AbortController().signal,
    calls: [{ type: 'tool-call', id: 'synthetic-dsh-call', name: 'bash', arguments: '{}' }] })
  assert.equal(batch.results[0].result.isError, false)
  assert.equal(invocations, 1)
  await childScope.dispose()
  parent.rebind({})
  assert.deepEqual(ctx.tools.schemas(secretary).map(tool => tool.name).sort(), ['bash', 'later_installed_executor', 'working_directory'])
  await scope.dispose()
  await presetScope.dispose()
  assert.equal(ctx.tools.executable('stella_overview', secretary), undefined)
})

test('native MCP is opt-in, executes only in active personal turns, and revokes without changing DSH', async context => {
  const ctx = new Context()
  context.after(() => ctx.fiber.dispose())
  await ctx.plugin(SystemPrompt, {})
  await ctx.plugin(Tools)
  await ctx.plugin(NativeExecutor)
  const allowed = new Set()
  ctx.provide('stellaSecretary', { mcpAllowed: name => allowed.has(name) })
  const preset = {}
  const secretary = { session: { header: {} } }
  bindScopeParent(secretary, preset)
  await ctx.plugin(Object.assign(inner => { apply(createScope(inner, preset).ctx) }, { inject: ['tools', 'systemPrompt', 'stellaSecretary'] }))
  const fixture = fileURLToPath(new URL('./fixtures/mcp-server.mjs', import.meta.url))
  const connection = await ctx.plugin(McpClient, { transport: 'stdio', serverName: 'elearning', command: process.execPath, args: [fixture], failOnStartupError: true })
  const name = 'mcp__elearning__list_courses'
  const write = 'mcp__elearning__save_settings'
  const call = async (agent, name, signal = new AbortController().signal) => (await ctx.tools.execute({ agent, signal, calls: [{ type: 'tool-call', id: 'probe', name, arguments: '{}' }] })).results[0].result
  const turn = (agent, kind) => ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind } }] }, async () => {})
  assert.deepEqual(ctx.tools.schemas(secretary).map(tool => tool.name), ['stella_overview'])
  assert.equal((await call(secretary, name)).isError, true)
  allowed.add(name); ctx.emit('stella/mcp-change')
  assert(ctx.tools.schemas(secretary).some(tool => tool.name === name))
  assert.equal((await call(secretary, name)).isError, true)
  await turn(secretary, 'user')
  const result = await call(secretary, name)
  assert.equal(result.isError, false)
  assert(JSON.stringify(result).includes('synthetic-course'))
  assert.equal((await call(secretary, write)).isError, true)
  const child = { session: { header: { origin: 'subagent', parentSession: 'owner' } } }
  bindScopeParent(child, secretary)
  await turn(child, 'user')
  assert.equal((await call(child, name)).isError, true)
  await turn(secretary, 'schedule')
  assert.equal((await call(secretary, name)).isError, true)
  await turn(secretary, 'user')
  const cancelled = new AbortController(); cancelled.abort()
  assert.equal((await call(secretary, name, cancelled.signal)).isError, true)
  // Revocation is rechecked after an asynchronous approval boundary.
  const stopGate = ctx.on('tools/pre-execute', async (execution, next) => {
    if (execution.agent === secretary) { allowed.clear(); ctx.emit('stella/mcp-change') }
    return next()
  })
  assert.equal((await call(secretary, name)).isError, true)
  stopGate()
  assert.equal(ctx.tools.executable(name, secretary), undefined)
  assert.equal((await call({}, write)).isError, false)
  allowed.add(name); ctx.emit('stella/mcp-change')
  await connection.dispose()
  assert.equal(ctx.tools.executable(name, secretary), undefined)
  assert.equal((await call(secretary, name)).isError, true)
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
    generateBriefing: async input => { saved.push(input); return { id: input.requestId, date: input.date, body: 'Synthetic saved briefing' } },
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
  assert.deepEqual(saved[0].sources.map(source => source.source), ['calendar_agenda', 'stella_tasks', 'stella_bills'])
  const child = { session: { header: { origin: 'subagent', parentSession: 'parent' } } }
  bindScopeParent(child, agent)
  await turn(child, 'user')
  for (const name of ['stella_generate_briefing', 'stella_review_tasks']) assert.equal((await call(child, name)).isError, true)
  assert.equal(saved.length, 2)
  await turn(agent, 'unknown')
  assert.equal((await call(agent, 'stella_review_tasks')).isError, true)
  await turn(agent, 'user')
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
