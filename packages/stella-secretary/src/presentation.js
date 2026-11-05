import { matchesTodoView, todoGroup } from './todo-view.js'

export const localDate = (value = new Date()) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
export const addDays = (day, count) => { const value = new Date(`${day}T12:00`); value.setDate(value.getDate() + count); return localDate(value) }
const originalTodo = record => ({ status: record.business?.status ?? (record.status === 'done' ? 'completed' : 'todo'), due_on: record.due, due_time: record.business?.due_time ?? null, timezone: record.business?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone })
export const taskMatches = (record, view, now) => matchesTodoView(originalTodo(record), view, now)
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
