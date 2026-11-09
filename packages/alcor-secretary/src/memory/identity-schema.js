import { z } from 'zod'

// This file contains public personality only. Memory scopes and grants stay on their device.
export const publicIdentity = z.object({
  format: z.literal('alcor.identity.v1'),
  id: z.string().uuid(),
  revision: z.string().uuid(),
  ancestors: z.array(z.string().uuid()).max(256),
  name: z.string().trim().min(1).max(80),
  soul: z.string().trim().min(1).max(12000),
}).strict()

export const reflectDescription = '从不同会话的两至五条经历形成自己的相处习惯。evidence 填写每条记忆的原始 ID（历史资料括号中的 ID，或 alcor_memory_recall 返回的 id）。content 用一句话描述习惯。'
