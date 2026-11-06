import { z } from 'zod'
import { createTypertRemoteInfo } from '@deepseek-ai/dsh-typert-protocol/remote-info'

export const overviewSchema = z.object({
  displayName: z.string(),
  installedAt: z.string().datetime(),
  message: z.string(),
}).strict()

const elearningAccountState = z.object({ installed: z.boolean(), configured: z.boolean(), connected: z.boolean(), writable: z.boolean(), username: z.string().max(160).optional() }).strict()
export const accountSchemas = {
  elearningAccount: [z.object({}).strict(), elearningAccountState],
  saveElearningAccount: [z.object({ username: z.string().trim().min(1).max(160), password: z.string().min(1).max(4096) }).strict(), elearningAccountState],
  removeElearningAccount: [z.object({}).strict(), elearningAccountState],
  checkElearning: [z.object({}).strict(), z.object({ status: z.enum(['ready', 'disconnected', 'login_required', 'query_failed']), courseCount: z.number().int().nonnegative().optional() }).strict()],
}

export const remote = createTypertRemoteInfo({
  package: '@stella/secretary',
  methods: {
    'stellaSecretary/overview': {
      service: 'stellaSecretary',
      invocation: { kind: 'direct' },
      parameters: [],
      result: { mode: 'strict', typeSymbol: '@stella/secretary#overview', create: () => overviewSchema },
    },
    ...Object.fromEntries(Object.entries(accountSchemas).map(([name, [request, result]]) => [`stellaSecretary/${name}`, {
      service: 'stellaSecretary', invocation: { kind: 'direct' },
      parameters: [{ name: 'request', source: 'json', codec: { mode: 'strict', typeSymbol: `@stella/secretary#${name}Request`, create: () => request } }],
      result: { mode: 'strict', typeSymbol: `@stella/secretary#${name}Result`, create: () => result },
    }])),
  },
}, z)
