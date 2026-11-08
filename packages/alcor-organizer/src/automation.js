import { z } from 'zod'

export const automationDeleteUnavailable = '无法确认待投递或未知投递状态，Alcor 暂不支持删除自动化。你可选择暂停；本次未删除或暂停任务。'

const zone = z.string().min(1).max(100)
const time = z.string().min(1).max(30)
const create = z.object({ cwd: z.string().min(1).max(4096).optional(), agentPreset: z.string().min(1).max(200).optional() }).strict()
const target = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('session'), sessionId: z.string().min(1).max(200) }).strict(),
  z.object({ kind: z.literal('new_once'), create: create.optional() }).strict(),
  z.object({ kind: z.literal('new'), create: create.optional() }).strict(),
])
export const timing = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('after'), after_seconds: z.number().int().positive() }).strict(),
  z.object({ kind: z.literal('at'), at: z.union([z.string().datetime(), z.object({ date: z.string().min(1).max(10), time, time_zone: zone }).strict()]) }).strict(),
  z.object({ kind: z.literal('every'), every_seconds: z.number().int().positive() }).strict(),
  z.object({ kind: z.literal('daily'), daily: z.object({ time, time_zone: zone }).strict() }).strict(),
  z.object({ kind: z.literal('weekly'), weekly: z.object({ time, time_zone: zone, weekdays: z.array(z.number().int().min(1).max(7)).min(1).max(7) }).strict() }).strict(),
  z.object({ kind: z.literal('cron'), cron: z.object({ expression: z.string().min(1).max(1000), time_zone: zone }).strict() }).strict(),
  z.object({ kind: z.literal('rrule'), rrule: z.string().min(1).max(12000) }).strict(),
])
const content = { title: z.string().trim().min(1).max(120), prompt: z.string().trim().min(1).max(12000), timing, target }
const id = z.string().min(1).max(200)
export const automationDeleteTargets = z.array(z.object({ id, expectedUpdatedAt: z.string().nullable() }).strict())
  .refine(rows => new Set(rows.map(row => row.id)).size === rows.length, '删除目标不能重复。')
const patch = z.object({ ...content, active: z.boolean() }).partial().strict().refine(value => Object.keys(value).length > 0)
export const automationRequest = z.intersection(z.object({ scope: z.enum(['alcor', 'all']) }), z.discriminatedUnion('action', [
  z.object({ scope: z.enum(['alcor', 'all']), action: z.literal('catalog') }).strict(),
  z.object({ scope: z.enum(['alcor', 'all']), action: z.literal('create'), request: z.object({ ...content, target: target.optional() }).strict() }).strict(),
  z.object({ scope: z.enum(['alcor', 'all']), action: z.literal('edit'), request: z.discriminatedUnion('action', [
    z.object({ id, action: z.literal('update'), patch, expectedUpdatedAt: z.string().nullable().optional() }).strict(),
    z.object({ id, action: z.literal('delete'), expectedUpdatedAt: z.string().nullable().optional() }).strict(),
  ]) }).strict(),
  z.object({ scope: z.enum(['alcor', 'all']), action: z.literal('run'), request: z.object({ id }).strict() }).strict(),
  z.object({ scope: z.enum(['alcor', 'all']), action: z.literal('history'), request: z.object({ id, limit: z.number().int().min(1).max(100), before: z.string().min(1).max(200).optional() }).strict() }).strict(),
  z.object({ scope: z.enum(['alcor', 'all']), action: z.literal('deleteInactive'), targets: automationDeleteTargets.optional() }).strict(),
]))

export function alcorTarget(requested, current) {
  const value = requested ?? { kind: 'new_once' }
  if (value.kind === 'session') {
    if (!current || current.target.kind !== 'session' || current.target.sessionId !== value.sessionId) throw new Error('Alcor 自动化只能使用原关联对话，或新建 Alcor 对话。')
    return value
  }
  return { ...value, create: { ...value.create, agentPreset: 'alcor' } }
}
