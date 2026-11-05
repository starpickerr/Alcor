import { z } from 'zod'
import { createTypertRemoteInfo } from '@deepseek-ai/dsh-typert-protocol/remote-info'

export const requestSchema = z.object({ method: z.string().min(1).max(80), arguments: z.record(z.string(), z.json()).default({}) }).strict()
export const remote = createTypertRemoteInfo({ package: '@stella/apple-sync', methods: {
  'stellaAppleSync/invoke': { service: 'stellaAppleSync', invocation: { kind: 'direct' },
    parameters: [{ name: 'request', source: 'json', codec: { mode: 'strict', typeSymbol: '@stella/apple-sync#request', create: () => requestSchema } }],
    result: { mode: 'strict', typeSymbol: '@stella/apple-sync#result', create: () => z.json() },
  },
} }, z)
