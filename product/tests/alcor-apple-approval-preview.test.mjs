import { python as testPython } from './platform-python.mjs'
import test from 'node:test'
import { execFileSync } from 'node:child_process'

test('Apple preview binds the exact target; old pending intent cannot authorize background dispatch', () => {
  execFileSync(testPython, ['-B', '-c', `
import hashlib,json,runpy,sys
sys.path[:0]=['packages/alcor-secretary','packages/alcor-apple-sync/python']
m=runpy.run_path('product/tests/apple-contracts.py')
from apple_sync.contracts import process_operations
case=m['Contracts']('test_unconfirmed_occurrence_delete_preserves_instances_after_restart');case.setUp()
try:
 bridge,sync,item=case.local_series();owner=case.business.calendar
 before=len(bridge.calls)
 preview=sync.prepare_delete_snapshot('item',item['id'],'this','2026-11-08')
 from worker import AppleWorker
 worker=AppleWorker(case.root/'worker-preview',lambda request:None,bridge=bridge);worker.managers={'calendar':sync}
 assert worker.invoke('prepare_delete_snapshot',{'kind':'calendar','entity':'item','local_id':item['id'],'scope':'this','occurrence_on':'2026-11-08'})==preview
 assert preview['scope']=='this' and preview['occurrence_on']=='2026-11-08'
 assert preview['source_id'] and preview['instance_apple_id']!=preview['series_apple_id']
 assert preview['native_fingerprint']==hashlib.sha256(json.dumps(preview['native_snapshot'],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
 assert not any(op in ('delete','upsert','delete_calendar') for op,_ in bridge.calls[before:])
 from business.apple_delete_approval import prepare,commit
 operation_id='a'*64;args={'series_id':item['id'],'occurrence_on':'2026-11-08'}
 plan=prepare(owner,'calendar','exclude_occurrence',args,operation_id)
 def enqueue(native=preview):
  return commit(owner,'calendar','exclude_occurrence',args,operation_id,plan['snapshot'],native)
 first=enqueue();assert first['operation_id']==operation_id and not first['replayed']
 again=enqueue();assert again['replayed'] and again['state']=='pending'
 bad=dict(preview);bad['native_fingerprint']='0'*64
 try:enqueue(bad)
 except ValueError:pass
 else:raise AssertionError('invalid preview accepted')
 target=next(row for row in bridge.rows if row['event_id']==preview['instance_apple_id']);target['title']='Changed after approval'
 count=sum(op=='delete' for op,_ in bridge.calls);_,errors=process_operations(sync,owner,'calendar')
 assert errors and sum(op=='delete' for op,_ in bridge.calls)==count and owner.apple_operations()[0]['state']=='pending'
 target.update(preview['native_snapshot']);_,errors=process_operations(sync,owner,'calendar')
 assert errors and sum(op=='delete' for op,_ in bridge.calls)==count and owner.apple_operations()[0]['state']=='pending'
finally:case.tearDown()

for kind in ('calendar','reminders'):
 case=m['Contracts']('test_unconfirmed_occurrence_delete_preserves_instances_after_restart');case.setUp()
 try:
  bridge=m['NativeBridge'](kind);sync=case.calendar(bridge) if kind=='calendar' else case.reminder(bridge);sync.start_sync()
  owner=case.business.calendar if kind=='calendar' else case.business.tasks
  mapping=sync._maps()[0];ident=mapping['alcor_id' if kind=='calendar' else 'todo_id']
  preview=sync.prepare_delete_snapshot('item',ident,'item')
  assert preview['instance_apple_id']==preview['series_apple_id'] and preview['occurrence_on'] is None
  local=owner.get_event(ident) if kind=='calendar' else owner.get_todo(ident)
  category=sync.prepare_delete_snapshot('category',local['category_id'],'category')
  assert category['entity']=='category' and category['group_id']==category['series_apple_id'] and category['source_id']
  from business.apple_delete_approval import prepare,commit
  domain='calendar' if kind=='calendar' else 'tasks'
  args={'id':local['category_id']} if kind=='calendar' else {'category_id':local['category_id']}
  plan=prepare(owner,domain,'delete_category',args,'c'*64)
  queued=commit(owner,domain,'delete_category',args,'c'*64,plan['snapshot'],category)
  assert queued['pending'] and not queued['replayed']
  category_row=next(row for row in bridge.sources if row['id']==category['series_apple_id']);category_row['title']='Changed after approval'
  count=sum(op in ('delete_calendar','reminder_list_delete') for op,_ in bridge.calls)
  _,errors=process_operations(sync,owner,kind)
  assert errors and sum(op in ('delete_calendar','reminder_list_delete') for op,_ in bridge.calls)==count
  category_row.update(category['native_snapshot']);_,errors=process_operations(sync,owner,kind)
  assert errors and sum(op in ('delete_calendar','reminder_list_delete') for op,_ in bridge.calls)==count and owner.apple_operations()[0]['state']=='pending'
 finally:case.tearDown()
`])
})
