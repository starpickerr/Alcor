import test from 'node:test'
import { execFileSync } from 'node:child_process'

test('inbound item and category deletion atomically save full affected local rows', () => {
  execFileSync('python3', ['-B', '-c', `
import json,sys,tempfile
from pathlib import Path
sys.path.insert(0,'packages/alcor-secretary')
from business.tasks.domain.service import TodoService
from business.calendar.domain.service import CalendarService
with tempfile.TemporaryDirectory(prefix='alcor-inbound-snapshot-') as root:
 tasks=TodoService(Path(root)/'tasks.sqlite3')
 category=tasks.create_category({'name':'Synthetic category'})
 parent=tasks.create_todo({'title':'Synthetic parent','category_id':category['id']})
 child=tasks.create_todo({'title':'Synthetic child','parent_id':parent['id']})
 outside=tasks.create_todo({'title':'Synthetic other'})
 tasks.add_dependency(outside['id'],child['id'])
 tasks.apple_bind('item',parent['id'],{'apple_id':'synthetic','can_delete':True})
 with tasks._connect() as db:
  db.execute('INSERT INTO apple_completion_history VALUES (?,?,?,?)',(parent['id'],'native','2026-01-01','{}'))
  db.execute('INSERT INTO todo_completions VALUES (?,?,?)',(child['id'],'2026-01-01','2026-01-01'))
  db.execute('INSERT INTO reminder_occurrences VALUES (?,?,?,?,?,?)',('occ',child['id'],'2026-01-01','due','2026-01-01','pending'))
  db.execute('INSERT INTO reminder_deliveries VALUES (?,?,?,?,?,?,?,?)',('delivery','occ','pending',None,None,0,None,None))
  db.execute('INSERT INTO reminder_route_deliveries VALUES (?,?,?,?,?,?,?,?,?,?)',('delivery','primary','desktop','desktop','pending',0,0,None,None,'2026-01-01'))
 tasks.apple_remove_category(category['id'])
 with tasks._connect() as db:
  docs=db.execute('SELECT document FROM apple_inbound_recovery').fetchall(); assert len(docs)==1
  tables=json.loads(docs[0][0])['tables']
  assert {x['id'] for x in tables['todos']}=={parent['id'],child['id']}
  assert len(tables['todo_categories'])==1 and len(tables['todo_dependencies'])==1
  assert len(tables['apple_bindings'])==1 and len(tables['apple_completion_history'])==1
  assert len(tables['todo_completions'])==1 and len(tables['reminder_occurrences'])==1
  assert len(tables['reminder_deliveries'])==1 and len(tables['reminder_route_deliveries'])==1
  assert db.execute('SELECT 1 FROM todos WHERE id=?',(parent['id'],)).fetchone() is None
  assert db.execute('SELECT 1 FROM todos WHERE id=?',(outside['id'],)).fetchone()
 recovery=tasks.list_apple_inbound_recoveries()[0]['id']
 tasks.create_category({'name':'Synthetic category'}) # A recreated name cannot be overwritten.
 result=tasks.restore_apple_inbound_recovery(recovery)
 assert result['sync']=='local_only' and len(result['ids'])==2 and len(result['categoryIds'])==1
 assert any(x['kind']=='category_renamed' for x in result['conflicts'])
 assert any(x['kind']=='external_dependency_not_relinked' for x in result['conflicts'])
 restored=[tasks.get_todo(identity) for identity in result['ids']]
 assert all(row['source_type']=='apple_recovered' and row['remind_at'] is None for row in restored)
 assert sum(row['parent_id'] in result['ids'] for row in restored)==1
 assert tasks.apple_local_only_categories()==result['categoryIds']
 replay=tasks.restore_apple_inbound_recovery(recovery);assert replay['replayed'] and replay['ids']==result['ids'] and not replay['currentConflict']
 with tasks._connect() as db:
  assert db.execute('SELECT count(*) FROM reminder_occurrences WHERE todo_id IN (?,?) AND state!=?',(*result['ids'],'cancelled')).fetchone()[0]==0
  assert db.execute('SELECT count(*) FROM apple_completion_history WHERE local_id IN (?,?)',result['ids']).fetchone()[0]==1
 calendar=CalendarService(Path(root)/'calendar.sqlite3')
 event=calendar.create_event({'title':'Synthetic event','start_at':'2026-11-01T09:00:00+00:00','end_at':'2026-11-01T10:00:00+00:00'})
 calendar.apple_bind('item',event['id'],{'apple_id':'native','can_delete':True})
 with calendar._connect() as db:
  db.execute('INSERT INTO calendar_occurrence_exclusions VALUES (?,?)',(event['id'],'2026-11-02'))
  db.execute('INSERT INTO agenda_handoffs VALUES (?,?,?,?,?,?,?,?,?)',('source','key','calendar','Title','Summary','brief','item',event['id'],'2026-01-01'))
 calendar.apple_remove_item(event['id'])
 with calendar._connect() as db:
  data=json.loads(db.execute('SELECT document FROM apple_inbound_recovery').fetchone()[0])['tables']
  assert len(data['calendar_events'])==1 and len(data['calendar_occurrence_exclusions'])==1 and len(data['agenda_handoffs'])==1 and len(data['apple_bindings'])==1
  assert db.execute('SELECT 1 FROM calendar_events WHERE id=?',(event['id'],)).fetchone() is None
 recovery=calendar.list_apple_inbound_recoveries()[0]['id']
 result=calendar.restore_apple_inbound_recovery(recovery)
 assert result['sync']=='local_only' and len(result['ids'])==1
 restored=calendar.get_event(result['ids'][0]);assert restored['source_type']=='apple_recovered' and restored['title']==event['title']
 assert any(x['kind']=='handoff_not_relinked' for x in result['conflicts'])
 assert calendar.restore_apple_inbound_recovery(recovery)['replayed']
`])
})

test('failure to save an inbound recovery snapshot rolls back deletion', () => {
  execFileSync('python3', ['-B', '-c', `
import sys,tempfile
from pathlib import Path
sys.path.insert(0,'packages/alcor-secretary')
from business.tasks.domain.service import TodoService
from business.calendar.domain.service import CalendarService
with tempfile.TemporaryDirectory(prefix='alcor-inbound-rollback-') as root:
 for domain, cls in [('tasks',TodoService),('calendar',CalendarService)]:
  svc=cls(Path(root)/(domain+'.sqlite3'))
  if domain=='tasks': item=svc.create_todo({'title':'Synthetic'});read=svc.get_todo
  else: item=svc.create_event({'title':'Synthetic','start_at':'2026-11-01T09:00:00+00:00','end_at':'2026-11-01T10:00:00+00:00'});read=svc.get_event
  with svc._connect() as db:
   db.execute('CREATE TABLE apple_inbound_recovery (id TEXT PRIMARY KEY, domain TEXT NOT NULL, entity TEXT NOT NULL, target_id TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL)')
   db.execute(\"CREATE TRIGGER fail_archive BEFORE INSERT ON apple_inbound_recovery BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END\")
  try: svc.apple_remove_item(item['id'])
  except Exception as error: assert 'synthetic failure' in str(error)
  else: raise AssertionError('deletion succeeded without archive')
  assert read(item['id'])['id']==item['id']
`])
})

test('restored inbound items remain local through the next real synthetic Apple sync', () => {
  execFileSync('python3', ['-B', '-c', `
import copy,runpy
from datetime import datetime,timedelta,timezone
m=runpy.run_path('product/tests/apple-contracts.py')
for kind in ('calendar','reminders'):
 fixture=m['Contracts']();fixture.setUp()
 try:
  bridge=m['NativeBridge'](kind);sync=fixture.calendar(bridge) if kind=='calendar' else fixture.reminder(bridge)
  owner=fixture.business.calendar if kind=='calendar' else fixture.business.tasks
  sync.start_sync();item=(owner.list_events() if kind=='calendar' else owner.list_todos())[0]
  other=copy.deepcopy(bridge.rows[0]);other['event_id' if kind=='calendar' else 'reminder_id']='unrelated-id';other['external_id']='unrelated-external';other['title']='Synthetic other';bridge.rows=[other]
  now=datetime.now(timezone.utc);sync.clock=lambda:now.isoformat();sync.sync();sync.clock=lambda:(now+timedelta(minutes=2)).isoformat();result=sync.sync()
  if kind=='calendar':
   assert result['deleted_local']==0
   # Prepare an explicit conditional owner archive; window null is not deletion proof.
   missing=owner.apple_capture_calendar_missing(item['id']); expected=missing['capture']; binding=expected['local']['apple']
   assert owner.apple_remove_calendar_inbound(expected,binding['source_id'],binding['group_id'],binding['apple_id'],expected['generation'],missing['deletion_snapshot'])
  else:
   assert result['deleted_local']==1
  record=owner.list_apple_inbound_recoveries()[0]; restored=owner.restore_apple_inbound_recovery(record['id'])
  assert restored['sync']=='local_only' and restored['ids']
  bridge.calls.clear();sync.sync()
  assert all(op not in ('upsert','reminder_upsert','create_calendar','reminder_list_create','delete','reminder_delete') for op,_ in bridge.calls), bridge.calls
  row=(owner.get_event if kind=='calendar' else owner.get_todo)(restored['ids'][0]);assert row['source_type']=='apple_recovered'
 finally:fixture.tearDown()
`])
})

test('restored inbound categories never create a new Apple source automatically', () => {
  execFileSync('python3', ['-B', '-c', `
import runpy
m=runpy.run_path('product/tests/apple-contracts.py')
for kind in ('calendar','reminders'):
 fixture=m['Contracts']();fixture.setUp()
 try:
  bridge=m['NativeBridge'](kind);sync=fixture.calendar(bridge) if kind=='calendar' else fixture.reminder(bridge)
  owner=fixture.business.calendar if kind=='calendar' else fixture.business.tasks
  sync.start_sync();category=owner.create_category({'name':'Synthetic recovered category'})
  if kind=='calendar': owner.create_event({'title':'Synthetic','category_id':category['id'],'start_at':'2026-11-01T09:00:00+00:00','end_at':'2026-11-01T10:00:00+00:00'})
  else: owner.create_todo({'title':'Synthetic','category_id':category['id']})
  owner.apple_remove_category(category['id']);record=owner.list_apple_inbound_recoveries()[0]
  result=owner.restore_apple_inbound_recovery(record['id']);assert result['categoryIds']
  bridge.calls.clear();sync.sync()
  assert not any(op in ('create_calendar','reminder_list_create','upsert','reminder_upsert') for op,_ in bridge.calls),bridge.calls
  assert result['categoryIds'][0] in owner.apple_local_only_categories()
  bridge.calls.clear()
  try: sync.replace_links([{'apple_id':'a','alcor_id':result['categoryIds'][0],'direction':'bidirectional','enabled':True}])
  except ValueError as error: assert '本机分类' in str(error)
  else: raise AssertionError('local-only category accepted for Apple link')
  assert not bridge.calls
 finally: fixture.tearDown()
`])
})
