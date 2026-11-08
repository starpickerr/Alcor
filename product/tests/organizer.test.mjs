import test from 'node:test'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { minorUnits, recordSchema, buildBriefing } from '../../packages/alcor-organizer/src/records.js'

const base = () => ({ id: randomUUID(), revision: 1, createdAt: '2026-11-04T00:00:00.000Z', updatedAt: '2026-11-04T00:00:00.000Z', archived: false, title: 'Test', details: '' })
test('money stays exact and rejects amounts that violate currency precision', () => {
  const bill = (amount, currency = 'CNY', extra = {}) => ({ ...base(), kind: 'bill', date: '2026-11-04', amount, currency, status: 'paid', direction: 'expense', ...extra })
  assert.equal(minorUnits('0.1', 'CNY') + minorUnits('0.2', 'CNY'), 30n)
  assert.equal(minorUnits('7', 'JPY'), 7n)
  for (const amount of ['0', '-1', '1e3', '1.001', '01', 'NaN']) assert.throws(() => minorUnits(amount, 'CNY'))
  assert.throws(() => minorUnits('1.1', 'JPY'))
  assert.throws(() => recordSchema.parse(bill('1.001')))
})
test('briefing uses half-open event days in the saved timezone and source revisions', () => {
  const event = { ...base(), kind: 'event', start: '2026-11-03T15:00:00.000Z', end: '2026-11-03T16:00:00.000Z', allDay: false, timeZone: 'Asia/Shanghai' }
  const allDay = { ...event, id: randomUUID(), start: '2026-11-03', end: '2026-11-04', allDay: true }
  const future = { ...base(), kind: 'task', due: '2026-11-05', status: 'open' }
  const due = { ...future, id: randomUUID(), due: '2026-11-04', revision: 3 }
  assert.equal(buildBriefing([event, allDay], '2026-11-04', '', base().createdAt).sources.length, 0)
  const report = buildBriefing([event, allDay, future, due, { ...due, id: randomUUID(), archived: true }], '2026-11-04', 'Untrusted <script>material</script>', base().createdAt)
  assert.deepEqual(report.sources, [{ id: due.id, revision: 3 }])
  assert.ok(report.body.includes('Untrusted <script>material</script>'))
  assert.equal(buildBriefing([event, allDay], '2026-11-03', '', base().createdAt).sources.length, 2)
  const point = { ...event, start: '2026-11-03T16:00:00.000Z', end: '2026-11-03T16:00:00.000Z' }
  assert.equal(recordSchema.parse(point).start, point.end)
  assert.equal(buildBriefing([point], '2026-11-04', '', base().createdAt).sources.length, 1)
  assert.equal(buildBriefing([point], '2026-11-03', '', base().createdAt).sources.length, 0)
  assert.throws(() => recordSchema.parse({ ...allDay, end: allDay.start }))
  assert.throws(() => recordSchema.parse({ ...event, timeZone: 'Unknown/Zone' }))
})
