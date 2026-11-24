import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { Context } from '@deepseek-ai/cordis'
import { bindScopeParent, scopeTarget } from '@deepseek-ai/dsh-scope'
import Sessions from '@deepseek-ai/dsh-session'
import Projections from '@deepseek-ai/dsh-session-projection'
import Agents from '@deepseek-ai/dsh-agent'
import Tools from '@deepseek-ai/dsh-tools'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import Approval from '@deepseek-ai/dsh-user-approval'
import * as Native from '@deepseek-ai/dsh-tool-executor-native'

test('DSH direct Apple writes need selected sources and a current one-time approval without Alcor', async t => {
  const dir = await mkdtemp(join(process.cwd(), '.apple-direct-test-'))
  t.after(() => rm(dir, { recursive: true, force: true }))
  const outfile = join(dir, 'tools.mjs')
  await build({ entryPoints: ['../dsh-apple-sync/src/direct-tools.js'], outfile, bundle: true,
    packages: 'external', platform: 'node', format: 'esm' })
  const { registerDirectTools } = await import(pathToFileURL(outfile))
  const ctx = new Context()
  t.after(() => ctx.fiber.dispose())
  for (const plugin of [Sessions, Projections, Agents, SystemPrompt, Tools, Approval, Native]) await ctx.plugin(plugin, {})
  const pending = new Map()
  let settings = { calendar: ['selected'], reminders: ['list'], writes: true }, answer = 'rejected', revoke = false
  const calls = [], reasons = []
  const worker = { async call(_, input) {
    calls.push(input)
    if (input.action === 'query') return { items: [{ title: 'Synthetic Apple event' }] }
    if (input.action === 'commit') {
      assert.equal(pending.get(input.plan.operation_id)(), true)
      return { status: 'saved' }
    }
    return { operation_id: input.operation_id, source: 'Synthetic', title: 'Local', item: input.args.title, changes: input.args }
  } }
  await ctx.plugin(Object.assign(inner => registerDirectTools(inner, worker, () => settings, pending), { inject: ['tools'] }))
  ctx.on('approval/request', request => {
    reasons.push(request.reason)
    if (revoke) settings = { ...settings, writes: false }
    return answer
  })
  const session = ctx.sessions.create('direct-apple')
  session.append('turn/start', { turn: 1 })
  const agent = { ctx, options: { subagentDepth: 0 }, session }; bindScopeParent(agent, {})
  let count = 0
  const call = async (name, args) => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000),
    calls: [{ type: 'tool-call', id: String(++count), name, arguments: JSON.stringify(args) }] })).results[0].result
  const create = { title: 'Synthetic event', start_at: '2026-11-20T09:00:00+08:00',
    end_at: '2026-11-20T10:00:00+08:00', timezone: 'Asia/Shanghai' }
  const write = input => call('apple_calendar_create', input)
  assert((await write(create)).isError)
  assert.equal(calls.length, 0)
  await ctx.waterfall(scopeTarget(agent, agent), 'agent/pre-step', { agent, messages: [{ source: { kind: 'user' } }] }, async () => {})
  assert(!(await call('apple_calendar_list', { start: create.start_at, end: create.end_at })).isError)
  assert((await write({ ...create, confirm: true })).isError)
  assert((await write(create)).isError)
  assert.equal(calls.filter(row => row.action === 'commit').length, 0)
  answer = 'allowed-once'
  const success = await write(create)
  assert.equal(success.isError, false, JSON.stringify(success))
  assert.match(reasons.at(-1), /Synthetic event/)
  revoke = true
  assert((await write(create)).isError)
  assert.equal(calls.filter(row => row.action === 'commit').length, 1)
  assert.equal(pending.size, 0)
})
