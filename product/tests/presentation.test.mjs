import { test } from 'node:test'
import assert from 'node:assert/strict'
import { monthDays, taskMatches, taskGroup, eventOnDay, periodBills, ledgerCsv } from '../../packages/stella-secretary/src/presentation.js'

test('task tabs retain original overdue, incomplete, upcoming and completed rules', () => {
  const now = new Date('2026-11-04T12:00:00')
  const task = { status: 'open', due: '2026-11-03' }
  assert.equal(taskMatches(task, 'overdue', now), true)
  assert.equal(taskMatches(task, 'incomplete', now), false)
  assert.equal(taskGroup({ ...task, due: null }, now), 'noDate')
  assert.equal(taskMatches({ ...task, due: '2026-11-11' }, 'upcoming', now), true)
  assert.equal(taskMatches({ ...task, due: '2026-11-12' }, 'upcoming', now), false)
  assert.equal(taskGroup({ ...task, status: 'done' }, now), 'completed')
})

test('month grid covers adjacent months and event days use an exclusive end', () => {
  const days = monthDays('2026-02-14')
  assert.equal(days.length, 42)
  assert.equal(days[0], '2026-02-01')
  assert.equal(days.at(-1), '2026-03-14')
  assert.equal(eventOnDay({ allDay: true, start: '2026-11-04', end: '2026-11-05' }, '2026-11-05'), false)
  const event = { allDay: false, start: new Date('2026-11-04T23:00').toISOString(), end: new Date('2026-11-05T00:00').toISOString() }
  assert.equal(eventOnDay(event, '2026-11-04'), true)
  assert.equal(eventOnDay(event, '2026-11-05'), false)
})

test('ledger periods keep year and month boundaries, CSV preserves money and neutralizes formulas', () => {
  const bill = { kind: 'bill', date: '2026-11-04', createdAt: '', title: '=1+1', details: 'a,"b"\nnext', direction: 'expense', currency: 'CNY', amount: '12.30', status: 'paid', business: { category: '餐饮', occurred_at: '2026-11-04T12:30:17+08:00' } }
  const records = [bill, { ...bill, date: '2026-10-04' }, { ...bill, date: '2025-11-04' }]
  assert.equal(periodBills(records, '2026', '11').length, 1)
  assert.equal(periodBills(records, '2026', '').length, 2)
  const csv = ledgerCsv([bill])
  assert.ok(csv.includes('"\'=1+1"'))
  assert.ok(csv.includes('"12.30"'))
  assert.ok(csv.includes('"餐饮"'))
  assert.ok(csv.includes('"2026-11-04T12:30:17+08:00"'))
  assert.ok(csv.includes('"a,""b""\nnext"'))
})

test('a point event at midnight belongs to its own day', () => {
  const point = { allDay: false, start: new Date('2026-11-05T00:00').toISOString(), end: new Date('2026-11-05T00:00').toISOString() }
  assert.equal(eventOnDay(point, '2026-11-05'), true)
  assert.equal(eventOnDay(point, '2026-11-04'), false)
  assert.equal(eventOnDay(point, '2026-11-06'), false)
})

test('calendar initially reveals the first timed event without hiding earlier hours', async () => {
  const { calendarFocusMinute } = await import('../../packages/stella-secretary/src/presentation.js')
  const event = (start, end, allDay = false) => ({ start, end, allDay })
  assert.equal(calendarFocusMinute([event('2026-11-06T13:30', '2026-11-06T15:00')], ['2026-11-06']), 780)
  assert.equal(calendarFocusMinute([event('2026-11-05T23:00', '2026-11-06T02:00')], ['2026-11-06']), 0)
  assert.equal(calendarFocusMinute([event('2026-11-06', '2026-11-07', true)], ['2026-11-06']), 480)
  assert.equal(calendarFocusMinute([], ['2026-11-06']), 480)
  assert.equal(calendarFocusMinute([event('2026-11-06T18:30', '2026-11-06T21:00'), event('2026-11-07T09:55', '2026-11-07T12:30')], ['2026-11-06', '2026-11-07']), 565)
  assert.equal(calendarFocusMinute([event('2026-11-06T00:00', '2026-11-06T00:00')], ['2026-11-06']), 0)
})
