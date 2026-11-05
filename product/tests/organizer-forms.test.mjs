import test from 'node:test'
import assert from 'node:assert/strict'
import { midnight, calendarRecord, eventLanes } from '../../packages/stella-secretary/src/organizer-forms.js'

test('all-day form dates keep their selected timezone across DST and calendar projection', () => {
  assert.equal(midnight('2026-03-08', 'America/New_York'), '2026-03-08T05:00:00.000Z')
  assert.equal(midnight('2026-03-09', 'America/New_York'), '2026-03-09T04:00:00.000Z')
  assert.equal(midnight('2026-11-05', 'Asia/Shanghai'), '2026-11-04T16:00:00.000Z')
  const event = calendarRecord({ id: 'series@2026-11-05', title: 'Birthday', description: '', all_day: true, start_at: '2026-11-04T16:00:00+00:00', end_at: '2026-11-05T16:00:00+00:00', repeat_timezone: 'Asia/Shanghai', series_id: 'series' })
  assert.equal(event.start, '2026-11-05')
  assert.equal(event.end, '2026-11-06')
  assert.equal(event.business.series_id, 'series')
  assert.throws(() => midnight('2026-11-05', 'Invalid/Zone'), RangeError)
})

test('overlapping calendar events remain separate clickable columns', () => {
  const events = [['a', '09:00', '11:00'], ['b', '10:00', '12:00'], ['c', '11:00', '12:00'], ['d', '13:00', '14:00']].map(([id, start, end]) => ({ id, start: `2026-11-05T${start}:00Z`, end: `2026-11-05T${end}:00Z` }))
  const lanes = eventLanes(events)
  assert.deepEqual(lanes.get('a'), { left: 0, width: 50 })
  assert.deepEqual(lanes.get('b'), { left: 50, width: 50 })
  assert.deepEqual(lanes.get('c'), { left: 0, width: 50 })
  assert.deepEqual(lanes.get('d'), { left: 0, width: 100 })
})
