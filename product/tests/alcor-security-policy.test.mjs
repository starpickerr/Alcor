import { python as testPython } from './platform-python.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'
import { BusinessWorker } from '../../packages/alcor-organizer/src/business.js'
import { automationMethods } from '../../packages/alcor-organizer/src/page-tools.js'
import { classifyActor, classifyCapability } from '../../packages/alcor-secretary/src/security-policy.js'

const business = (domain, method, args = {}) => classifyCapability('alcor_business', { domain, method, arguments: args }).kind

test('every actual business catalog method is classified, and new operations fail closed', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'alcor-policy-catalog-'))
  const worker = new BusinessWorker(directory, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  t.after(async () => { await worker.close(); await rm(directory, { recursive: true, force: true }) })
  for (const domain of ['tasks', 'calendar', 'bills', 'briefing']) {
    const methods = await worker.call('invoke', { domain, method: 'catalog', arguments: {} })
    for (const { name } of methods) assert.notEqual(business(domain, name), 'unclassified', `${domain}:${name}`)
    assert.equal(business(domain, 'new_unclassified_operation'), 'unclassified')
  }
  for (const { name } of automationMethods) {
    assert.notEqual(business('automation', name, name === 'edit' ? { action: 'update' } : {}), 'unclassified', name)
  }
  assert.equal(classifyCapability('alcor_unclassified').kind, 'unclassified')
  assert.equal(classifyCapability('bash').kind, 'not-alcor')
})

test('resource and irreversible operations never classify as ordinary local writes', () => {
  for (const [domain, name] of [['tasks','create_todo'], ['tasks','update_todo'], ['calendar','list_events'], ['calendar','update_event']]) assert.equal(business(domain, name), 'resource')
  for (const [domain, name] of [['tasks','delete_todo'], ['calendar','delete_event'], ['calendar','exclude_occurrence'], ['bills','delete_ledger'], ['briefing','delete_rule']]) assert.equal(business(domain, name), 'irreversible')
  assert.equal(business('automation','edit',{action:'delete'}), 'irreversible')
  assert.equal(business('automation','deleteInactive'), 'irreversible')
  assert.equal(business('automation','edit',{action:'invented'}), 'unclassified')
  assert.equal(business('apple','sync'), 'apple')
  assert.equal(classifyCapability('alcor_generate_briefing').kind, 'briefing')
  assert.equal(classifyCapability('alcor_review_tasks').kind, 'read')
  assert.equal(business('tasks','queue_delivery'), 'internal')
  assert.equal(business('bills','create_ledger'), 'local')
})

test('actor classification needs host-supplied source, rejects mixed and unknown provenance', () => {
  assert.equal(classifyActor({}, ['user']), 'user')
  assert.equal(classifyActor({}, ['schedule']), 'schedule')
  assert.equal(classifyActor({ origin: 'subagent' }, ['user']), 'delegated')
  assert.equal(classifyActor({ parentSession: 'parent' }, ['user']), 'delegated')
  for (const sources of [[], ['user','schedule'], ['tool'], ['unknown']]) assert.equal(classifyActor({}, sources), 'unknown')
  assert.equal(classifyActor(null, ['user']), 'unknown')
})

test('billing owner rejects a stale deletion snapshot inside its transaction', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'alcor-policy-bill-'))
  const worker = new BusinessWorker(directory, { worker: new URL('../../packages/alcor-secretary/business/worker.py', import.meta.url) })
  t.after(async () => { await worker.close(); await rm(directory, { recursive: true, force: true }) })
  const call = (method, args, request_id) => worker.call('invoke', { domain: 'bills', method, arguments: args, request_id })
  const entry = await call('create_ledger', { name: 'Synthetic item', amount_minor: 123, currency: 'CNY', entry_type: 'expense', occurred_at: '2026-11-06T09:00:00+08:00', category: 'Test' }, 'create-test')
  const snapshot = await call('get_ledger', { id: entry.id })
  await call('update_ledger', { id: entry.id, patch: { name: 'Synthetic changed item' }, expectedRevision: snapshot.revision }, 'update-test')
  await assert.rejects(call('delete_ledger', { id: entry.id, expectedRevision: snapshot.revision }, 'delete-stale'), /人工确认/)
  await assert.rejects(worker.call('commit_bill_delete', { entry_id: entry.id, revision: snapshot.revision, snapshot, operation_id: 'a'.repeat(64) }), /变化/)
  const current = await call('get_ledger', { id: entry.id })
  assert.equal(current.name, 'Synthetic changed item')
  const input = { entry_id: entry.id, revision: current.revision, snapshot: current, operation_id: 'b'.repeat(64) }
  assert.equal((await worker.call('commit_bill_delete', input)).deleted, true)
  assert.equal((await worker.call('commit_bill_delete', input)).replayed, true)
})

test('local todo deletion snapshot is compared in the owner write transaction', () => {
  execFileSync(testPython, ['-B', '-c', `
from contextlib import closing
import sqlite3, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, 'packages/alcor-secretary')
from business.tasks.domain.service import TodoService
with tempfile.TemporaryDirectory(prefix='alcor-security-todo-') as root:
    path = Path(root) / 'todos.sqlite3'
    svc = TodoService(path)
    parent = svc.create_todo({'title': 'synthetic parent'})
    child = svc.create_todo({'title': 'synthetic child', 'parent_id': parent['id']})
    plan = svc.prepare_local_todo_delete(parent['id'], operation_id='a'*64)
    assert plan['count'] == 2
    svc.update_todo(child['id'], {'title': 'synthetic changed child'})
    try: svc.commit_local_todo_delete(parent['id'], snapshot=plan['snapshot'], operation_id='a'*64)
    except ValueError as error: assert 'snapshot conflict' in str(error)
    else: raise AssertionError('changed child must fail closed')
    assert svc.get_todo(child['id'])['title'] == 'synthetic changed child'
    plan = svc.prepare_local_todo_delete(parent['id'], operation_id='a'*64)
    svc.apple_bind('item', child['id'], {'apple_id': 'synthetic-id', 'can_delete': True})
    try: svc.commit_local_todo_delete(parent['id'], snapshot=plan['snapshot'], operation_id='a'*64)
    except ValueError as error: assert 'Apple' in str(error)
    else: raise AssertionError('new Apple binding must fail closed')
    with svc._connect() as db: db.execute('DELETE FROM apple_bindings')
    plan = svc.prepare_local_todo_delete(parent['id'], operation_id='a'*64)
    original = svc._delete_todo_tree
    attempts = []
    def guarded(db, todo_id, preserve_children=False):
        def concurrent_write():
            try:
                with closing(sqlite3.connect(str(path), timeout=0.01)) as other, other:
                    other.execute('UPDATE todos SET title=? WHERE id=?', ('late write', child['id']))
                attempts.append('wrote')
            except sqlite3.OperationalError: attempts.append('locked')
        thread = threading.Thread(target=concurrent_write); thread.start(); thread.join()
        return original(db, todo_id, preserve_children)
    svc._delete_todo_tree = guarded
    assert svc.commit_local_todo_delete(parent['id'], snapshot=plan['snapshot'], operation_id='a'*64)['deleted']
    assert attempts == ['locked']
    try: svc.get_todo(parent['id'])
    except KeyError: pass
    else: raise AssertionError('parent remains')
    keep = svc.create_todo({'title': 'synthetic parent 2'})
    kept = svc.create_todo({'title': 'synthetic child 2', 'parent_id': keep['id']})
    plan = svc.prepare_local_todo_delete(keep['id'], preserve_children=True, operation_id='b'*64)
    assert plan['preserve_children'] is True and plan['count'] == 2
    svc._delete_todo_tree = original
    try: svc.commit_local_todo_delete(keep['id'], snapshot=plan['snapshot'], preserve_children=True, operation_id='b'*64)
    except ValueError: pass
    else: raise AssertionError('existing preserve-children rule must remain')
    assert svc.get_todo(kept['id'])['parent_id'] == keep['id']
`])
})

test('local nonrecurring calendar deletion rechecks target and category binding in one transaction', () => {
  execFileSync(testPython, ['-B', '-c', `
import sys, tempfile
from pathlib import Path
sys.path.insert(0, 'packages/alcor-secretary')
from business.calendar.domain.service import CalendarService
with tempfile.TemporaryDirectory(prefix='alcor-security-calendar-') as root:
    svc = CalendarService(Path(root) / 'calendar.sqlite3')
    event = svc.create_event({'title': 'synthetic event', 'start_at': '2026-11-06T09:00:00+08:00', 'end_at': '2026-11-06T10:00:00+08:00'})
    plan = svc.prepare_local_event_delete(event['id'], operation_id='c'*64)
    svc.update_event(event['id'], {'title': 'synthetic update'})
    try: svc.commit_local_event_delete(event['id'], snapshot=plan['snapshot'], operation_id='c'*64)
    except ValueError as error: assert 'snapshot conflict' in str(error)
    else: raise AssertionError('changed event must be retained')
    assert svc.get_event(event['id'])['title'] == 'synthetic update'
    plan = svc.prepare_local_event_delete(event['id'], operation_id='c'*64)
    category_id = svc.get_event(event['id'])['category_id']
    svc.apple_bind('category', category_id, {'apple_id': 'synthetic-id', 'can_delete': True})
    try: svc.commit_local_event_delete(event['id'], snapshot=plan['snapshot'], operation_id='c'*64)
    except ValueError as error: assert 'Apple' in str(error)
    else: raise AssertionError('new Apple binding must be retained')
    with svc._connect() as db: db.execute('DELETE FROM apple_bindings')
    plan = svc.prepare_local_event_delete(event['id'], operation_id='c'*64)
    assert svc.commit_local_event_delete(event['id'], snapshot=plan['snapshot'], operation_id='c'*64)['deleted']
    try: svc.get_event(event['id'])
    except KeyError: pass
    else: raise AssertionError('event remains')
    recurring = svc.create_event({'title': 'synthetic repeat', 'start_at': '2026-11-06T09:00:00+08:00', 'end_at': '2026-11-06T10:00:00+08:00', 'repeat_frequency': 'daily'})
    try: svc.prepare_local_event_delete(recurring['id'], operation_id='d'*64)
    except ValueError as error: assert '重复' in str(error)
    else: raise AssertionError('recurring deletion needs separate handling')
`])
})

test('billing owner checks receipt and current target in the same write transaction', () => {
  execFileSync(testPython, ['-B', '-c', `
import tempfile, sys
from pathlib import Path
sys.path.insert(0, 'packages/alcor-secretary')
from business.bills.domain.store import BillStore
from business.bills.domain.service import BillsService
with tempfile.TemporaryDirectory(prefix='alcor-receipt-owner-') as root:
    store = BillStore(Path(root) / 'bills.sqlite3'); svc = BillsService(store)
    row = svc.create_ledger({'name':'Synthetic row','amount_minor':123,'currency':'CNY','entry_type':'expense','occurred_at':'2026-11-06T09:00:00Z','category':'Test'})
    svc.commit_approved_ledger_delete(row['id'], 'synthetic-operation', row['revision'], row)
    replay = svc.verify_deleted_ledger(row['id'], 'synthetic-operation', row['revision'])
    assert replay['replayed'] and not replay['targetConflict'] and replay['receipt']['result_id'] == row['id']
    try: svc.verify_deleted_ledger(row['id'], 'synthetic-operation', row['revision']+1)
    except ValueError: pass
    else: raise AssertionError('different revision must not replay')
    store.insert_ledger(row)
    replay = svc.verify_deleted_ledger(row['id'], 'synthetic-operation', row['revision'])
    assert replay['replayed'] and replay['targetConflict'] and replay['receipt']['result_id'] == row['id']
    assert svc.get_ledger(row['id'])['id'] == row['id']
    replay = svc.commit_approved_ledger_delete(row['id'], 'synthetic-operation', row['revision'], row)
    assert replay['targetConflict'] and svc.get_ledger(row['id']) == row
`])
})
