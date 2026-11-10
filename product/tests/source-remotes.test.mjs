import test from 'node:test'
import assert from 'node:assert/strict'
import { Context } from '@deepseek-ai/cordis'
import Typert from '@deepseek-ai/dsh-typert-registry'
import * as Gateway from '@deepseek-ai/dsh-api-gateway/client'
import { remote as school } from '../../packages/alcor-elearning/src/remote.js'
import { remote as mail } from '../../packages/alcor-mail/src/remote.js'
import { remote as secretary } from '../../packages/alcor-secretary/src/remote.js'
import { remote as organizer } from '../../packages/alcor-organizer/src/remote.js'
import { remote as apple } from '../../packages/alcor-apple-sync/src/remote.js'
import { build } from 'esbuild'
import { mkdtemp, rm, mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { join } from 'node:path'

test('Stella plugin APIs mount on the real DSH client gateway without namespace conflicts', async t => {
  const ctx = new Context(); t.after(() => ctx.fiber.dispose())
  await ctx.plugin(Typert).await()
  ctx.provide('connection', { interactions: 'none', rpc: { open() {} } })
  await ctx.plugin(Gateway).await()
  for (const contribution of [school, mail, secretary, organizer, apple]) await ctx.remote.$mount(contribution)
  assert(ctx.get('remote.alcorSchool'))
  assert(ctx.get('remote.alcorMail'))
})

test('installing the school bundle preserves an existing manual connection and its owner', async t => {
  const local = fileURLToPath(new URL('../../.local/', import.meta.url)); await mkdir(local, { recursive: true })
  const folder = await mkdtemp(join(local, 'school-compat-'))
  t.after(() => rm(folder, { recursive: true, force: true }))
  await build({ entryPoints: [fileURLToPath(new URL('../../packages/alcor-elearning/src/index.ts', import.meta.url))], outfile: join(folder, 'index.mjs'), bundle: true, packages: 'external', platform: 'node', format: 'esm', target: 'node24' })
  const plugin = await import(pathToFileURL(join(folder, 'index.mjs')).href)
  const ctx = new Context(); t.after(() => ctx.fiber.dispose())
  const legacy = { state: async () => ({ installed: true, configured: false, connected: false, writable: true }) }
  const warnings = []
  ctx.provide('logger', { warn: value => warnings.push(value) })
  const home = join(folder, 'home'), dir = join(home, 'profiles', 'fixture')
  await mkdir(join(dir, 'alcor'), { recursive: true })
  await writeFile(join(dir, 'alcor', '.migration.json'), JSON.stringify({ schema: 1, status: 'complete', source: 'fresh', directories: { business: 'absent', appleSync: 'absent', email: 'absent', elearning: 'absent' }, domains: { organizer: 'absent', secretary: 'absent' }, credentials: { elearning: 'absent', email: 'absent' }, schedule: { mapped: 0 } }))
  ctx.provide('profileContext', { home, dir, startedBundles: ['@alcor/elearning'] })
  for (const name of ['credentials', 'authorization']) ctx.provide(name, {})
  ctx.provide('tools',{guard:()=>()=>{}})
  ctx.provide('alcorElearning', legacy)
  await ctx.plugin(Typert).await()
  const fiber = await ctx.plugin(plugin).await()
  assert.equal(ctx.get('alcorElearning'), legacy)
  assert(ctx.get('alcorSchool'))
  assert.deepEqual(await ctx.alcorSchool.elearningAccount({}), await legacy.state())
  await fiber.dispose()
  assert.equal(ctx.get('alcorElearning'), legacy)
})
