import test from 'node:test'
import { execFileSync } from 'node:child_process'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BusinessWorker } from '../../packages/stella-organizer/src/business.js'

test('bill files preview without writes and commit once across duplicate files and restart', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'stella-bill-import-'))
  const options = { worker: new URL('../../packages/stella-secretary/business/worker.py', import.meta.url) }
  let worker = new BusinessWorker(dir, options)
  t.after(async () => { await worker.close(); await rm(dir, { recursive: true, force: true }) })
  const call = (method, args = {}) => worker.call('invoke', { domain: 'bills', method, arguments: args })
  const content = '支付宝交易明细\n交易时间,交易分类,交易对方,商品说明,收/支,金额,交易状态,交易订单号,备注\n2026-11-01 12:30:00,餐饮,商家,午餐,支出,12.34,交易成功,12345678901234567890,\n2026-11-02 08:00:00,工资,公司,工资,收入,2000.00,交易成功,222,\n2026-11-02 09:00:00,转账,自己,转账,不计收支,100,交易成功,333,\n2026-11-02 09:00:00,餐饮,商家,退款,支出,5,退款成功,444,\n'
  const file = { name: '支付宝.csv', content: Buffer.from(content).toString('base64') }
  const preview = await call('import_files', { files: [file], dry_run: true })
  assert.equal(preview.ready, 2)
  assert.equal(preview.skipped, 2)
  assert.equal((await call('list_ledger', { year: 2026, month: 11 })).length, 0)
  const result = await call('import_files', { files: [file] })
  assert.equal(result.imported, 2)
  assert.equal((await call('import_files', { files: [file] })).imported, 0)
  let rows = await call('list_ledger', { year: 2026, month: 11 })
  assert.equal(rows.length, 2)
  assert.equal(rows.find(row => row.name === '午餐').amount_minor, 1234)
  assert.match(rows[0].occurred_at, /\+08:00$/)
  await worker.close(); worker = new BusinessWorker(dir, options)
  const again = await call('import_files', { files: [file, file], dry_run: true })
  assert.equal(again.ready, 0)
  assert.equal(again.duplicates, 4)
  assert.equal((await call('list_ledger', { year: 2026, month: 11 })).length, 2)
})

test('WeChat XLSX and GB18030 Alipay exports preserve exact IDs, money and CSV quoting', async t => {
  const { execFileSync } = await import('node:child_process')
  const files = JSON.parse(execFileSync('python3', ['product/tests/bill-import-fixtures.py'], { encoding: 'utf8' }))
  const dir = await mkdtemp(join(tmpdir(), 'stella-bill-xlsx-'))
  const worker = new BusinessWorker(dir, { worker: new URL('../../packages/stella-secretary/business/worker.py', import.meta.url) })
  t.after(async () => { await worker.close(); await rm(dir, { recursive: true, force: true }) })
  const call = (method, args = {}) => worker.call('invoke', { domain: 'bills', method, arguments: args })
  const result = await call('import_files', { files })
  assert.equal(result.imported, 3)
  assert.deepEqual(result.totals_minor, { income: '1000', expense: '3381' })
  const rows = await call('list_ledger', { year: 2026, month: 11 })
  assert(rows.some(row => row.note.includes('900719925474099312345')))
  assert(rows.some(row => row.name === '车票,返程'))
  const statements = await call('list_statements')
  assert(statements.length >= 2)
  const foreign = { name: '外币.csv', content: Buffer.from('交易时间,收/支,金额,交易状态,交易订单号,币种\n2026-11-02 18:00:00,支出,99.99,交易成功,usd-one,USD').toString('base64') }
  const excluded = await call('import_files', { files: [foreign] })
  assert.equal(excluded.imported, 0)
  assert.equal(excluded.skipped, 1)
  const changed = { name: '支付宝.csv', content: Buffer.from('交易时间,收/支,金额,交易状态,交易订单号,商品说明\n2026-11-02 18:00:00,支出,99.99,交易成功,ali-one,变化').toString('base64') }
  await assert.rejects(call('import_files', { files: [files[0], changed] }), /内容已变化/)
  assert.equal((await call('list_ledger', { year: 2026, month: 11 })).length, 3)
  await assert.rejects(call('import_files', { files: [{ name: 'bad.xlsx', content: Buffer.from('bad zip').toString('base64') }] }), /损坏/)
  await assert.rejects(call('import_files', { files: [{ name: 'bad.csv', content: '*invalid*' }] }), /编码/)
})

test('a mid-import failure rolls back ledger, dedupe keys and period reports together', () => {
  execFileSync('python3', ['-B', '-c', `
import sys,tempfile,runpy
from pathlib import Path
sys.path.insert(0, 'packages/stella-secretary')
from business.bills.domain.store import BillStore
from business.bills.domain.service import BillsService
files=runpy.run_path('product/tests/bill-import-fixtures.py')['files']()
with tempfile.TemporaryDirectory() as tmp:
 store=BillStore(Path(tmp)/'bills.sqlite3'); service=BillsService(store)
 original=store.insert_ledger
 calls=[0]
 def fail(entry):
  calls[0]+=1
  if calls[0]==2: raise RuntimeError('synthetic database failure')
  return original(entry)
 store.insert_ledger=fail
 try: service.import_files(files)
 except RuntimeError: pass
 else: raise AssertionError('Expected failure')
 assert store.list_ledgers()==[]
 with store._conn() as db:
  assert db.execute('SELECT count(*) FROM bill_import_sources').fetchone()[0]==0
  assert db.execute('SELECT count(*) FROM period_statements').fetchone()[0]==0
 store.insert_ledger=original
 assert service.import_files(files)['imported']==3
 row=store.list_ledgers()[0]
 service.delete_ledger(row['id'])
 assert service.import_files(files)['imported']==0
 assert len(store.list_ledgers())==2
`])
})

test('spreadsheet formulas and external entities are refused before ledger writes', () => {
  execFileSync('python3', ['-B', '-c', `
import sys,io,zipfile,base64
sys.path.insert(0,'packages/stella-secretary')
from business.bills.domain.import_files import parse_files
for xml in ['<!DOCTYPE worksheet [<!ENTITY x SYSTEM "file:///etc/passwd">]><worksheet/>', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1"><f>HYPERLINK("http://example.com")</f></c></row></sheetData></worksheet>']:
 output=io.BytesIO()
 with zipfile.ZipFile(output,'w') as archive: archive.writestr('xl/worksheets/sheet1.xml',xml)
 try: parse_files([{'name':'test.xlsx','content':base64.b64encode(output.getvalue()).decode()}])
 except ValueError: pass
 else: raise AssertionError('Unsafe workbook accepted')
`])
})
