import { addDays } from './presentation.js'
export const arrayValue = value => Array.isArray(value) ? value : JSON.parse(value || '[]')
export const localTime = value => { const date = new Date(value); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${date.toTimeString().slice(0, 5)}` }
export function dayInZone(value, zone) {
  const parts = new Intl.DateTimeFormat('en', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value))
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type).value).join('-')
}
export function dateTimeInZone(day, time, zone) {
  const wanted = Date.parse(`${day}T${time}:00Z`)
  let instant = wanted
  const format = new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
  for (let attempt = 0; attempt < 4; attempt++) {
    const parts = Object.fromEntries(format.formatToParts(new Date(instant)).map(part => [part.type, part.value]))
    const actual = Date.parse(`${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}Z`)
    if (actual === wanted) return new Date(instant).toISOString()
    instant += wanted - actual
  }
  throw new Error('这个时区的所选时间不存在，请选择其他时间。')
}
export const midnight = (day, zone) => dateTimeInZone(day, '00:00', zone)

export function calendarTaskRecord(row) {
  if (!row.due_on) return null
  const start = row.due_time ? dateTimeInZone(row.due_on, row.due_time, row.floating ? Intl.DateTimeFormat().resolvedOptions().timeZone : row.timezone || 'UTC') : row.due_on
  return { id: `task:${row.id}`, kind: 'task', title: row.title, details: row.notes, due: row.due_on,
    status: row.status === 'completed' ? 'done' : 'open', allDay: !row.due_time, start,
    end: row.due_time ? start : addDays(row.due_on, 1), groupId: row.category_id ? `task:${row.category_id}` : null, business: row }
}

export function dateTimeDraft(instant, zone) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(instant)).map(part => [part.type, part.value]))
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}

export function calendarDraftTimes(start, end, allDay) {
  if (!allDay) return { start: `${start}T09:00`, end: `${addDays(end, -1)}T10:00` }
  const last = end.slice(0, 10)
  return { start: start.slice(0, 10), end: last <= start.slice(0, 10) || end.slice(11) !== '00:00' ? addDays(last, 1) : last }
}

export function calendarRecord(row) {
  const originalZone = row.repeat_timezone || 'UTC'
  const zone = row.floating ? Intl.DateTimeFormat().resolvedOptions().timeZone : originalZone
  const instant = value => { const wall = dateTimeDraft(value,originalZone); return row.floating ? dateTimeInZone(wall.slice(0,10),wall.slice(11),zone) : value }
  const last = dayInZone(row.end_at,originalZone)
  const endWall = dateTimeDraft(row.end_at,originalZone)
  return { id: row.id, kind: 'event', title: row.title, details: row.description, allDay: !!row.all_day, timeZone: zone,
    start: row.all_day ? dayInZone(row.start_at,originalZone) : instant(row.start_at), end: row.all_day ? endWall.slice(11)!=='00:00' ? addDays(last,1) : last : instant(row.end_at), business: row }
}

export function eventLanes(events) {
  const placements = new Map()
  let group = []
  let ends = []
  let groupEnd = -Infinity
  const flush = () => { for (const item of group) placements.set(item.id, { left: item.lane / ends.length * 100, width: 100 / ends.length }) }
  for (const event of [...events].sort((left, right) => Date.parse(left.start) - Date.parse(right.start) || Date.parse(left.end) - Date.parse(right.end))) {
    const start = Date.parse(event.start)
    if (start >= groupEnd) { flush(); group = []; ends = [] }
    let lane = ends.findIndex(end => end <= start)
    if (lane < 0) lane = ends.length
    ends[lane] = Math.max(Date.parse(event.end), start + 1)
    group.push({ id: event.id, lane }); groupEnd = Math.max(...ends)
  }
  flush()
  return placements
}
