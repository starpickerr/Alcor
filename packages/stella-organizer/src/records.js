import { z } from 'zod'

export const text = z.string().trim().min(1)
export const date = z.iso.date()
const instant = z.iso.datetime()
const base = { id: z.uuid(), revision: z.number().int().positive(), createdAt: instant, updatedAt: instant, archived: z.boolean(), business: z.json().optional() }
const task = z.object({ ...base, kind: z.literal('task'), title: text, details: z.string(), due: date.nullable(), status: z.enum(['open', 'done']) }).strict()
const event = z.object({ ...base, kind: z.literal('event'), title: text, details: z.string(), start: z.string(), end: z.string(), allDay: z.boolean(), timeZone: z.string().max(100) }).strict().superRefine((value, ctx) => {
  const format = value.allDay ? date : instant
  if (!format.safeParse(value.start).success || !format.safeParse(value.end).success || value.end <= value.start) ctx.addIssue({ code: 'custom', message: '结束时间必须晚于开始时间；全天日程结束日期不包含当天。' })
  try { new Intl.DateTimeFormat('zh-CN', { timeZone: value.timeZone }) } catch { ctx.addIssue({ code: 'custom', message: '时区无效。' }) }
})
export const currencies = { CNY: 2, USD: 2, EUR: 2, HKD: 2, GBP: 2, JPY: 0 }
const bill = z.object({ ...base, kind: z.literal('bill'), title: text, details: z.string(), date, currency: z.string().min(1), amount: z.string(), direction: z.enum(['expense', 'income']), status: z.enum(['planned', 'paid', 'reversed']) }).strict().superRefine((value, ctx) => {
  try { minorUnits(value.amount, value.currency) } catch (error) { ctx.addIssue({ code: 'custom', message: error.message }) }
})
const briefing = z.object({ ...base, kind: z.literal('briefing'), title: text, date, notes: z.string().max(12000), body: z.string().max(100000), sources: z.array(z.object({ id: z.uuid(), revision: z.number().int().positive() }).strict()).max(2000) }).strict()
export const recordSchema = z.discriminatedUnion('kind', [task, event, bill, briefing])
export const writeRequest = z.object({ record: recordSchema, expectedRevision: z.number().int().nonnegative() }).strict()
export const archiveRequest = z.object({ id: z.uuid(), expectedRevision: z.number().int().positive(), archived: z.boolean() }).strict()

export function minorUnits(amount, currency) {
  const digits = currencies[currency] ?? 2
  if (!new RegExp(`^(0|[1-9]\\d*)${digits ? `(\\.\\d{1,${digits}})?` : ''}$`).test(amount)) throw new Error('金额须为正数，且小数位符合币种。')
  const [whole, fraction = ''] = amount.split('.')
  const result = BigInt(whole + fraction.padEnd(digits, '0'))
  if (result <= 0n) throw new Error('金额必须大于零。')
  return result
}

export function formatMinor(value, currency) {
  const digits = currencies[currency] ?? 2
  const sign = value < 0n ? '-' : ''
  const raw = (value < 0n ? -value : value).toString().padStart(digits + 1, '0')
  return `${sign}${digits ? raw.slice(0, -digits) + '.' + raw.slice(-digits) : raw}`
}

export function buildBriefing(records, reportDate, notes, now) {
  date.parse(reportDate)
  const active = records.filter(item => !item.archived)
  const tasks = active.filter(item => item.kind === 'task' && item.status === 'open' && (!item.due || item.due <= reportDate))
  const events = active.filter(item => {
    if (item.kind !== 'event') return false
    if (item.allDay) return item.start <= reportDate && item.end > reportDate
    const localDay = instant => {
      const parts = new Intl.DateTimeFormat('en', { timeZone: item.timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(instant))
      return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type).value).join('-')
    }
    return localDay(item.start) <= reportDate && localDay(Date.parse(item.end) - 1) >= reportDate
  })
  const bills = active.filter(item => item.kind === 'bill' && item.status === 'planned' && item.date <= reportDate)
  const selected = [...tasks, ...events, ...bills]
  if (selected.length > 2000) throw new Error('本日条目过多，请先缩小范围。')
  const section = (title, lines) => `## ${title}\n${lines.length ? lines.map(line => `- ${line}`).join('\n') : '已保存记录中暂无此项。'}`
  return { kind: 'briefing', title: `${reportDate} 简报`, date: reportDate, notes, body: [
    `# ${reportDate} 简报`, `生成时间：${now}\n范围：仅本机已保存的事务、日程、账单和本次提供的材料；未读取邮件、课程平台或旧资料。`,
    section('需要关注的事务', tasks.map(item => `${item.title}${item.due ? `（截止 ${item.due}${item.due < reportDate ? '，已逾期' : ''}）` : '（未设截止）'}`)),
    section('当日安排', events.map(item => `${item.title} · ${item.start} → ${item.end}（${item.allDay ? '全天，结束日期不含当天' : item.timeZone}）`)),
    section('待处理账单', bills.map(item => `${item.title} · ${item.currency} ${item.amount} · ${item.date}`)),
    `## 你提供的材料\n${notes || '未提供额外材料。'}`,
  ].join('\n\n'), sources: selected.map(({ id, revision }) => ({ id, revision })) }
}
