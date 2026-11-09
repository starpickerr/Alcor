import { z } from 'zod'
export const memoryAudience = z.string().max(250).regex(/^(personal|group:[1-9]\d{0,19}|channel:[a-zA-Z0-9_.:-]+)$/)
const id = z.string().min(1).max(200)
const source = z.object({ kind: id, at: z.string().max(64), sessionId: id.optional(),
  channelId:id.optional(),accountId:id.optional(),channelUserId:id.optional(),qqUserId: id.optional(), qqGroupId: id.optional() }).strict()
const entry = z.object({ id, audience: memoryAudience, subject: id, content: z.string().min(1).max(65536), source }).strict()
export const experiences = z.object({
  format: z.literal('alcor.experiences.v1'), identityId: z.string().uuid(), audience: memoryAudience,
  entries: z.array(entry).max(4096), habits: z.array(entry.extend({ evidence: z.array(id).min(2).max(5) })).max(12),
}).strict().superRefine((doc, ctx) => {
  const entries = new Map(doc.entries.map(item => [item.id, item]))
  const rows = [...doc.entries, ...doc.habits]
  if (rows.some(item => item.audience !== doc.audience) || new Set(rows.map(item => item.id)).size !== rows.length) {
    ctx.addIssue({ code: 'custom', message: '经历的来源范围或编号不一致。' })
  }
  for (const habit of doc.habits) {
    const sessions = habit.evidence.map(id => entries.get(id)?.source.sessionId)
    if (sessions.some(id => !id) || new Set(sessions).size < 2) ctx.addIssue({ code: 'custom', message: '相处习惯缺少对应的经历。' })
  }
})
