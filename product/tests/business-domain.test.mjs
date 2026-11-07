import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { BusinessWorker } from '../../packages/stella-organizer/src/business.js'
import { HubWorker } from '../../packages/stella-agent-hub/src/worker.js'
import { recordSchema } from '../../packages/stella-organizer/src/records.js'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'

test('business recovery preserves Apple imports, action receipts and unknown operations', async () => {
  await promisify(execFile)(process.env.STELLA_BUSINESS_PYTHON || 'python3', [fileURLToPath(new URL('./business-recovery.py', import.meta.url))])
})

test('briefing catalog retains host operations after AgentHub is detached', async context => {
  const directory = await mkdtemp(join(tmpdir(), 'stella-briefing-catalog-'))
  const worker = new BusinessWorker(directory, { worker: new URL('../../packages/stella-secretary/business/worker.py', import.meta.url) })
  context.after(async () => { await worker.close(); await rm(directory, { recursive: true, force: true }) })
  const catalog = args => worker.call('invoke', { domain: 'briefing', method: 'catalog', arguments: args })
  const methods = await catalog({})
  for (const name of ['configure_sources', 'available_sources', 'generate']) {
    assert.equal(methods.filter(method => method.name === name).length, 1)
    assert.equal((await catalog({ method: name }))[0].name, name)
  }
})

test('migrated business services share page records, persist hierarchy, dependencies, recurrence and ledger receipts', async context => {
  const directory = await mkdtemp(join(tmpdir(), 'stella-business-domain-'))
  const worker = new BusinessWorker(directory, { worker: new URL('../../packages/stella-secretary/business/worker.py', import.meta.url) })
  context.after(async () => { await worker.close(); await rm(directory, { recursive: true, force: true }) })
  const invoke = (domain, method, args = {}, request_id) => worker.call('invoke', { domain, method, arguments: args, ...(request_id ? { request_id } : {}) })
  const parent = await invoke('tasks', 'create_todo', { payload: { title: 'Parent' } })
  const child = await invoke('tasks', 'add_subtask', { parent_id: parent.id, payload: { title: 'Child' } })
  const blocker = await invoke('tasks', 'create_todo', { payload: { title: 'Blocker' } })
  await invoke('tasks', 'add_dependency', { todo_id: child.id, prerequisite_id: blocker.id })
  await assert.rejects(invoke('tasks', 'add_dependency', { todo_id: blocker.id, prerequisite_id: child.id }), /cycl|循环/)
  await assert.rejects(invoke('tasks', 'change_todo_status', { todo_id: child.id, status: 'completed' }), /unfinished|depend/)
  await invoke('tasks', 'complete_todo', { todo_id: blocker.id, completed: true })
  await invoke('tasks', 'change_todo_status', { todo_id: child.id, status: 'completed' })
  assert.equal((await invoke('tasks', 'get_todo', { todo_id: parent.id })).progress_completed, 1)
  let projected = (await worker.call('records')).find(item => item.id === parent.id)
  const edited = await worker.call('write', { record: { ...projected, title: 'Page edit' }, expected_revision: projected.revision })
  assert.equal((await invoke('tasks', 'get_todo', { todo_id: parent.id })).title, 'Page edit')
  assert.equal(edited.business.id, parent.id)
  await assert.rejects(worker.call('write', { record: projected, expected_revision: projected.revision }), /记录已变化/)
  const recurring = await invoke('tasks', 'create_todo', { payload: { title: 'Daily', due_on: '2026-11-05', repeat_frequency: 'daily' } })
  assert.equal((await invoke('tasks', 'list_todos', { start: '2026-11-05', end: '2026-11-08' })).filter(item => item.id.startsWith(recurring.id + '@')).length, 3)
  const category = await invoke('calendar', 'create_category', { name: 'Test', color: '#123456' })
  await invoke('calendar', 'create_event', { title: 'Recurring event', start_at: '2026-11-05T09:00:00+08:00', end_at: '2026-11-05T10:00:00+08:00', category_id: category.id, repeat_frequency: 'daily', repeat_timezone: 'Asia/Shanghai' })
  assert.equal((await invoke('calendar', 'list_events', { start: '2026-11-05T00:00:00+08:00', end: '2026-11-08T00:00:00+08:00' })).length, 3)
  const payload = { name: 'Lunch', amount_minor: 3000, entry_type: 'expense', occurred_at: '2026-11-05T12:00:00+08:00', currency: 'CNY', category: 'Food' }
  const entry = await invoke('bills', 'create_ledger', payload, 'lunch-once')
  assert.equal((await invoke('bills', 'create_ledger', payload, 'lunch-once')).id, entry.id)
  await assert.rejects(invoke('bills', 'create_ledger', { ...payload, amount_minor: 4000 }, 'lunch-once'), /conflict/)
  const statement = (await invoke('bills', 'list_statements', { period_type: 'monthly', year: 2026 }))[0]
  assert.equal(statement.expense_minor, 3000)
  const billRecord = (await worker.call('records')).find(item => item.id === entry.id)
  await worker.call('write', { record: { ...billRecord, title: 'Updated lunch' }, expected_revision: billRecord.revision })
  const precise = await invoke('bills', 'get_ledger', { id: entry.id })
  assert.equal(precise.occurred_at, payload.occurred_at)
  for (const amount_minor of [true, 1.5, '3000']) await assert.rejects(invoke('bills', 'create_ledger', { ...payload, amount_minor }), /integer/)
  const unrestricted = await invoke('bills', 'create_ledger', { ...payload, name: 'Long title '.repeat(60), note: 'Long note '.repeat(900), currency: 'CHF' })
  recordSchema.parse((await worker.call('records')).find(item => item.id === unrestricted.id))
  await invoke('bills', 'update_ledger', { id: entry.id, patch: { status: 'reversed' }, expectedRevision: precise.revision }, 'reverse-once')
  assert.equal((await invoke('bills', 'list_statements', { period_type: 'monthly', year: 2026 })).filter(item => item.currency === 'CNY' && item.expense_minor !== 0).length, 0)
  assert.equal(recordSchema.parse((await worker.call('records')).find(item => item.id === entry.id)).status, 'reversed')
  const beforeReversal = (await worker.call('records')).find(item => item.id === unrestricted.id)
  const reversedOnPage = await worker.call('write', { record: { ...beforeReversal, status: 'reversed' }, expected_revision: beforeReversal.revision })
  assert.equal(reversedOnPage.business.status, 'reversed')
  const exported = await invoke('bills', 'export_ledger', { start: '2026-11-01', end: '2026-12-01' })
  assert.equal(Buffer.from(exported.content, 'base64').subarray(0, 2).toString(), 'PK')
  const stamp = new Date().toISOString()
  const legacy = { id: randomUUID(), kind: 'task', revision: 4, archived: true, title: 'Retained legacy', details: '', due: null, status: 'open', createdAt: stamp, updatedAt: stamp }
  await worker.call('migrate', { records: [legacy] })
  await worker.call('migrate', { records: [legacy] })
  projected = (await worker.call('records')).find(item => item.id === legacy.id)
  assert.equal(projected.revision, 4)
  assert.equal(projected.archived, true)
  assert.equal((await worker.call('records')).filter(item => item.id === legacy.id).length, 1)
})

test('briefing revisions, source deduplication and collaboration configuration survive worker restart', async context => {
  const directory = await mkdtemp(join(tmpdir(), 'stella-business-revisions-'))
  const options = { worker: new URL('../../packages/stella-secretary/business/worker.py', import.meta.url) }
  let worker = new BusinessWorker(directory, options)
  let hub = new HubWorker(join(directory,'hub')); await hub.call('initialize')
  context.after(async () => { await hub.close(); await worker.close(); await rm(directory, { recursive: true, force: true }) })
  const invoke = (domain, method, args = {}) => (domain==='agent_hub' ? hub : worker).call('invoke', { domain, method, arguments: args })
  const sources = [{ source: 'local-test', account_id: 'synthetic', items: [{ source_id: 'one', title: 'Source one', occurred_at: '2026-11-05T09:00:00Z', snippet: 'Retained source' }], cursors: { sequence: '1' } }]
  const first = await invoke('briefing', 'run_pipeline', { briefing_date: '2026-11-05', sources, run_at: '2026-11-05T10:00:00Z' })
  const second = await invoke('briefing', 'run_pipeline', { briefing_date: '2026-11-05', sources, run_at: '2026-11-05T11:00:00Z' })
  assert.equal(second.mail.length, 1)
  assert.equal(second.revisions.length, first.revisions.length + 1)
  const task = await invoke('agent_hub', 'create_task', { payload: { title: 'Task', goal: 'Deliver', participants: [{ nativeAgentId: 'stella' }] }, request_id: randomUUID() })
  const participant = task.participants[0]
  const changed = await invoke('agent_hub', 'post_message', { task_id: task.taskId, body: 'Task persona', recipient_ids: [participant.participantId], intent: 'persona', expected_revision: task.revision, request_id: randomUUID() })
  assert.equal(changed.participants[0].persona, 'Task persona')
  await hub.close(); await worker.close()
  worker = new BusinessWorker(directory, options)
  hub = new HubWorker(join(directory,'hub')); await hub.call('initialize')
  assert.equal((await invoke('agent_hub', 'get_task', { task_id: task.taskId })).participants[0].persona, 'Task persona')
  assert.equal((await invoke('briefing', 'get_briefing', { briefing_id: first.briefing.id })).mail.length, 1)
})


test('bill CSV export uses exact ledger amounts and protects spreadsheet formulas', async context => {
  const directory = await mkdtemp(join(tmpdir(), 'stella-csv-export-'))
  const worker = new BusinessWorker(directory, { worker: new URL('../../packages/stella-secretary/business/worker.py', import.meta.url) })
  context.after(async () => { await worker.close(); await rm(directory, { recursive: true, force: true }) })
  const invoke = (method, args) => worker.call('invoke', { domain: 'bills', method, arguments: args })
  await invoke('create_ledger', { name: '=SUM(1,2)', amount_minor: 1234, currency: 'CNY', entry_type: 'expense', occurred_at: '2026-11-06T12:00:00+08:00' })
  await invoke('create_ledger', { name: 'Japanese yen', amount_minor: 250, currency: 'JPY', entry_type: 'expense', occurred_at: '2026-11-06T12:00:00+08:00' })
  const csv = await invoke('export_ledger_csv', { start: '2026-11-01', end: '2026-11-30' })
  assert(csv.content.includes('12.34'))
  assert(csv.content.includes("'=SUM(1,2)"))
  assert(csv.content.includes(',250,JPY,'))
})
