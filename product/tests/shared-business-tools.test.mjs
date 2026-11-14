import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { load } from 'js-yaml'
import { entryListSchema } from '@deepseek-ai/cordis-plugin-include'
import { Context } from '@deepseek-ai/cordis'
import Tools from '@deepseek-ai/dsh-tools'
import * as NativeExecutor from '@deepseek-ai/dsh-tool-executor-native'
import SkillRegistry from '@deepseek-ai/dsh-skill'
import * as ToolSkill from '@deepseek-ai/dsh-tool-skill'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import * as SecretaryTools from '../../packages/alcor-secretary/src/tools.js'

test('installed business tools and guides serve standard and Alcor chats without changing native tool policy', async t => {
  const ctx = new Context()
  t.after(() => ctx.fiber.dispose())
  await ctx.plugin(Tools)
  await ctx.plugin(NativeExecutor)
  await ctx.plugin(SkillRegistry)
  await ctx.plugin(SystemPrompt, {})
  await ctx.plugin(ToolSkill)
  ctx.provide('workingDirectory', { ensure: async () => tmpdir() })
  ctx.provide('agents', { turnBoundaryOf: () => ({ openTurnStartSeq: 1, lastTurn: 1 }) })
  ctx.provide('agentPresets', { presetForSession: session => session.preset })
  ctx.provide('alcorSecretary', {})
  let reads = 0
  ctx.provide('alcorOrganizer', { business: async request => {
    assert.equal(request.domain, 'tasks')
    assert.equal(request.method, 'list_todos')
    reads++
    return [{ id: 'synthetic-task', title: 'Synthetic task' }]
  } })
  ctx.tools.register({ name: 'bash', description: 'Native tool', parameters: {},
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] }, execute: async () => 'native-result' })
  const patch = load(await readFile(new URL('../../packages/alcor-secretary/cordis.patch.yml', import.meta.url), 'utf8'), { schema: entryListSchema })
  const entries = patch.flatMap(row => row.insert ?? [])
  const shared = entries.filter(row => row.name === '@alcor/secretary/tools')
  assert.equal(shared.length, 1, 'Business tools must be owned by the installed bundle, not only a preset')
  for (const row of entries.filter(row => row.name === '@deepseek-ai/dsh-agent-preset')) {
    assert(!row.config.plugins.some(plugin => plugin.name === '@alcor/secretary/tools'), 'Presets must not register duplicate business tools')
  }
  const bundle = await ctx.plugin(SecretaryTools)
  const call = async (agent, name, args = {}) => (await ctx.tools.execute({ agent, signal: AbortSignal.timeout(5000), calls: [
    { type: 'tool-call', id: 'shared-business', name, arguments: JSON.stringify(args) },
  ] })).results[0].result
  const standard = { ctx, session: { preset: 'standard', header: {}, seq: 0, surface: { nodes: [] } } }
  const alcor = { ctx, session: { preset: 'alcor', header: {}, seq: 0, surface: { nodes: [] } } }
  assert.equal((await call(standard, 'bash')).isError, false, 'Installing Alcor must not restrict native DSH file tools')
  assert.equal((await call(alcor, 'bash')).isError, true, 'Alcor keeps its personal-turn file guard')
  for (const agent of [standard, alcor]) {
    assert(ctx.tools.schemas(agent).some(row => row.name === 'alcor_business'))
    const names = (await ctx.skills.list({ scope: agent, cwd: tmpdir() })).map(row => row.name)
    assert(names.includes('alcor-tasks') && names.includes('alcor-bills'))
    const guide = await call(agent, 'skill', { name: 'alcor-tasks' })
    assert.equal(guide.isError, false)
    assert(guide.value.content.includes('list_todos'))
    assert.equal((await call(agent, 'alcor_business', { domain: 'tasks', method: 'list_todos' })).isError, true)
    await ctx.waterfall('agent/pre-step', { agent, signal: AbortSignal.timeout(5000), messages: [{ source: { kind: 'user' }, content: [] }] }, async () => ({ kind: 'continue', messages: [] }))
    const result = await call(agent, 'alcor_business', { domain: 'tasks', method: 'list_todos' })
    assert.equal(result.isError, false, JSON.stringify(result))
    assert.equal(result.value.result[0].id, 'synthetic-task')
  }
  const child = { ctx, session: { preset: 'standard', header: { origin: 'subagent', parentSession: 'parent' }, seq: 0, surface: { nodes: [] } } }
  await ctx.waterfall('agent/pre-step', { agent: child, signal: AbortSignal.timeout(5000), messages: [{ source: { kind: 'user' }, content: [] }] }, async () => ({ kind: 'continue', messages: [] }))
  assert.equal((await call(child, 'alcor_business', { domain: 'tasks', method: 'list_todos' })).isError, true)
  assert.equal(reads, 2)
  const deny = ctx.tools.guard(execution => execution.name === 'alcor_business' ? 'Host denied' : undefined)
  assert.equal((await call(standard, 'alcor_business', { domain: 'tasks', method: 'list_todos' })).isError, true)
  assert.equal(reads, 2)
  deny()
  await bundle.dispose()
  assert(!ctx.tools.schemas(standard).some(row => row.name === 'alcor_business'))
  assert(!(await ctx.skills.list({ scope: standard, cwd: tmpdir() })).some(row => row.name === 'alcor-tasks'))
  assert.equal((await call(standard, 'bash')).isError, false)
})
