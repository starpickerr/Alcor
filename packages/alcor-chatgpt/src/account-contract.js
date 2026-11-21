import { z } from 'zod'
import { createTypertRemoteInfo } from '@deepseek-ai/dsh-typert-protocol/remote-info'

const empty = z.object({}).strict()
const id = z.string().uuid()
const state = z.object({ accounts: z.array(z.object({ id, label: z.string(), connected: z.boolean(), canSignOut: z.boolean().optional() }).strict()),
  activeId: id.nullable(), pending: z.boolean(), notice: z.string() }).strict()
const account = z.object({ accountId: id }).strict()
export const methods = {
  accountState: [empty, state],
  beginLogin: [account.partial(), z.object({ browserUrl: z.string().regex(/^http:\/\/127\.0\.0\.1:\d+\/start\/[A-Za-z0-9_-]+$/) }).strict()],
  cancelLogin: [empty, state],
  signOut: [account, state],
}
export const remote = createTypertRemoteInfo({ package: '@alcor/chatgpt', methods: Object.fromEntries(
  Object.entries(methods).map(([name, [request, result]]) => [`alcorChatgpt/${name}`, {
    service: 'alcorChatgpt', invocation: { kind: 'direct' },
    parameters: [{ name: 'request', source: 'json', codec: { mode: 'strict', typeSymbol: `@alcor/chatgpt#${name}Request`, create: () => request } }],
    result: { mode: 'strict', typeSymbol: `@alcor/chatgpt#${name}Result`, create: () => result },
  }])) }, z)
