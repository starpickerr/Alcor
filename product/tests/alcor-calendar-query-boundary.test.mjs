import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'

test('calendar query is separately callable while existing business flow refreshes first', async t => {
  const output = await mkdtemp(join(process.cwd(), '.alcor-query-build-'))
  t.after(() => rm(output, { recursive: true, force: true }))
  const file = join(output, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  const Organizer = (await import(pathToFileURL(file).href)).default
  const organizer = Object.create(Organizer.prototype), calls = []
  let failure = false
  organizer.active = true; organizer.queue = Promise.resolve()
  organizer.ctx = { get: name => name === 'alcorAppleSync' ? { invoke: async request => { calls.push(['refresh', request]); if (failure) throw new Error('Synthetic offline'); return {} } } : undefined }
  organizer.businessWorker = { call: async (action, input) => { calls.push(['query', action, input]); return [{ id: 'synthetic' }] } }
  const input = { domain: 'calendar', method: 'list_events', arguments: { start: '2026-11-01', end: '2026-11-02' }, requestId: 'synthetic-id' }
  assert.deepEqual(await organizer.queryCalendarRange(input), [{ id: 'synthetic' }]); assert.deepEqual(calls.map(row => row[0]), ['query'])
  calls.length = 0; assert.deepEqual(await organizer.business(input), [{ id: 'synthetic' }]); assert.deepEqual(calls.map(row => row[0]), ['refresh', 'query'])
  calls.length = 0; failure = true; assert.deepEqual(await organizer.business(input), [{ id: 'synthetic' }]); assert.deepEqual(calls.map(row => row[0]), ['refresh', 'query'])
})
