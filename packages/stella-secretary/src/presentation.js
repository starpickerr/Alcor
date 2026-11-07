import { matchesTodoView, todoGroup } from './todo-view.js'

export const localDate = (value = new Date()) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
export const addDays = (day, count) => { const value = new Date(`${day}T12:00`); value.setDate(value.getDate() + count); return localDate(value) }
const originalTodo = record => ({ status: record.business?.status ?? (record.status === 'done' ? 'completed' : 'todo'), due_on: record.due, due_time: record.business?.due_time ?? null, timezone: record.business?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone })
export const taskMatches = (record, view, now) => view === 'pending' ? !record.business?.occurrence_unavailable && !['completed', 'cancelled'].includes(originalTodo(record).status) : matchesTodoView(originalTodo(record), view, now)
export const taskGroup = (record, now) => todoGroup(originalTodo(record), record.due, now)

export function monthDays(day) {
  const selected = new Date(`${day}T12:00`)
  const first = new Date(selected.getFullYear(), selected.getMonth(), 1, 12)
  first.setDate(1 - first.getDay())
  return Array.from({ length: 42 }, (_, index) => addDays(localDate(first), index))
}

export function eventOnDay(event, day) {
  if (event.allDay) return event.start <= day && event.end > day
  const start = new Date(`${day}T00:00`)
  const end = new Date(start); end.setDate(end.getDate() + 1)
  if (event.start === event.end) return Date.parse(event.start) >= start.getTime() && Date.parse(event.start) < end.getTime()
  return Date.parse(event.start) < end.getTime() && Date.parse(event.end) > start.getTime()
}

export function periodBills(records, year, month) {
  const prefix = `${year}${month ? '-' + month : ''}`
  return records.filter(item => item.kind === 'bill' && item.date.startsWith(prefix)).sort((a, b) => b.date.localeCompare(a.date) || b.createdAt.localeCompare(a.createdAt))
}

export function ledgerCsv(records) {
  const quote = value => '"' + String(value).replace(/^[\s]*[=+@\-]/, value => "'" + value).replaceAll('"', '""') + '"'
  const rows = [['发生时间', '分类', '名称', '收支', '币种', '金额', '状态', '备注'], ...records.map(item => [item.business?.occurred_at ?? item.date, item.business?.category ?? '', item.title, item.direction === 'income' ? '收入' : '支出', item.currency, item.amount, item.status === 'paid' ? '已记账' : item.status === 'reversed' ? '已冲正' : '待处理', item.details])]
  return '\ufeff' + rows.map(row => row.map(quote).join(',')).join('\r\n')
}
export function calendarFocusMinute(records, days) {
  let first = Infinity
  for (const day of days) {
    for (const item of records) {
      if (item.allDay || !eventOnDay(item, day)) continue
      const start = new Date(item.start)
      first = Math.min(first, localDate(start) < day ? 0 : start.getHours() * 60 + start.getMinutes())
    }
  }
  return Number.isFinite(first) ? Math.max(0, first - 30) : 8 * 60
}

export function calendarOrder(left, right) {
  const day = item => item.allDay ? item.start : localDate(new Date(item.start))
  return day(left).localeCompare(day(right)) || Number(right.allDay)-Number(left.allDay) || Date.parse(left.start)-Date.parse(right.start) || left.title.localeCompare(right.title,'zh-CN')
}

export function categorySources(categories) {
  const sources = new Map()
  for (const item of categories) {
    const id = item.apple?.source_id || item.apple?.source_name || '__other__'
    if (!sources.has(id)) sources.set(id,{id,name:item.apple?.source_name || '其他',items:[]})
    sources.get(id).items.push(item)
  }
  const other = sources.get('__other__')
  sources.delete('__other__')
  return [...sources.values(),...(other ? [other] : [])]
}

export function calendarSources(categories) {
  const sources = categorySources(categories)
  let other = sources.find(source => source.id === '__other__')
  if (!other) {other={id:'__other__',name:'其他',items:[]};sources.push(other)}
  other.items.push({id:'__tasks__',name:'事务',color:'var(--stella-brand)'})
  return sources
}

export function calendarWeekLabel(first, last) {
  const start = new Date(`${first}T12:00`)
  const end = new Date(`${last}T12:00`)
  const options = {day:'numeric',...(first.slice(0,7) !== last.slice(0,7) ? {month:'long'} : {}),...(first.slice(0,4) !== last.slice(0,4) ? {year:'numeric'} : {})}
  return `${start.toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric'})} — ${end.toLocaleDateString('zh-CN',options)}`
}
