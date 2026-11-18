import { z } from 'zod'

export const logCapacity = 1000
export const logDebugCapacity = 100
export const logSources = ['Alcor', '事务', '日程', '简报', '账单', '自动化', 'Apple 同步', 'eLearning', '邮件', 'AgentHub']
export const logCategoryLabels = { success: '成功', failure: '失败', error: '错误', warn: '警告', info: '信息', debug: '调试' }
export const logCategories = Object.keys(logCategoryLabels)
export const logCategory = row => row.level === 'debug' ? 'debug' : row.outcome ?? row.level
export const logEntry = z.object({
  id: z.number().int().positive(), time: z.string().datetime(),
  level: z.enum(['error', 'warn', 'info', 'debug']),
  source: z.string().max(100), message: z.string().max(4000),
}).strict()
export const logHistory = z.object({ entries: z.array(logEntry).max(logCapacity) }).strict()
export const logViewEntry = logEntry.extend({
  outcome: z.enum(['success', 'failure']).nullable(), operation: z.string().max(180).nullable(),
  diagnostic: z.object({ code: z.string(), reason: z.string().nullable(), businessCode: z.string().nullable(), systemCode: z.string().nullable(), locations: z.array(z.string()).max(8) }).optional(),
  durationMs: z.number().int().nonnegative().nullable(), requestId: z.string().uuid().nullable(),
}).strict()
export const logSchemas = [
  z.object({ query: z.string().trim().max(200).default(''), level: z.enum(['normal', 'all', 'error', 'warn', 'info', 'debug']).default('all'),
    outcome: z.enum(['all', 'success', 'failure']).default('all'),
    category: z.enum(logCategories).optional(),
    source: z.string().max(100).default(''), before: z.number().int().positive().optional(),
    since: z.string().datetime().optional(),
  }).strict(),
  z.object({ entries: z.array(logViewEntry).max(100), sources: z.array(z.string()), matched: z.number().int().nonnegative(),
    nextBefore: z.number().int().positive().nullable(), retained: z.number().int().nonnegative(),
    capacity: z.number().int().positive(), startedAt: z.string().datetime(),
    storage: z.enum(['saved', 'pending', 'failed']),
  }).strict(),
]
