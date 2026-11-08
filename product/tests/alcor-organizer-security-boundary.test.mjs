import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

test('actual organizer queue rechecks authorization before real worker dispatch', async t => {
  const output = await mkdtemp(join(process.cwd(), '.alcor-isolated-build-'))
  const data = await mkdtemp(join(tmpdir(), 'alcor-organizer-boundary-'))
  const worker = new BusinessWorker(data, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  t.after(async () => { await worker.close(); await rm(output, { recursive: true, force: true }); await rm(data, { recursive: true, force: true }) })
  const file = join(output, 'organizer.mjs')
  await build({ entryPoints: ['packages/alcor-organizer/src/index.ts'], outfile: file, bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24', logLevel: 'silent' })
  const Organizer = (await import(pathToFileURL(file).href)).default
  const organizer = Object.create(Organizer.prototype)
  organizer.ctx = {}; organizer.active = true; organizer.businessWorker = worker
  let unblock; organizer.queue = new Promise(resolve => { unblock = resolve })
  const row = await worker.call('invoke', { domain: 'bills', method: 'create_ledger', arguments: { name: 'Synthetic', amount_minor: 123, currency: 'CNY', entry_type: 'expense', occurred_at: '2026-11-06T09:00:00Z', category: 'Test' } })
  let authorized = true
  const request = { id: row.id, revision: row.revision, snapshot: row, operation_id: 'c'.repeat(64) }
  const pending = organizer.approvedBillDelete(request, () => authorized)
  authorized = false; unblock()
  await assert.rejects(pending, /失效/)
  assert.equal((await worker.call('invoke', { domain: 'bills', method: 'get_ledger', arguments: { id: row.id } })).id, row.id)
  authorized = true
  assert.equal((await organizer.approvedBillDelete(request, () => authorized)).deleted, true)
  const replay = await organizer.approvedBillDelete(request, () => authorized)
  assert.equal(replay.replayed, true); assert.equal(replay.targetConflict, false)
})
