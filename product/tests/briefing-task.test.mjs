import test from 'node:test'
import assert from 'node:assert/strict'
import { briefingPrompt, startBriefingConversation } from '../../packages/alcor-secretary/src/briefing-task.js'

const api = preferences => ({ business: async () => ({ ok: true, value: { task_target: 'new', task_prompt: briefingPrompt, task_workspace_id: '', ...preferences } }) })

test('manual briefing submits an Alcor conversation without navigating away', async () => {
  const calls = []
  const session = Object.fromEntries(['create', 'rename', 'prompt'].map(method => [method, async request => {
    calls.push([method, request]); return { ok: true, value: method === 'create' ? { sessionId: 'chat' } : { accepted: true } }
  }]))
  const id = await startBriefingConversation({ session, api: api({}), context: { workspaceId: 'selected-workspace' }, date: '2026-11-16', openSession: async id => calls.push(['open', id]) })
  assert.equal(id, 'chat')
  assert.deepEqual(calls[0], ['create', { workspaceId: 'selected-workspace', agentPreset: 'alcor' }])
  assert.deepEqual(calls[1], ['rename', { sessionId: 'chat', title: '生成/更新简报 · 2026-11-16' }])
  assert.equal(calls[2][1].mode, 'queue')
  assert.deepEqual(calls[2][1].content, [{ type: 'text', text: briefingPrompt }])
  assert.match(calls[2][1].requestId, /^[0-9a-f-]{36}$/)
  assert.equal(calls.length, 3, 'Starting a briefing must leave the current page in place')
})

test('briefing button follows its saved content, target and workspace without creating a scheduled task', async () => {
  const calls = []
  const session = { create: async request => { calls.push(request); return { ok: true, value: { sessionId: 'new-chat' } } }, rename: async () => ({ ok: true }), prompt: async request => { calls.push(request); return { ok: true } } }
  await startBriefingConversation({ session, api: api({ task_prompt: '自定义简报内容', task_workspace_id: 'saved-workspace' }), context: { workspaceId: 'current-workspace' }, date: '2026-11-16', openSession() {} })
  assert.equal(calls[0].workspaceId, 'saved-workspace')
  assert.equal(calls[1].content[0].text, '自定义简报内容')
  calls.length = 0
  await startBriefingConversation({ session, api: api({ task_target: 'current' }), currentSessionId: 'existing-chat', date: '2026-11-16', openSession: () => assert.fail('must keep the current view') })
  assert.equal(calls.length, 1)
  assert.equal(calls[0].sessionId, 'existing-chat')
  await assert.rejects(startBriefingConversation({ session, api: api({ task_target: 'current' }) }), /请先打开一个对话/)
})

test('failed session creation or prompt acceptance does not report a started briefing', async () => {
  for (const failed of ['create', 'rename', 'prompt']) {
    const calls = []
    const session = Object.fromEntries(['create', 'rename', 'prompt'].map(method => [method, async () => {
      calls.push(method)
      return method === failed ? { ok: false, error: { message: 'Native failure' } } : { ok: true, value: { sessionId: 'chat' } }
    }]))
    await assert.rejects(startBriefingConversation({ session, api: api({}), date: '2026-11-16', context: {}, openSession: () => assert.fail('must not navigate after rejected acceptance') }), /Native failure/)
    assert.equal(calls.at(-1), failed)
  }
})

test('reuse sends the prompt to the Host-owned briefing session, never the current unrelated chat', async () => {
  const calls = []
  const organizer = api({ task_target: 'new_once' })
  organizer.briefingConversation = async request => { calls.push(request); return { ok: true, value: { sessionId: 'owned-briefing' } } }
  const session = { create: () => assert.fail('Host owns reuse creation'), prompt: async request => { calls.push(request); return { ok: true } } }
  const id = await startBriefingConversation({ session, api: organizer, currentSessionId: 'unrelated-chat', context: { workspaceId: 'one' }, openSession: async id => calls.push(id) })
  assert.equal(id, 'owned-briefing')
  assert.deepEqual(calls[0], { workspaceId: 'one' })
  assert.equal(calls[1].sessionId, 'owned-briefing')
  assert.equal(calls.length, 2, 'Reusing a briefing session must not open it automatically')
  organizer.briefingConversation = async () => ({ ok: false, error: { message: 'Host unavailable' } })
  await assert.rejects(startBriefingConversation({ session, api: organizer }), /Host unavailable/)
})
