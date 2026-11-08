import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'

test('scheduled source selection and repeated authorization use only local settings', async t => {
  const output = await mkdtemp(join(process.cwd(), '.alcor-briefing-build-'))
  t.after(() => rm(output, { recursive: true, force: true }))
  const file = join(output, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  const Organizer = (await import(pathToFileURL(file).href)).default
  const organizer = Object.create(Organizer.prototype), calls = []
  let groups = { alcor_tasks: true, alcor_bills: false }
  const preferences = [{source:'elearning',accountId:'school'}, ...[1,2,3].map(id=>({source:'email',accountId:`mail-${id}`}))]
  organizer.active = true
  organizer.ctx = { get: () => assert.fail('local selection must not discover external services'), tools: { definitions: () => assert.fail('local selection must not inspect external tools') } }
  organizer.businessWorker = { call: async action => { calls.push(action); if (action === 'source_settings') return preferences; if (action === 'source_group_settings') return groups; assert.fail(`unexpected worker action: ${action}`) } }
  const sources = await organizer.localBriefingSources()
  assert.deepEqual(sources.map(row => row.source), ['alcor_tasks'])
  const authorize = organizer.briefingAuthorization(sources)
  await authorize(); await authorize()
  assert.deepEqual(calls, ['source_settings','source_group_settings','source_settings','source_group_settings','source_settings','source_group_settings'])
  groups = { alcor_tasks: false, alcor_bills: false }
  await assert.rejects(authorize(), /来源已关闭/)
  assert.deepEqual(await organizer.localBriefingSources(), [])
})
