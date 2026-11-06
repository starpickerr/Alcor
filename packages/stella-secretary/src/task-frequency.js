export const repeatOptions = { none: '单次', daily: '每天', weekdays: '工作日', weekly: '每周', monthly: '每月', yearly: '每年', custom: '自定义' }
export function repeatDraft(raw = {}) {
  const frequency = raw.repeat_frequency || 'none'
  const days = Array.isArray(raw.repeat_days) ? raw.repeat_days : JSON.parse(raw.repeat_days || '[]')
  const interval = raw.repeat_interval ?? 1
  return { repeat_mode: interval !== 1 ? 'custom' : frequency === 'weekly' && days.join(',') === '0,1,2,3,4' ? 'weekdays' : frequency,
    repeat_frequency: frequency === 'none' ? 'daily' : frequency, repeat_interval: interval, repeat_days: days, repeat_until: raw.repeat_until || '' }
}
export function repeatPayload(value) {
  const frequency = value.repeat_mode === 'custom' ? value.repeat_frequency : value.repeat_mode === 'weekdays' ? 'weekly' : value.repeat_mode
  const interval = value.repeat_mode === 'custom' ? Number(value.repeat_interval) : 1
  if (!['none', 'daily', 'weekly', 'monthly', 'yearly'].includes(frequency) || !Number.isInteger(interval) || interval < 1 || interval > 99) throw new Error('重复间隔须为 1–99 的整数。')
  return { repeat_frequency: frequency, repeat_interval: frequency === 'none' ? 1 : interval,
    repeat_days: value.repeat_mode === 'weekdays' ? [0,1,2,3,4] : frequency === 'weekly' ? value.repeat_days : [],
    repeat_until: frequency === 'none' ? null : value.repeat_until || null }
}

export function repeatLabel(raw) {
  const frequency = raw.repeat_frequency || 'none'
  const interval = raw.repeat_interval || 1
  return interval === 1 ? repeatOptions[frequency] : `每 ${interval} ${{ daily: '天', weekly: '周', monthly: '月', yearly: '年' }[frequency]}`
}
