import { addDays } from './presentation.js'
import { arrayValue } from './organizer-forms.js'

export const priorityLabels = { 0: '无', 1: '低', 2: '中', 3: '高' }
export const groupName = group => group?.display_name ?? group?.name
export const recordRepeats = record => [record.business?.repeat_frequency, record.business?.native_recurrence?.frequency].some(value => value && value !== 'none')
export function recordId(record) {
  const raw = record.business ?? {}
  const id = raw.series_id ?? raw.id ?? record.id
  return record.kind === 'task' ? id.replace(/^task:/, '').split('@')[0] : id
}
export function recordEditTarget(record, templates) {
  if (record.kind==='event' && record.business?.series_id && !record.business.apple) return templates.find(item => item.kind==='event' && item.id===record.business.series_id) ?? record
  return record.kind === 'task' ? templates.find(item => item.kind === 'task' && item.id === recordId(record)) ?? { ...record, id: recordId(record) } : record
}
export function recordPermissions(record, busy = false) {
  const raw = record.business ?? {}
  const available = !busy && !record.archived && !raw.apple_pending?.length && !raw.apple_history
  const edit = available && raw.apple?.can_modify !== false
  return { edit, remove: available && (raw.apple ? (raw.apple.can_delete ?? raw.apple.can_modify) !== false : true), complete: record.kind === 'task' && edit && !raw.occurrence_unavailable }
}
export function recordMenuEntries(record, groups, busy = false) {
  const rights = recordPermissions(record, busy)
  const task = record.kind === 'task'
  return [
    { id: 'details', label: '显示简介' },
    { id: 'edit', label: '编辑详情', disabled: !rights.edit },
    ...(task ? [{ id: 'complete', label: record.status === 'done' ? '标为未完成' : '标为已完成', disabled: !rights.complete }] : []),
    ...(groups.length ? [{ id: 'group', label: task ? '清单' : '日历', disabled: !rights.edit,
      submenu: groups.map(group => ({ id: `group:${group.id}`, label: group.apple?.source_name ? `${groupName(group)} · ${group.apple.source_name}` : groupName(group), disabled: !rights.edit || group.apple?.can_modify === false || !!group.apple_pending?.length || group.id === record.business?.category_id })) }] : []),
    { type: 'separator', id: 'copy-separator' },
    { id: 'copy', label: '拷贝文本' },
    { type: 'separator', id: 'delete-separator' },
    { id: 'delete', label: task && recordRepeats(record) ? '删除整个系列' : task ? '删除事务' : '删除日程', danger: true, disabled: !rights.remove },
  ]
}
export function recordAlarms(raw) {
  return Object.hasOwn(raw, 'alarms') ? raw.alarms ?? [] : raw.remind_at ? [{ absolute: raw.remind_at }] : []
}
export function recordRepeatLabel(raw) {
  const rule = raw.native_recurrence?.frequency ? raw.native_recurrence : { frequency: raw.repeat_frequency, interval: raw.repeat_interval, days: arrayValue(raw.repeat_days), until: raw.repeat_until }
  const name = { daily: '每天', weekly: '每周', monthly: '每月', yearly: '每年' }[rule.frequency]
  if (!rule.frequency || rule.frequency === 'none') return '永不'
  if (!name) return '按 Apple 规则重复'
  const interval = rule.interval ?? 1
  const parts = [interval > 1 ? `每 ${interval} ${{ daily: '天', weekly: '周', monthly: '个月', yearly: '年' }[rule.frequency]}` : name]
  if (rule.weekday_ordinals?.some(day => day.week)) parts.push(rule.weekday_ordinals.map(day => `${day.week ? day.week < 0 ? `倒数第 ${-day.week} 个` : `第 ${day.week} 个` : ''}周${'一二三四五六日'[day.day]}`).join('、'))
  else if (rule.days?.length) parts.push(rule.days.map(day => `周${'一二三四五六日'[day]}`).join('、'))
  if (rule.month_days?.length) parts.push(`每月 ${rule.month_days.join('、')} 日`)
  if (rule.months?.length) parts.push(`${rule.months.join('、')} 月`)
  if (rule.rule_count>1) parts.push(`${rule.rule_count} 条规则`)
  if (rule.count) parts.push(`共 ${rule.count} 次`)
  if (rule.until) parts.push(`截至 ${rule.until.slice(0, 10)}`)
  return parts.join(' · ')
}
export function recordClipboardText(record) {
  const raw = record.business ?? {}
  const when = record.kind === 'task' ? [record.due ?? raw.due_on, raw.due_time].filter(Boolean).join(' ') : record.allDay ? `${record.start}${record.end > addDays(record.start,1) ? ` — ${addDays(record.end,-1)}` : ''} · 全天` : [record.start, record.end].map(value => new Date(value).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })).join(' — ')
  return [record.title, when, record.details, raw.location, raw.url].filter(Boolean).join('\n')
}

export function taskDeleteMessage(record) {
  if (!recordRepeats(record)) return '将同时删除其子事务，无法撤销。'
  return record.business?.apple || record.business?.source_type==='apple_reminder' ? '将删除当前事务并停止后续重复，无法撤销。' : '将删除整个重复系列及其完成记录，无法撤销。'
}
