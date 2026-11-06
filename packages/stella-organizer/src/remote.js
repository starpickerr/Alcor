import { z } from 'zod'
import { automationRequest } from './automation.js'
import { createTypertRemoteInfo } from '@deepseek-ai/dsh-typert-protocol/remote-info'
import { recordSchema, writeRequest, archiveRequest, date, text } from './records.js'

const id = z.object({ id: z.string().min(1).max(200) }).strict()
export const jobSchema = z.object({ id: z.string().min(1).max(200), title: text, prompt: z.string().min(1).max(16000), parentSessionId: z.string(), childId: z.string().optional(), createdAt: z.iso.datetime(), status: z.enum(['starting', 'running', 'completed', 'cancelled', 'failed', 'interrupted']), output: z.string().max(100000), diagnostic: z.string().max(4096) }).strict()
export const nativeScheduleSchema = z.object({ id: z.string(), title: z.string(), prompt: z.string(), status: z.string(), scheduledAt: z.string(), updatedAt: z.string().nullable(), targetSessionId: z.string().nullable(), deliveredAt: z.string().nullable() }).strict()
const briefingSources = z.array(z.union([
  z.object({ source: z.enum(['calendar_agenda', 'stella_tasks', 'stella_bills']), accountId: z.literal('local'), label: z.string().max(200).optional() }).strict(),
  z.object({ source: z.enum(['email', 'elearning']), accountId: z.string().min(1).max(200), label: z.string().max(200).optional(), tool: z.string().regex(/^mcp__[A-Za-z0-9_-]+__(fetch_messages|fetch_source|fetch)$/), method: z.enum(['fetch_messages', 'fetch_source', 'fetch']), arguments: z.record(z.string(), z.json()).default({}) }).strict().refine(value => value.tool.endsWith('__' + value.method)),
  z.object({ source: z.string().regex(/^plugin:[A-Za-z0-9_-]{1,64}:[A-Za-z0-9._:-]{1,80}$/), accountId: z.string().min(1).max(200), label: z.string().max(200).optional(), tool: z.string().regex(/^[A-Za-z0-9_-]{1,64}_briefing_fetch$/), method: z.literal('briefing_fetch'), arguments: z.object({ source_id: z.string().regex(/^[A-Za-z0-9._:-]{1,80}$/) }).strict() }).strict().refine(value => value.source === `plugin:${value.tool.slice(0, -'_briefing_fetch'.length)}:${value.arguments.source_id}`),
])).max(20)

const briefingRequest = z.object({ date, notes: z.string().max(12000), requestId: z.uuid(), parentSessionId: z.string().min(1).max(200).optional(), workspaceId: z.string().min(1).max(200).optional(), sources: briefingSources.optional() }).strict()
export const briefingRunSchema = z.object({ requestId: z.uuid(), date, phase: z.enum(['starting', 'collecting', 'model', 'completed', 'cancelled', 'failed', 'interrupted']), message: z.string(), sessionId: z.string().optional(), model: z.object({ provider: z.string(), model: z.string(), reasoningEffort: z.string().optional() }).strict().optional(), createdAt: z.string(), updatedAt: z.string(), briefingId: z.string().optional(), completedSources: z.number().int().nonnegative(), completedBatches: z.number().int().nonnegative() }).strict()

export const schemas = {
  briefingSources,
  sourceGroups: z.record(z.string().regex(/^(email|elearning|calendar_agenda|stella_tasks|stella_bills|plugin:[A-Za-z0-9_-]{1,64})$/), z.boolean()).refine(value => Object.keys(value).length <= 200),
  business: [z.object({ domain: z.enum(['tasks', 'calendar', 'bills', 'briefing', 'agent_hub', 'apple', 'automation']), method: z.string().min(1).max(80), arguments: z.record(z.string(), z.json()).default({}), requestId: z.string().min(1).max(200).optional() }).strict(), z.json()],
  automation: [automationRequest, z.json()],
  read: [z.object({}).strict(), z.object({ records: z.array(recordSchema), jobs: z.array(jobSchema), schedules: z.array(nativeScheduleSchema) }).strict()],
  write: [writeRequest, recordSchema],
  archive: [archiveRequest, recordSchema],
  briefing: [z.object({ date, notes: z.string().max(12000), requestId: z.uuid() }).strict(), recordSchema],
  generateBriefing: [briefingRequest, recordSchema],
  startBriefing: [briefingRequest, briefingRunSchema],
  resumeBriefing: [z.object({ requestId: z.uuid() }).strict(), briefingRunSchema],
  startJob: [z.object({ id: z.uuid(), parentSessionId: z.string().min(1).max(200), title: text, prompt: z.string().trim().min(1).max(12000) }).strict(), jobSchema],
  cancelJob: [id, jobSchema],
  createSchedule: [z.object({ title: text.max(120), prompt: z.string().trim().min(1).max(12000), timing: z.discriminatedUnion('kind', [z.object({ kind: z.literal('at'), at: z.string().datetime() }).strict(), z.object({ kind: z.literal('daily'), time: z.string().regex(/^\d{2}:\d{2}$/), timeZone: z.string().min(1).max(100) }).strict()]) }).strict(), nativeScheduleSchema],
  editSchedule: [z.object({ id: z.string().min(1), action: z.enum(['pause', 'resume', 'run', 'delete']), expectedUpdatedAt: z.string().nullable() }).strict(), z.boolean()],
}
export const remote = createTypertRemoteInfo({ package: '@stella/organizer', methods: Object.fromEntries(Object.entries(schemas).filter(([, value]) => Array.isArray(value)).map(([name, [request, result]]) => [`stellaOrganizer/${name}`, {
  service: 'stellaOrganizer', invocation: { kind: 'direct' },
  parameters: [{ name: 'request', source: 'json', codec: { mode: 'strict', typeSymbol: `@stella/organizer#${name}Request`, create: () => request } }],
  result: { mode: 'strict', typeSymbol: `@stella/organizer#${name}Result`, create: () => result },
}])) }, z)
