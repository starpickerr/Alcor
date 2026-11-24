import test, { before, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'

let directory, Organizer
before(async () => {
  directory = await mkdtemp(join(process.cwd(), '.alcor-briefing-session-test-'))
  const file = join(directory, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file, bundle: true, packages: 'external', platform: 'node', format: 'esm', target: 'node24', logLevel: 'silent' })
  Organizer = (await import(pathToFileURL(file).href)).default
})
after(async () => { await rm(directory, { recursive: true, force: true }) })

function fixture() {
  const owner = Object.create(Organizer.prototype)
  owner.active = true; owner.queue = Promise.resolve()
  const preferences = { task_target: 'new_once', task_workspace_id: '' }
  const workspaces = ['one', 'two'].map(id => ({ id, sessionIds: [] }))
  const agents = new Map(), creations = [], archived = []
  let binding, failure
  owner.businessWorker = { call: async (action, input) => {
    if (action === 'invoke') return preferences
    assert.equal(input.method, 'session')
    if (input.binding) binding = structuredClone(input.binding)
    return binding
  } }
  owner.ctx = {
    workspaceRegistry: { get: id => workspaces.find(row => row.id === id), list: () => workspaces, archivedSessionIds: archived },
    agentPresets: { presetForSession: session => session.preset },
    sessionController: {
      create: async request => {
        creations.push(request)
        const id = `session-${creations.length}`
        workspaces.find(row => row.id === request.workspaceId).sessionIds.push(id)
        agents.set(id, { session: { id, preset: 'alcor' } })
        return { sessionId: id }
      },
      rename: async () => {},
      resolveAgent: async id => failure ? { error: { code: failure } } : agents.has(id) ? { agent: agents.get(id) } : { error: { code: 'session/not-found' } },
    },
  }
  return { owner, preferences, workspaces, agents, creations, archived, fail: value => { failure = value } }
}

test('first creation is serialized and subsequent briefing runs reuse their owned session', async () => {
  const f = fixture()
  const [first, second] = await Promise.all([f.owner.briefingConversation({ workspaceId: 'one' }), f.owner.briefingConversation({ workspaceId: 'one' })])
  assert.equal(first.sessionId, second.sessionId)
  assert.deepEqual(f.creations, [{ agentPreset: 'alcor', workspaceId: 'one' }])
  f.preferences.task_workspace_id = 'two'
  const changed = await f.owner.briefingConversation({ workspaceId: 'one' })
  assert.notEqual(changed.sessionId, first.sessionId)
  assert.equal(f.creations[1].workspaceId, 'two')
})

test('archived, moved, missing and reconfigured sessions are not silently reused', async () => {
  for (const change of ['archived', 'moved', 'missing', 'preset']) {
    const f = fixture(), first = await f.owner.briefingConversation({ workspaceId: 'one' })
    if (change === 'archived') f.archived.push(first.sessionId)
    if (change === 'moved') { f.workspaces[0].sessionIds.length = 0; f.workspaces[1].sessionIds.push(first.sessionId) }
    if (change === 'missing') f.agents.delete(first.sessionId)
    if (change === 'preset') f.agents.get(first.sessionId).session.preset = 'different'
    assert.notEqual((await f.owner.briefingConversation({ workspaceId: 'one' })).sessionId, first.sessionId, change)
  }
})

test('transient host errors and changed settings fail without creating another session', async () => {
  const f = fixture()
  await f.owner.briefingConversation({ workspaceId: 'one' })
  f.fail('session/writer-held')
  await assert.rejects(f.owner.briefingConversation({ workspaceId: 'one' }), /暂时不可用/)
  assert.equal(f.creations.length, 1)
  f.preferences.task_target = 'new'
  await assert.rejects(f.owner.briefingConversation({ workspaceId: 'one' }), /执行位置已更改/)
  await assert.rejects(f.owner.briefingConversation({ workspaceId: 'one', sessionId: 'foreign' }))
})
