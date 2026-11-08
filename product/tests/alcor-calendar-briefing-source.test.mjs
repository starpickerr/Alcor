import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

test('new calendar briefing payload uses Alcor identity without altering saved events', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'alcor-calendar-source-'))
  const worker = new BusinessWorker(dir, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  t.after(async () => { await worker.close(); await rm(dir, { recursive: true, force: true }) })
  const row = await worker.call('invoke', { domain: 'calendar', method: 'create_event', arguments: { title: 'Synthetic event', start_at: '2026-11-01T09:00:00+00:00', end_at: '2026-11-01T10:00:00+00:00' } })
  const source = await worker.call('calendar_agenda', { run_at: '2026-11-01T00:00:00+00:00' })
  const item = source.items.find(item => item.source_id === row.id)
  assert(item); assert.equal(item.sender, 'Alcor 日程'); assert.equal(item.payload.alcor_calendar_event_id, row.id)
  assert.equal('stella_calendar_event_id' in item.payload, false)
  const saved = await worker.call('invoke', { domain: 'calendar', method: 'get_event', arguments: { id: row.id } })
  assert.equal(saved.title, row.title)
})
