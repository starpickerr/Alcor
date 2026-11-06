import test from 'node:test'
import assert from 'node:assert/strict'
import { repeatDraft, repeatPayload } from '../../packages/stella-secretary/src/task-frequency.js'
test('frequency presets and custom rules round-trip without losing an existing interval', () => {
  const raw = { repeat_frequency: 'weekly', repeat_interval: 2, repeat_days: '[0,2]', repeat_until: '2027-01-01' }
  assert.deepEqual(repeatPayload(repeatDraft(raw)), { ...raw, repeat_days: [0,2] })
  assert.deepEqual(repeatPayload({ ...repeatDraft(), repeat_mode: 'weekdays' }).repeat_days, [0,1,2,3,4])
  assert.equal(repeatPayload(repeatDraft()).repeat_frequency, 'none')
  for (const interval of [0, 100, 1.5, '']) assert.throws(() => repeatPayload({ ...repeatDraft(), repeat_mode: 'custom', repeat_interval: interval }))
})
