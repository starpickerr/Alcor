import test from 'node:test'
import assert from 'node:assert/strict'
import { automationRequest, stellaTarget } from '../../packages/stella-organizer/src/automation.js'

test('Stella automation keeps its identity across new targets and workspace changes', () => {
  assert.deepEqual(stellaTarget(undefined), { kind: 'new_once', create: { agentPreset: 'stella' } })
  assert.deepEqual(stellaTarget({ kind: 'new', create: { cwd: '/new/workspace', agentPreset: 'owner-default' } }), { kind: 'new', create: { cwd: '/new/workspace', agentPreset: 'stella' } })
  const current = { target: { kind: 'session', sessionId: 'stella-session' } }
  assert.deepEqual(stellaTarget({ kind: 'session', sessionId: 'stella-session' }, current), current.target)
  assert.throws(() => stellaTarget({ kind: 'session', sessionId: 'foreign-session' }, current))
  assert.throws(() => stellaTarget({ kind: 'session', sessionId: 'foreign-session' }))
})

test('automation accepts all native timing families and rejects malformed or foreign fields', () => {
  const timings = [
    { kind: 'after', after_seconds: 120 }, { kind: 'at', at: '2099-01-01T00:00:00.000Z' },
    { kind: 'every', every_seconds: 3600 }, { kind: 'daily', daily: { time: '08:00', time_zone: 'Asia/Shanghai' } },
    { kind: 'weekly', weekly: { time: '09:00', time_zone: 'UTC', weekdays: [1] } },
    { kind: 'cron', cron: { expression: '0 8 * * *', time_zone: 'UTC' } },
    { kind: 'rrule', rrule: 'DTSTART;TZID=UTC:20990101T080000\nRRULE:FREQ=MONTHLY' },
  ]
  for (const timing of timings) assert(automationRequest.safeParse({ scope: 'stella', action: 'create', request: { title: 'Test', prompt: 'Do it', timing } }).success)
  for (const request of [
    { scope: 'stella', action: 'create', request: { title: '', prompt: 'Do it', timing: timings[0] } },
    { scope: 'stella', action: 'edit', request: { id: 'id', action: 'update', patch: {} } },
    { scope: 'stella', action: 'edit', request: { id: 'id', action: 'delete', sourceLabel: 'DSH' } },
    { scope: 'foreign', action: 'catalog' },
  ]) assert.equal(automationRequest.safeParse(request).success, false)
})
