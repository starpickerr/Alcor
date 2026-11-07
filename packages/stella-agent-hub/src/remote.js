import { z } from 'zod'
import { createTypertRemoteInfo } from '@deepseek-ai/dsh-typert-protocol/remote-info'
const text = z.string().trim().min(1).max(160)
const id = z.object({ id: z.string().min(1).max(200) }).strict()
export const hubAgents = z.array(z.object({ name: z.string().trim().min(1).max(80), task: z.string().trim().min(1).max(1000) }).strict()).min(1).max(3)
export const hubActivitySchema = z.array(z.object({ id: z.string(), kind: z.enum(['created', 'started', 'user', 'reply', 'status', 'configuration']), body: z.string(), createdAt: z.string().optional(), senderId: z.string().optional(), recipientIds: z.array(z.string()).optional(), stepId: z.string().optional(), executionId: z.string().optional(), sessionId: z.string().optional(), status: z.string().optional() }).strict())
const collaboration = z.object({ participants: z.array(z.object({ id: z.string(), name: z.string() }).strict()), steps: z.array(z.object({
  stepId: z.string(), title: z.string(), participantId: z.string(), dependsOn: z.array(z.string()), status: z.string(), output: z.string(),
  attempts: z.array(z.object({ executionId: z.string(), status: z.string(), createdAt: z.string(), childId: z.string().optional(), model: z.string().optional(), diagnostic: z.string().optional() }).strict()),
}).strict()) }).strict()
export const jobSchema = z.object({ id: z.string().min(1).max(200), title: text, prompt: z.string().min(1).max(16000), parentSessionId: z.string(), childId: z.string().optional(), createdAt: z.iso.datetime(), status: z.enum(['starting', 'running', 'completed', 'cancelled', 'failed', 'interrupted']), output: z.string().max(100000), diagnostic: z.string().max(4096), collaboration: collaboration.optional() }).strict()

export const schemas = {
  read: [z.object({}).strict(), z.object({ jobs: z.array(jobSchema) }).strict()],
  startJob: [z.object({ id: z.uuid(), parentSessionId: z.string().min(1).max(200), title: text, prompt: z.string().trim().min(1).max(12000), agents: hubAgents.optional() }).strict(), jobSchema],
  retryJob: [z.object({ id: z.string().startsWith('hub-').max(200), stepId: z.string().min(1).max(80), parentSessionId: z.string().min(1).max(200) }).strict(), jobSchema],
  hubActivity: [id, hubActivitySchema],
  hubSend: [z.object({ id: z.string().startsWith('hub-').max(200), requestId: z.uuid(), body: z.string().trim().min(1).max(16000), intent: z.enum(['message', 'task', 'persona']), recipientIds: z.array(z.string().min(1).max(100)).max(3), parentSessionId: z.string().min(1).max(200).optional() }).strict().superRefine((value, ctx) => {
    if (new Set(value.recipientIds).size !== value.recipientIds.length) ctx.addIssue({ code: 'custom', message: '收件人不能重复。' })
    if (value.intent !== 'message' && !value.recipientIds.length) ctx.addIssue({ code: 'custom', message: '请先 @ 一个助手。' })
    if (value.intent === 'persona' && (value.recipientIds.length !== 1 || value.body.length > 8000)) ctx.addIssue({ code: 'custom', message: '人设需选择一位助手，最多 8000 字符。' })
    if (value.intent === 'task' && (value.body.length > 1000 || !value.parentSessionId)) ctx.addIssue({ code: 'custom', message: '分配任务最多 1000 字符，且需当前开发会话。' })
  }), z.object({ id: z.string(), intent: z.string() }).strict()],
  cancelJob: [id, jobSchema],
}
export const remote = createTypertRemoteInfo({ package: '@stella/agent-hub', methods: Object.fromEntries(Object.entries(schemas).map(([name, [request, result]]) => [`agentHub/${name}`, {service: 'agentHub', invocation: {kind: 'direct'}, parameters: [{name:'request',source:'json',codec:{mode:'strict',typeSymbol:`@stella/agent-hub#${name}Request`,create:()=>request}}],result:{mode:'strict',typeSymbol:`@stella/agent-hub#${name}Result`,create:()=>result}}])) }, z)
