export const arrayValue = value => Array.isArray(value) ? value : JSON.parse(value || '[]')
export const localTime = value => { const date = new Date(value); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${date.toTimeString().slice(0, 5)}` }
export function dayInZone(value, zone) {
  const parts = new Intl.DateTimeFormat('en', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value))
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type).value).join('-')
}
export function midnight(day, zone) {
  const wanted = Date.parse(`${day}T00:00:00Z`)
  let instant = wanted
  const format = new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
  for (let attempt = 0; attempt < 4; attempt++) {
    const parts = Object.fromEntries(format.formatToParts(new Date(instant)).map(part => [part.type, part.value]))
    const actual = Date.parse(`${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}Z`)
    if (actual === wanted) return new Date(instant).toISOString()
    instant += wanted - actual
  }
  throw new Error('这个时区的当天零点不存在，请选择其他日期或使用具体时间。')
}
export function calendarRecord(row) {
  const zone = row.repeat_timezone || 'UTC'
  return { id: row.id, kind: 'event', title: row.title, details: row.description, allDay: !!row.all_day, timeZone: zone,
    start: row.all_day ? dayInZone(row.start_at, zone) : row.start_at, end: row.all_day ? dayInZone(row.end_at, zone) : row.end_at, business: row }
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
