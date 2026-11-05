import { z } from 'zod'

export const identityNames = ['AGENT.md', 'USER.md', 'RULE.md']
export const sourceSchema = z.object(Object.fromEntries(['application', 'agent', 'user', 'shared'].map(name => [name, z.string().min(1).max(4096)]))).strict()
export const identityFileSchema = z.object({ name: z.enum(identityNames), content: z.string().max(65536) }).strict()
const identitySchema = z.object({ source: sourceSchema, fingerprint: z.string(), importedAt: z.iso.datetime(), files: z.array(identityFileSchema).max(3) }).strict()
export const workspaceStateSchema = z.object({
  revision: z.number().int().nonnegative(),
  // Read older previews without treating their retired bindings as workspace selection.
  personalWorkspaceId: z.string().nullable(),
  projectWorkspaceId: z.string().nullable(),
  identity: identitySchema.nullable(),
  previousIdentity: identitySchema.nullable(),
  canRestoreIdentity: z.boolean(),
}).strict()
export const initialWorkspaceState = { revision: 0, personalWorkspaceId: null, projectWorkspaceId: null, identity: null, previousIdentity: null, canRestoreIdentity: false }
