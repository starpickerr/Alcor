import { channelRouteSchema, channelMessageSchema } from './channels/registry.js'
import { deliveryStateSchema } from './channels/schema.js'
import { experiences, memoryAudience } from './memory/experience-schema.js'
import { publicIdentity, personaExamples } from './memory/identity-schema.js'
import { checkMessages } from './elearning-status.js'
import { z } from 'zod'
import { createTypertRemoteInfo } from '@deepseek-ai/dsh-typert-protocol/remote-info'

export const overviewSchema = z.object({
  displayName: z.string(),
  installedAt: z.string().datetime(),
  message: z.string(),
}).strict()

const elearningAccountState = z.object({ installed: z.boolean(), configured: z.boolean(), connected: z.boolean(), writable: z.boolean(), username: z.string().max(160).optional() }).strict()
const space = z.enum(['alcor', 'dsh'])
const identifier = z.string().min(1).max(200)
export const ownershipSchema = z.object({ projects: z.record(identifier, space), projectViews: z.record(identifier,z.object({alcor:z.string().optional(),dsh:z.string().optional()}).strict()).optional(), sessions: z.record(identifier, space), qq: z.record(identifier, z.string().max(128)).optional() }).strict()
export const spacesSchema = ownershipSchema.extend({ defaults: z.object({ alcor: identifier, dsh: identifier }).strict(),channelRoutes:z.array(channelRouteSchema.extend({available:z.boolean(),channelName:z.string()})).optional() }).strict()
const memoryEntry = z.object({ id: identifier, content: z.string(), subject: z.string(), audience: z.string(), kind: z.string(), evidence: z.array(identifier).max(5).optional(),
  source: z.object({ kind: z.string(), at: z.string(), sessionId: z.string().optional(), channelId:z.string().optional(),accountId:z.string().optional(),channelUserId:z.string().optional(),qqUserId: z.string().optional(), qqGroupId: z.string().optional() }).strict() }).strict()
export const accountSchemas = {
  qqConversation: [z.object({sessionId:identifier}).strict(), z.object({
    online:z.boolean(), response:z.enum(['disabled','private','mention','ambient','addressed']),
    queue:z.enum(['queue','steer']), interim:z.boolean(), limit:z.number(), admin:z.boolean().optional(),
    name:z.string().max(128).optional(), members:z.number().int().nonnegative().optional(),
  }).strict().nullable()],
  channelCatalog:[z.object({}).strict(),z.array(channelRouteSchema.extend({available:z.boolean(),channelName:z.string()}))],
  channelConversation:[z.object({sessionId:identifier}).strict(),z.object({
    online:z.boolean(),response:z.enum(['disabled','private','mention','ambient','addressed']),queue:z.enum(['queue','steer']),
    interim:z.boolean(),limit:z.number(),admin:z.boolean().optional(),name:z.string().max(128).optional(),members:z.number().int().nonnegative().optional(),
  }).strict().nullable()],
  channelHistory:[z.object({sessionId:identifier,beforeSeq:z.number().int().optional()}).strict(),
    z.object({messages:z.array(channelMessageSchema),
      hasMore:z.boolean(),beforeSeq:z.number().nullable()}).strict()],
  deliverySettings:[z.object({}).strict(),z.object({targets:z.array(z.object({route:channelRouteSchema.extend({available:z.boolean(),channelName:z.string()}),until:z.number().nullable(),available:z.boolean()})),
    receipts:deliveryStateSchema.shape.receipts})],
  allowChannelDelivery:[z.object({sessionId:identifier,enabled:z.boolean()}).strict(),z.object({ok:z.literal(true)}).strict()],
  searchSpace: [z.object({ space, query: z.string().trim().min(1).max(1000), sessionIds: z.array(identifier).max(10000) }).strict(),
    z.object({ items: z.array(z.object({ sessionId: identifier, snippet: z.string() }).strict()).max(20), hasMore: z.boolean() }).strict()],
  memoryState: [z.object({}).strict(), z.object({ initiative: z.boolean(), desktopDelivery:z.boolean(), soul: z.string(), entries: z.array(memoryEntry).max(200) }).strict()],
  allowInitiative: [z.object({ enabled: z.boolean() }).strict(), z.object({ ok: z.literal(true) }).strict()],
  allowDesktopDelivery: [z.object({enabled:z.boolean()}).strict(),z.object({ok:z.literal(true)}).strict()],
  nextFollowup: [z.object({}).strict(), z.object({ id: identifier, content: z.string(), evidence: identifier, due: z.number() }).strict().nullable()],
  dismissFollowup: [z.object({ id: identifier }).strict(), z.object({ ok: z.literal(true) }).strict()],
  experienceScopes: [z.object({}).strict(), z.array(memoryAudience)],
  exportExperiences: [z.object({ audience: memoryAudience }).strict(), experiences],
  restoreExperiences: [z.object({ document: experiences }).strict(), z.object({ ok: z.literal(true) }).strict()],
  exportIdentity: [z.object({}).strict(), publicIdentity],
  importIdentity: [z.object({ document: publicIdentity, expectedRevision: z.string().uuid() }).strict(), z.object({ ok: z.literal(true) }).strict()],
  updateSoul: [z.object({ name: z.string().trim().min(1).max(80).optional(), expectedRevision: z.string().uuid().optional(), soul: z.string().trim().min(1).max(12000), examples: personaExamples.optional() }).strict(), z.object({ ok: z.literal(true) }).strict()],
  changeMemory: [z.object({ id: identifier, content: z.string().trim().min(1).max(1000).optional() }).strict(), z.object({ ok: z.literal(true), id: identifier }).strict()],
  spaces: [z.object({}).strict(), spacesSchema],
  editProject: [z.object({space,workspaceId:identifier,action:z.enum(['create','rename','remove']),title:z.string().trim().min(1).max(500).optional()}).strict(),spacesSchema],
  claimSpace: [z.object({ space, workspaceId: identifier.optional(), sessionId: identifier.optional() }).strict()
    .refine(value => !!value.workspaceId !== !!value.sessionId, 'Specify one workspace or session'), spacesSchema],
  elearningAccount: [z.object({}).strict(), elearningAccountState],
  saveElearningAccount: [z.object({ username: z.string().trim().min(1).max(160), password: z.string().min(1).max(4096) }).strict(), elearningAccountState],
  removeElearningAccount: [z.object({}).strict(), elearningAccountState],
  checkElearning: [z.object({}).strict(), z.object({ status: z.enum(['ready', 'disconnected', 'login_required', 'query_failed']), courseCount: z.number().int().nonnegative().optional(), code: z.enum(Object.keys(checkMessages)).optional() }).strict()],
}

export const remote = createTypertRemoteInfo({
  package: '@alcor/secretary',
  methods: {
    'alcorSecretary/overview': {
      service: 'alcorSecretary',
      invocation: { kind: 'direct' },
      parameters: [],
      result: { mode: 'strict', typeSymbol: '@alcor/secretary#overview', create: () => overviewSchema },
    },
    ...Object.fromEntries(Object.entries(accountSchemas).map(([name, [request, result]]) => [`alcorSecretary/${name}`, {
      service: 'alcorSecretary', invocation: { kind: 'direct' },
      parameters: [{ name: 'request', source: 'json', codec: { mode: 'strict', typeSymbol: `@alcor/secretary#${name}Request`, create: () => request } }],
      result: { mode: 'strict', typeSymbol: `@alcor/secretary#${name}Result`, create: () => result },
    }])),
  },
}, z)
