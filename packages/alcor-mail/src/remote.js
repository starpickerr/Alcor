import { checkMessages } from './check-status.js'
import { z } from 'zod'
import { createTypertRemoteInfo } from '@deepseek-ai/dsh-typert-protocol/remote-info'

const line = size => z.string().trim().min(1).max(size).regex(/^[^\r\n\0]+$/)
export const account = z.object({ id: z.string().regex(/^[A-Za-z0-9_-]{1,80}$/), host: line(253).regex(/^[A-Za-z0-9.:_-]+$/), user: line(320), password: z.string().min(1).max(4096).regex(/^[^\r\n\0]+$/), port: z.number().int().min(1).max(65535), folder: line(512), enabled: z.boolean() }).strict()
export const credential = z.object({ accounts: z.array(account).min(1).max(20) }).strict().refine(value => new Set(value.accounts.map(row => row.id)).size === value.accounts.length)
const revision = z.number().int().nonnegative()
const publicAccount = account.omit({ password: true })
export const stateSchema = z.object({ installed: z.boolean(), configured: z.boolean(), connected: z.boolean(), writable: z.boolean(), revision, accounts: z.array(publicAccount) }).strict()
export const schemas = {
  state: [z.object({}).strict(), stateSchema],
  save: [z.object({ expectedRevision: revision, account: account.omit({ id: true }).extend({ id: account.shape.id.optional(), password: z.string().max(4096).regex(/^[^\r\n\0]*$/) }) }).strict(), stateSchema],
  removeAccount: [z.object({ id: account.shape.id, expectedRevision: revision }).strict(), stateSchema],
  check: [z.object({ id: account.shape.id }).strict(), z.object({ status: z.enum(['ready', 'disconnected', 'query_failed']), code: z.enum(Object.keys(checkMessages)).optional() }).strict()],
}
export const remote = createTypertRemoteInfo({ package: '@alcor/mail', methods: Object.fromEntries(Object.entries(schemas).map(([name, [request, result]]) => [`alcorMail/${name}`, {
  service: 'alcorMail', invocation: { kind: 'direct' },
  parameters: [{ name: 'request', source: 'json', codec: { mode: 'strict', typeSymbol: `@alcor/mail#${name}Request`, create: () => request } }],
  result: { mode: 'strict', typeSymbol: `@alcor/mail#${name}Result`, create: () => result },
}])) }, z)
