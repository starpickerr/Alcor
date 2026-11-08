import { z } from 'zod'
import { createTypertRemoteInfo } from '@deepseek-ai/dsh-typert-protocol/remote-info'

export const requestSchema = z.object({ method: z.string().min(1).max(80), arguments: z.record(z.string(), z.json()).default({}) }).strict()
export const itemRestoreSchema = z.object({ kind:z.enum(['calendar','reminders']), identity:z.string().min(1).max(500) }).strict()
export const boundConflictSchema = z.object({ kind:z.enum(['calendar','reminders']), identity:z.string().min(1).max(500), choice:z.enum(['keep_apple','keep_alcor']) }).strict()
export const rebuildPreviewSchema = z.object({ kind:z.enum(['calendar','reminders']), apple_id:z.string().min(1).max(500), source_id:z.string().min(1).max(500) }).strict()
export const rebuildCommitSchema = z.object({ ticket:z.string().uuid() }).strict()
export const remote = createTypertRemoteInfo({ package: '@alcor/apple-sync', methods: {
  'alcorAppleSync/invoke': { service: 'alcorAppleSync', invocation: { kind: 'direct' },
    parameters: [{ name: 'request', source: 'json', codec: { mode: 'strict', typeSymbol: '@alcor/apple-sync#request', create: () => requestSchema } }],
    result: { mode: 'strict', typeSymbol: '@alcor/apple-sync#result', create: () => z.json() },
  },
  'alcorAppleSync/resolveItemConflict': { service:'alcorAppleSync', invocation:{kind:'direct'},
    parameters:[{name:'request',source:'json',codec:{mode:'strict',typeSymbol:'@alcor/apple-sync#boundConflict',create:()=>boundConflictSchema}}],
    result:{mode:'strict',typeSymbol:'@alcor/apple-sync#result',create:()=>z.json()} },
  'alcorAppleSync/restoreLocalItem': { service:'alcorAppleSync', invocation:{kind:'direct'},
    parameters:[{name:'request',source:'json',codec:{mode:'strict',typeSymbol:'@alcor/apple-sync#itemRestore',create:()=>itemRestoreSchema}}],
    result:{mode:'strict',typeSymbol:'@alcor/apple-sync#result',create:()=>z.json()} },
  'alcorAppleSync/previewCategoryRebuild': { service:'alcorAppleSync', invocation:{kind:'direct'},
    parameters:[{name:'request',source:'json',codec:{mode:'strict',typeSymbol:'@alcor/apple-sync#rebuildPreview',create:()=>rebuildPreviewSchema}}],
    result:{mode:'strict',typeSymbol:'@alcor/apple-sync#result',create:()=>z.json()} },
  'alcorAppleSync/rebuildCategory': { service:'alcorAppleSync', invocation:{kind:'direct'},
    parameters:[{name:'request',source:'json',codec:{mode:'strict',typeSymbol:'@alcor/apple-sync#rebuildCommit',create:()=>rebuildCommitSchema}}],
    result:{mode:'strict',typeSymbol:'@alcor/apple-sync#result',create:()=>z.json()} },
  'alcorAppleSync/cancelCategoryRebuild': { service:'alcorAppleSync', invocation:{kind:'direct'},
    parameters:[{name:'request',source:'json',codec:{mode:'strict',typeSymbol:'@alcor/apple-sync#rebuildCommit',create:()=>rebuildCommitSchema}}],
    result:{mode:'strict',typeSymbol:'@alcor/apple-sync#result',create:()=>z.json()} },
} }, z)
