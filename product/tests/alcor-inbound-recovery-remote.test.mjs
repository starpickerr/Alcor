import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { schemas } from '../../packages/alcor-organizer/src/remote.js'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

test('removed sync recovery commands are unavailable to the desktop and worker', async t => {
  const data = await mkdtemp(join(tmpdir(), 'alcor-retired-recovery-'))
  const worker = new BusinessWorker(data, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  t.after(async () => { await worker.close(); await rm(data, { recursive: true, force: true }) })
  for (const [remote, action] of [['listInboundRecoveries', 'list_inbound_recoveries'], ['restoreInboundRecovery', 'restore_inbound_recovery']]) {
    assert.equal(Object.hasOwn(schemas, remote), false)
    await assert.rejects(worker.call(action, { domain: 'tasks' }), /Unknown business action/)
  }
})
