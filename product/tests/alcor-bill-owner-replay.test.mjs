import { python as testPython } from './platform-python.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'

test('real bill owner enforces snapshot CAS and idempotent receipt; public invoke remains denied', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'alcor-bill-cas-'))
  const worker = new BusinessWorker(dir, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  t.after(async () => { await worker.close(); await rm(dir, { recursive: true, force: true }) })
  const invoke = (method, args) => worker.call('invoke', { domain: 'bills', method, arguments: args })
  const row = await invoke('create_ledger', { name: 'Synthetic', amount_minor: 123, currency: 'CNY', entry_type: 'expense', occurred_at: '2026-11-06T09:00:00Z', category: 'Test' })
  await assert.rejects(invoke('delete_ledger', { id: row.id, expectedRevision: row.revision }), /人工确认/)
  const changed = await invoke('update_ledger', { id: row.id, patch: { name: 'Ordinary update' }, expectedRevision: row.revision })
  await assert.rejects(worker.call('commit_bill_delete', { entry_id: row.id, operation_id: 'a'.repeat(64), revision: row.revision, snapshot: row }), /变化/)
  const input = { entry_id: row.id, operation_id: 'a'.repeat(64), revision: changed.revision, snapshot: changed }
  assert.equal((await worker.call('commit_bill_delete', input)).deleted, true)
  assert.equal((await worker.call('commit_bill_delete', input)).replayed, true)
  assert.equal((await worker.call('verify_bill_delete_replay', { entry_id: row.id, operation_id: input.operation_id, expected_revision: changed.revision })).replayed, true)
  await assert.rejects(worker.call('verify_bill_delete_replay', { entry_id: row.id, operation_id: input.operation_id, expected_revision: row.revision }), /conflict/)
})

test('owner rolls deletion, reports and receipt back together on either failure', async () => {
  const { execFileSync } = await import('node:child_process')
  execFileSync(testPython, ['-B', '-c', `
import sys,tempfile
from pathlib import Path
sys.path.insert(0,'packages/alcor-secretary')
from business.bills.domain.store import BillStore
from business.bills.domain.service import BillsService
for failing in ('_refresh_periods','_save_receipt'):
 with tempfile.TemporaryDirectory(prefix='bill-atomic-') as tmp:
  store=BillStore(Path(tmp)/'bills.sqlite3'); svc=BillsService(store)
  row=svc.create_ledger({'name':'Synthetic','amount_minor':123,'currency':'CNY','entry_type':'expense','occurred_at':'2026-11-06T09:00:00Z','category':'Test'})
  with store._conn() as db: before=[tuple(r) for r in db.execute('SELECT * FROM period_statements ORDER BY id')]
  original=getattr(svc,failing)
  def fail(*a,**kw): raise RuntimeError('synthetic failure')
  setattr(svc,failing,fail)
  try: svc.commit_approved_ledger_delete(row['id'],'a'*64,row['revision'],row)
  except RuntimeError: pass
  else: raise AssertionError('expected failure')
  setattr(svc,failing,original)
  assert svc.get_ledger(row['id']) == row
  assert svc.get_operation('a'*64) is None
  with store._conn() as db: assert [tuple(r) for r in db.execute('SELECT * FROM period_statements ORDER BY id')] == before
`])
})
