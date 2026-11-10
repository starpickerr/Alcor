import { channelRouteSchema } from './registry.js'
import { z } from 'zod'
const grantSchema=z.object({route:channelRouteSchema,nonce:z.string(),until:z.number().nullable()}).strict()
const receiptSchema=z.object({
  id:z.string(),scope:z.string(),content:z.string().max(240),route:channelRouteSchema,grant:z.string(),status:z.enum(['pending','sending','sent','unknown','cancelled','expired','desktop']),
  updatedAt:z.number(),messageId:z.string().optional(),
}).strict()
export const deliveryStateSchema=z.object({grants:z.array(grantSchema),receipts:z.array(receiptSchema),
  job:z.object({id:z.string(),prompt:z.string(),sessionId:z.string()}).nullable()}).strict()
