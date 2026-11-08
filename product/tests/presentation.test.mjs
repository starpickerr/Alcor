import { test } from 'node:test'
import assert from 'node:assert/strict'
import { monthDays, taskMatches, taskGroup, eventOnDay, periodBills, ledgerCsv } from '../../packages/alcor-secretary/src/presentation.js'

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
  const { calendarFocusMinute } = await import('../../packages/alcor-secretary/src/presentation.js')
  const event = (start, end, allDay = false) => ({ start, end, allDay })
  assert.equal(calendarFocusMinute([event('2026-11-06T13:30', '2026-11-06T15:00')], ['2026-11-06']), 780)
  assert.equal(calendarFocusMinute([event('2026-11-05T23:00', '2026-11-06T02:00')], ['2026-11-06']), 0)
  assert.equal(calendarFocusMinute([event('2026-11-06', '2026-11-07', true)], ['2026-11-06']), 480)
  assert.equal(calendarFocusMinute([], ['2026-11-06']), 480)
  assert.equal(calendarFocusMinute([event('2026-11-06T18:30', '2026-11-06T21:00'), event('2026-11-07T09:55', '2026-11-07T12:30')], ['2026-11-06', '2026-11-07']), 565)
  assert.equal(calendarFocusMinute([event('2026-11-06T00:00', '2026-11-06T00:00')], ['2026-11-06']), 0)
})

test('calendar agenda keeps all-day items before timed events and tasks in local chronological order', async () => {
  const { calendarOrder } = await import('../../packages/alcor-secretary/src/presentation.js')
  const event = (title,start,allDay=false) => ({title,start,allDay})
  const early=event('Morning event',new Date(2026,10,7,9).toISOString())
  const task=event('Noon reminder',new Date(2026,10,7,12).toISOString())
  const late=event('Evening event',new Date(2026,10,7,18).toISOString())
  const allDay=event('All-day reminder','2026-11-07',true)
  assert.deepEqual([late,task,allDay,early].sort(calendarOrder),[allDay,early,task,late])
  const tomorrow=event('Tomorrow',new Date(2026,10,8,8).toISOString())
  assert.deepEqual([tomorrow,late].sort(calendarOrder),[late,tomorrow])
})

test('calendar sidebar groups real source identities and keeps reminders in a single other row', async () => {
  const { calendarSources, categorySources } = await import('../../packages/alcor-secretary/src/presentation.js')
  const categories=[{id:'a',name:'课程',apple:{source_id:'icloud',source_name:'iCloud'}},{id:'b',name:'工作',apple:{source_id:'icloud',source_name:'iCloud'}},{id:'c',name:'课程',apple:{source_id:'exchange',source_name:'Exchange'}},{id:'local',name:'生活'}]
  const sources=calendarSources(categories)
  assert.equal(sources.length,3)
  assert.deepEqual(sources[0].items.map(item=>item.id),['a','b'])
  assert.deepEqual(sources[1].items.map(item=>item.id),['c'])
  assert.deepEqual(sources[2].items.map(item=>item.id),['local','__tasks__'])
  assert.equal(sources[2].name,'其他')
  assert.equal(categories.length,4)
  assert.deepEqual(categorySources(categories).map(source=>source.items.map(item=>item.id)),[['a','b'],['c'],['local']])
  assert.deepEqual(categorySources([]),[])
  assert.equal(categories.some(item=>item.id==='__tasks__'),false)
  assert.equal(calendarSources([])[0].items[0].name,'事务')
})

test('week labels retain Chinese month/year context at month and year boundaries', async () => {
  const { calendarWeekLabel }=await import('../../packages/alcor-secretary/src/presentation.js')
  assert.equal(calendarWeekLabel('2026-11-01','2026-11-07'),'2026年11月1日 — 7日')
  assert.equal(calendarWeekLabel('2026-11-29','2026-12-05'),'2026年11月29日 — 12月5日')
  assert.equal(calendarWeekLabel('2026-12-27','2027-01-02'),'2026年12月27日 — 2027年1月2日')
})
