import test from 'node:test'
import assert from 'node:assert/strict'
import { midnight, calendarRecord, calendarTaskRecord, dateTimeDraft, dateTimeInZone, calendarDraftTimes, eventLanes } from '../../packages/alcor-secretary/src/organizer-forms.js'

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

test('simultaneous point events occupy separate lanes without changing their times', () => {
  const start = '2026-11-05T09:00:00Z'
  const points = [{ id: 'a', start, end: start }, { id: 'b', start, end: start }]
  const lanes = eventLanes(points)
  assert.deepEqual(lanes.get('a'), { left: 0, width: 50 })
  assert.deepEqual(lanes.get('b'), { left: 50, width: 50 })
  assert.equal(points[0].end, start)
})


test('calendar tasks remain point reminders with original identity, category, timezone and completion', () => {
  const row = { id: 'series@2026-11-07', series_id: 'series', title: 'planA task', due_on: '2026-11-07', due_time: '04:00', timezone: 'Asia/Shanghai', category_id: 'planA', status: 'completed' }
  const projected = calendarTaskRecord(row)
  assert.equal(projected.kind, 'task')
  assert.equal(projected.start, '2026-11-06T20:00:00.000Z')
  assert.equal(projected.end, projected.start)
  assert.equal(projected.status, 'done')
  assert.equal(projected.groupId, 'task:planA')
  assert.equal(projected.business.id, row.id)
  assert.equal(calendarTaskRecord({...row, due_on: null}), null)
  const allDay = calendarTaskRecord({...row, due_time: null})
  assert.equal(allDay.start, '2026-11-07')
  assert.equal(allDay.end, '2026-11-08')
  assert.equal(allDay.allDay, true)
})


test('calendar editor preserves event-zone time and uses exclusive all-day ends', () => {
  const instant = '2026-11-07T09:00:00Z'
  const draft = dateTimeDraft(instant, 'America/New_York')
  assert.equal(draft, '2026-11-07T04:00')
  assert.equal(dateTimeInZone(draft.slice(0, 10), draft.slice(11), 'America/New_York'), '2026-11-07T09:00:00.000Z')
  assert.deepEqual(calendarDraftTimes('2026-11-07T09:00', '2026-11-07T10:00', true), {start:'2026-11-07',end:'2026-11-08'})
  assert.deepEqual(calendarDraftTimes('2026-11-07', '2026-11-08', false), {start:'2026-11-07T09:00',end:'2026-11-07T10:00'})
})

 test('floating events retain wall time across display zones and all-day boundaries stay exclusive', () => {
  const previous=process.env.TZ
  try {
    process.env.TZ='America/New_York'
    const raw={id:'floating',title:'Floating',start_at:'2026-11-07T01:00:00Z',end_at:'2026-11-07T02:00:00Z',repeat_timezone:'Asia/Shanghai',floating:true}
    const record=calendarRecord(raw)
    assert.equal(new Date(record.start).toISOString(),'2026-11-07T14:00:00.000Z')
    assert.equal(calendarRecord({...raw,all_day:true,start_at:'2026-11-06T16:00:00Z',end_at:'2026-11-07T15:59:59Z'}).end,'2026-11-08')
  } finally { if(previous===undefined) delete process.env.TZ; else process.env.TZ=previous }
})
