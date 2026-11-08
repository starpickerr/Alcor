import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

test('first collection starts at previous local midnight even across a DST transition', async t => {
  const directory = await mkdtemp(join(tmpdir(),'alcor-briefing-window-'))
  const worker = new BusinessWorker(directory,{ worker:new URL('../../packages/alcor-secretary/business/worker.py',import.meta.url) })
  t.after(async () => {await worker.close(); await rm(directory,{recursive:true,force:true})})
  const [start] = await worker.call('source_windows',{date:'2026-11-02', run_at:'2026-11-02T13:00:00Z', sources:[{source:'email',accountId:'a'}], time_zone:'America/New_York'})
  assert.equal(Date.parse(start),Date.parse('2026-11-01T04:00:00Z'))
})

test('SQLite daily reports, checkpoint recovery, late arrival, boundaries and incremental thread merge', async () => {
  const {execFileSync} = await import('node:child_process')
  execFileSync('python3',['-B',new URL('./briefing-incremental.py',import.meta.url).pathname],{encoding:'utf8'})
})
