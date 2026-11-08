import { minorUnits, formatMinor } from '../../alcor-organizer/src/records.js'
import { localDate } from './presentation.js'

export function money(minor, currency) {
  const value = formatMinor(BigInt(minor), currency)
  const [whole, fraction] = value.split('.')
  const symbol = { CNY: '¥', USD: '$', EUR: '€', HKD: 'HK$', GBP: '£', JPY: 'JP¥' }[currency] ?? currency + ' '
  return (whole.startsWith('-') ? '-' : '') + symbol + whole.replace(/^-/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction === undefined ? '' : '.' + fraction)
}

export function periodRange(year, month) {
  const leap = Number(year) % 4 === 0 && (Number(year) % 100 !== 0 || Number(year) % 400 === 0)
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  return { start: `${year}-${month || '01'}-01`, end: `${year}-${month || '12'}-${month ? days[Number(month) - 1] : 31}` }
}

export const periodTitle = report => `${report.year}年${report.month ? `${report.month}月` : ''}`
export function periodStatus(report, today = localDate()) {
  const { start, end } = periodRange(String(report.year), report.month ? String(report.month).padStart(2, '0') : '')
  return today < start ? '未开始' : today > end ? '已结束' : '进行中'
}
export const shareText = basisPoints => `${(basisPoints / 100).toLocaleString('zh-CN', { maximumFractionDigits: 1 })}%`

export function donutArc(start, share) {
  if (share <= 0) return ''
  if (share >= 100) return 'M 80 22 A 58 58 0 1 1 80 138 A 58 58 0 1 1 80 22'
  const point = percent => {
    const angle = percent * Math.PI / 50
    return `${80 + 58 * Math.sin(angle)} ${80 - 58 * Math.cos(angle)}`
  }
  return `M ${point(start)} A 58 58 0 ${share > 50 ? 1 : 0} 1 ${point(start + share)}`
}

export function billValues(record) {
  const now = new Date()
  return { name: record?.title ?? '', amount: record?.amount ?? '', currency: record?.currency ?? 'CNY',
    entry_type: record?.direction ?? 'expense', category: record?.business?.category ?? '', note: record?.details ?? '',
    status: record?.business?.status ?? 'posted',
    occurred_at: record?.business?.occurred_at?.slice(0, 19) ?? `${record?.date ?? localDate(now)}T${now.toTimeString().slice(0, 8)}` }
}

export function billPayload(value, record) {
  const raw = record?.business
  let occurred_at = raw?.occurred_at
  if (!occurred_at || value.occurred_at !== occurred_at.slice(0, 19)) {
    const offset = -new Date(value.occurred_at).getTimezoneOffset()
    const zone = raw?.occurred_at.match(/(Z|[+-]\d{2}:\d{2})$/)?.[0] ?? `${offset < 0 ? '-' : '+'}${String(Math.floor(Math.abs(offset) / 60)).padStart(2, '0')}:${String(Math.abs(offset) % 60).padStart(2, '0')}`
    occurred_at = value.occurred_at + zone
  }
  const payload = { name: value.name, currency: value.currency, entry_type: value.entry_type, category: value.category.trim(), note: value.note, status: value.status, occurred_at }
  if (!raw || value.amount !== record.amount || value.currency !== record.currency) {
    const minor = minorUnits(value.amount, value.currency)
    if (minor > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('金额超出当前编辑范围。')
    payload.amount_minor = Number(minor)
  }
  return payload
}
