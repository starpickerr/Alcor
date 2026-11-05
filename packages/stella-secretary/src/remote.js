import { z } from 'zod'
import { createTypertRemoteInfo } from '@deepseek-ai/dsh-typert-protocol/remote-info'
import { sourceSchema, identityFileSchema, workspaceStateSchema } from './workspace-state.js'

export const overviewSchema = z.object({
  displayName: z.string(),
  installedAt: z.string().datetime(),
  message: z.string(),
}).strict()

const revision = z.number().int().nonnegative()
export const mcpToolName = z.string().regex(/^mcp__[A-Za-z0-9_-]{1,32}__[A-Za-z0-9_-]+$/).max(128)
export const mcpGrantSchema = z.object({ revision, allowedTools: z.array(mcpToolName).max(200) }).strict()
const elearningAccountState = z.object({ installed: z.boolean(), configured: z.boolean(), connected: z.boolean(), writable: z.boolean(), username: z.string().max(160).optional() }).strict()
export const workspaceSchemas = {
  elearningAccount: [z.object({}).strict(), elearningAccountState],
  saveElearningAccount: [z.object({ username: z.string().trim().min(1).max(160), password: z.string().min(1).max(4096) }).strict(), elearningAccountState],
  removeElearningAccount: [z.object({}).strict(), elearningAccountState],
  checkElearning: [z.object({}).strict(), z.object({ status: z.enum(['ready', 'disconnected', 'login_required', 'query_failed']), courseCount: z.number().int().nonnegative().optional() }).strict()],
  mcpState: [z.object({}).strict(), mcpGrantSchema.extend({ availableTools: z.array(mcpToolName) }).strict()],
  setMcpTools: [z.object({ expectedRevision: revision, allowedTools: z.array(mcpToolName).max(200) }).strict(), mcpGrantSchema],
  workspaceState: [z.object({}).strict(), workspaceStateSchema],
  previewWorkspace: [sourceSchema, z.object({ source: sourceSchema, fingerprint: z.string(), files: z.array(identityFileSchema), inventory: z.array(z.object({ role: z.string(), name: z.string(), present: z.boolean(), kind: z.enum(['file', 'directory']) }).strict()) }).strict()],
  importWorkspace: [z.object({ source: sourceSchema, fingerprint: z.string().regex(/^[a-f0-9]{64}$/), names: z.array(z.enum(['AGENT.md', 'USER.md', 'RULE.md'])).min(1).max(3), expectedRevision: revision }).strict(), workspaceStateSchema],
  restoreIdentity: [z.object({ expectedRevision: revision }).strict(), workspaceStateSchema],
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
    ...Object.fromEntries(Object.entries(workspaceSchemas).map(([name, [request, result]]) => [`stellaSecretary/${name}`, {
      service: 'stellaSecretary', invocation: { kind: 'direct' },
      parameters: [{ name: 'request', source: 'json', codec: { mode: 'strict', typeSymbol: `@stella/secretary#${name}Request`, create: () => request } }],
      result: { mode: 'strict', typeSymbol: `@stella/secretary#${name}Result`, create: () => result },
    }])),
  },
}, z)
