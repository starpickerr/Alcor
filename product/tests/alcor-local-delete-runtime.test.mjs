import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'
import { Context } from '@deepseek-ai/cordis'
import { bindScopeParent, createScope, scopeTarget } from '@deepseek-ai/dsh-scope'
import Sessions from '@deepseek-ai/dsh-session'
import Projections from '@deepseek-ai/dsh-session-projection'
import Agents from '@deepseek-ai/dsh-agent'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Tools from '@deepseek-ai/dsh-tools'
import Approval from '@deepseek-ai/dsh-user-approval'
import * as Native from '@deepseek-ai/dsh-tool-executor-native'
import * as Ptc from '@deepseek-ai/dsh-tool-executor-ptc'
import { apply } from '../../packages/alcor-secretary/src/tools.js'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

for (const mode of ['native', 'ptc']) test(`${mode}: secretary registered tool uses current user turn, native approval and real owner`, async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-local-runtime-')), output = await mkdtemp(join(process.cwd(), '.alcor-local-runtime-'))
  const worker = new BusinessWorker(root, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) }), ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose(); await worker.close(); await rm(root, { recursive: true, force: true }); await rm(output, { recursive: true, force: true }) })
  const file = join(output, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  const Organizer = (await import(pathToFileURL(file).href)).default, organizer = Object.create(Organizer.prototype)
  organizer.active = true; organizer.queue = Promise.resolve(); organizer.businessWorker = worker
  organizer.ctx = { get: () => undefined }; organizer.deleteAuthorizers = new WeakMap()
  for (const plugin of [Sessions, Projections, Agents, SystemPrompt, Tools, Approval]) await ctx.plugin(plugin, {})
  let ptcInput
  if (mode === 'ptc') {
    ctx.provide('ptcRuntime', { language: 'typescript', programFormat: 'function-body', isolation: 'synthetic', executionInstructions: '', resolve: value => value,
      run: async request => { try { return { logs: [], value: await request.bindings[0].functions.alcor_business(ptcInput) } } catch (error) { return { logs: [], error: { kind: 'exception', message: error.message } } } }, release: async () => {} })
    ctx.provide('workingDirectory', { ensure: async () => tmpdir() }); await ctx.plugin(Ptc)
  } else await ctx.plugin(Native)
  ctx.provide('alcorSecretary', {}); ctx.provide('alcorOrganizer', organizer)
  const session = ctx.sessions.create('synthetic-runtime'); session.append('turn/start', { turn: 1 })
  const agent = { ctx, options: { subagentDepth: 0 }, session }, preset = {}
  bindScopeParent(agent, preset)
  await ctx.plugin(Object.assign(inner => apply(createScope(inner, preset).ctx), { inject: ['tools', 'systemPrompt', 'alcorSecretary', 'alcorOrganizer'] }))
  const turn = kind => ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind } }] }, async () => {})
  let answer = 'rejected', asks = 0
  ctx.on('approval/request', async () => { asks++; return answer })
  const row = await worker.call('invoke', { domain: 'tasks', method: 'create_todo', arguments: { payload: { title: 'Synthetic runtime' } } })
  const call = async (callId, input = { domain: 'tasks', method: 'delete_todo', arguments: { todo_id: row.id } }) => {
    ptcInput = input
    const block = mode === 'ptc' ? { type: 'tool-call', id: callId, name: 'run_code', arguments: JSON.stringify({ code: 'synthetic', description: 'Delete task' }) }
      : { type: 'tool-call', id: callId, name: 'alcor_business', arguments: JSON.stringify(ptcInput) }
    return (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [block] })).results[0].result
  }
  await turn('schedule'); assert.equal((await call('schedule')).isError, true); assert.equal(asks, 0)
  session.append('turn/end', { turn: 1, reason: 'completed' }); session.append('turn/start', { turn: 2 })
  await turn('user'); assert.equal((await call('deny')).isError, true); assert.equal(asks, 1)
  answer = 'allowed-once'; const allowed = await call('allow'); assert.equal(allowed.isError, false, JSON.stringify(allowed)); assert.equal(asks, 2)
  await assert.rejects(() => worker.call('invoke', { domain: 'tasks', method: 'get_todo', arguments: { todo_id: row.id } }), /not found/)
  const replay = await call('allow'); assert.equal(replay.isError, false, JSON.stringify(replay)); assert.equal(asks, 2)
  let dispatches = 0; organizer.business = async () => { dispatches++; return {} }
  for (const [method, args] of [['one_time', { direction: 'export', confirm: true }], ['transfer_batch', { direction: 'export', confirm: true, token: 'forged' }], ['resolve_category_conflict', { choice: 'keep_apple' }], ['start_sync', { confirm_local_deletions: true }], ['sync', { confirm_local_deletions: 'yes' }], ['sync', { confirm_deletions: 1 }]]) {
    const blocked = await call('apple-' + method, { domain: 'apple', method, arguments: args })
    assert.equal(blocked.isError, true, method); assert.match(JSON.stringify(blocked), /具体目标审批|无法验证导出目标|无法验证导出范围/)
  }
  assert.equal(dispatches, 0)
})
