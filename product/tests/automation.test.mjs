import test from 'node:test'
import assert from 'node:assert/strict'
import { automationRequest, alcorTarget } from '../../packages/alcor-organizer/src/automation.js'

test('Stella automation keeps its identity across new targets and workspace changes', () => {
  assert.deepEqual(alcorTarget(undefined), { kind: 'new_once', create: { agentPreset: 'alcor' } })
  assert.deepEqual(alcorTarget({ kind: 'new', create: { cwd: '/new/workspace', agentPreset: 'owner-default' } }), { kind: 'new', create: { cwd: '/new/workspace', agentPreset: 'alcor' } })
  const current = { target: { kind: 'session', sessionId: 'stella-session' } }
  assert.deepEqual(alcorTarget({ kind: 'session', sessionId: 'stella-session' }, current), current.target)
  assert.throws(() => alcorTarget({ kind: 'session', sessionId: 'foreign-session' }, current), /Alcor 自动化只能使用原关联对话，或新建 Alcor 对话。/)
  assert.throws(() => alcorTarget({ kind: 'session', sessionId: 'foreign-session' }))
})

test('automation accepts all native timing families and rejects malformed or foreign fields', () => {
  const timings = [
    { kind: 'after', after_seconds: 120 }, { kind: 'at', at: '2099-01-01T00:00:00.000Z' },
    { kind: 'every', every_seconds: 3600 }, { kind: 'daily', daily: { time: '08:00', time_zone: 'Asia/Shanghai' } },
    { kind: 'weekly', weekly: { time: '09:00', time_zone: 'UTC', weekdays: [1] } },
    { kind: 'cron', cron: { expression: '0 8 * * *', time_zone: 'UTC' } },
    { kind: 'rrule', rrule: 'DTSTART;TZID=UTC:20990101T080000\nRRULE:FREQ=MONTHLY' },
  ]
  for (const timing of timings) assert(automationRequest.safeParse({ scope: 'alcor', action: 'create', request: { title: 'Test', prompt: 'Do it', timing } }).success)
  for (const request of [
    { scope: 'alcor', action: 'create', request: { title: '', prompt: 'Do it', timing: timings[0] } },
    { scope: 'alcor', action: 'edit', request: { id: 'id', action: 'update', patch: {} } },
    { scope: 'alcor', action: 'edit', request: { id: 'id', action: 'delete', sourceLabel: 'DSH' } },
    { scope: 'foreign', action: 'catalog' },
  ]) assert.equal(automationRequest.safeParse(request).success, false)
})
