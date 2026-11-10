import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import { RemoteError } from '@deepseek-ai/dsh-typert-protocol'
import Storage from '@deepseek-ai/dsh-storage'
import * as Json from '@deepseek-ai/dsh-storage-json'
import * as Domain from '@deepseek-ai/dsh-storage-domain'
import Schedule from '@deepseek-ai/dsh-schedule'

test('official scheduler reuses bound session or creates the reserved ID with its migrated preset', async t => {
  const root = await mkdtemp(join(tmpdir(), 'alcor-reserved-schedule-'))
  const ctx = new Context()
  t.after(async () => { await ctx.fiber.dispose(); await rm(root, { recursive: true, force: true }) })
  const agents = new Map(), created = [], messages = []
  ctx.provide('agents', { withoutInitiator: fn => fn() })
  ctx.provide('sessions', { flush: async () => true })
  ctx.provide('sessionPersistence', {})
  ctx.provide('sessionController', { create: async request => { created.push(request); agents.set(request.sessionId, { session: { id: request.sessionId }, followup: message => messages.push(message), whenIdle: () => Promise.resolve() }) } })
  ctx.on('schedule/resolve-agent', async id => {
    if (!agents.has(id)) throw new RemoteError('session/not-found', 'Synthetic session does not exist')
    return agents.get(id)
  })
  await ctx.plugin(Storage); await ctx.plugin(Json, { root }); await ctx.plugin(Domain, { backend: 'json' }); await ctx.plugin(Schedule, {})
  await ctx.schedule.initialized
  const task = { record: { id: 'synthetic-task', title: 'Synthetic' }, target: { kind: 'new_once', sessionId: 'reserved-synthetic', create: { agentPreset: 'alcor' } } }
  const first = await ctx.schedule.runtime.resolveTarget(task)
  assert.equal(first.sessionId, 'reserved-synthetic')
  assert.deepEqual(created, [{ sessionId: 'reserved-synthetic', agentPreset: 'alcor' }])
  task.target.create.agentPreset = 'different-preset-not-applied-to-existing-session'
  const again = await ctx.schedule.runtime.resolveTarget(task)
  assert.equal(again.agent, first.agent)
  assert.equal(created.length, 1)
  const scheduled = { record: { id: 'synthetic-delivery', kind: 'at', title: 'Synthetic', prompt: 'Use alcor_generate_briefing', scheduledAt: '2099-01-01T00:00:00.000Z' }, status: 'active', updatedAt: '2026-01-01T00:00:00.000Z', target: { kind: 'new_once', sessionId: 'reserved-delivery', create: { agentPreset: 'alcor' } }, involvedSessionIds: [], deliveryHistory: { records: [], earlierRecordsUnavailable: false } }
  await ctx.storageDomain.get('schedule').table('tasks').put(scheduled.record.id, scheduled)
  await ctx.schedule.run({ id: scheduled.record.id })
  const delivered = ctx.storageDomain.get('schedule').table('tasks').get(scheduled.record.id)
  assert.deepEqual(created.at(-1), { sessionId: 'reserved-delivery', agentPreset: 'alcor' })
  assert.equal(delivered.target.sessionId, 'reserved-delivery')
  assert.equal(delivered.lastDelivery.targetSessionId, 'reserved-delivery')
  assert.equal(delivered.record.scheduledAt, scheduled.record.scheduledAt)
  assert.equal(messages.length, 1)
  assert.equal(messages[0].source.kind, 'schedule')
})
