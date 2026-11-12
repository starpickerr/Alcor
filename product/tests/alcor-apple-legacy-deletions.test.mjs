import { python as testPython } from './platform-python.mjs'
import test from 'node:test'
import { execFileSync } from 'node:child_process'

test('legacy mapping and category deletions retain Apple targets without dispatching writes', () => {
  execFileSync(testPython, ['-B', '-c', `
import runpy,sys
sys.path[:0]=['packages/alcor-secretary','packages/alcor-apple-sync/python']
m=runpy.run_path('product/tests/business-recovery.py');Base=m['Bridge']
from apple_sync.calendar import AppleCalendarSync
from apple_sync.reminders import AppleReminderSync
class Observing(Base):
 def __init__(self,kind):super().__init__(kind);self.deletes=[]
 def call(self,op,**kwargs):
  if op in ('delete','reminder_delete','delete_calendar','reminder_list_delete'):
   self.deletes.append(op);raise AssertionError('legacy state dispatched native deletion')
  if op in ('events','reminders'):
   value=super().call(op,**kwargs);group='calendar_id' if op=='events' else 'list_id';key='events' if op=='events' else 'reminders';selected=kwargs['calendar_ids' if op=='events' else 'list_ids']
   value[key]=[row for row in value[key] if row[group] in selected];value['successful_calendar_ids' if op=='events' else 'successful_list_ids']=[identity for identity in selected if any(source['id']==identity for source in self.sources)]
   return value
  return super().call(op,**kwargs)
for kind,cls,port in (('calendar',AppleCalendarSync,'calendar_port'),('reminders',AppleReminderSync,'todo_port')):
 for target in ('item','category','category_manual'):
  case=m['Recovery']('test_unavailable_apple_account_does_not_delete_categories');case.setUp()
  try:
   owner=case.business.calendar if kind=='calendar' else case.business.tasks
   bridge=Observing(kind);sync=cls(bridge=bridge,db_path=case.root/(kind+'-'+target+'.sqlite3'),**{port:owner});sync.start_auto=lambda:None
   sync.start_sync([{'apple_id':'a'}]);mapping=sync._maps()[0]
   ident=mapping['alcor_id' if kind=='calendar' else 'todo_id'];item=owner.get_event(ident) if kind=='calendar' else owner.get_todo(ident)
   with owner._connect() as db:
    if kind=='calendar':owner._delete_event(db,item)
    else:owner._delete_todo_tree(db,ident)
    if target!='item':db.execute('DELETE FROM '+('calendar_categories' if kind=='calendar' else 'todo_categories')+' WHERE id=?',(item['category_id'],))
   if target=='item':
    mapping['state']='pending_delete';sync._save_map(mapping)
   else:
    with sync._connect() as db:db.execute('DELETE FROM mappings')
    sync._set('sync_all',target=='category')
    if target=='category':
     import copy
     bridge.sources.append({'id':'b','title':'Other synthetic source','source_id':'synthetic-source','writable':True})
     extra=copy.deepcopy(bridge.rows[0]);extra['calendar_id' if kind=='calendar' else 'list_id']='b';extra['event_id' if kind=='calendar' else 'reminder_id']='other';extra['external_id']='other';bridge.rows.append(extra)
   result=sync.sync();status=sync.status()
   assert result['status'] in ('partial','error') and not bridge.deletes,(kind,target,result)
   assert bridge.rows and bridge.sources and sync._stored_links()
   if target=='item':
    assert sync._maps()[0]['state']=='pending_delete' and status['legacy_deletions']
    mapping=sync._maps()[0];mapping['state']='conflict';mapping['conflict']='legacy';sync._save_map(mapping)
    try:sync.resolve_conflict(ident,'keep_alcor')
    except ValueError as error:assert '重新获得具体删除授权' in str(error)
    else:raise AssertionError('legacy conflict bypassed approval')
    mapping['state']='pending_delete';sync._save_map(mapping)
    bridge.rows=[];sync.sync();assert sync._maps()[0]['state']=='pending_delete' and not bridge.deletes
   else:
    assert any(row['kind']=='legacy_delete' for row in status['category_conflicts']),(kind,target,result,status['category_conflicts'])
    if target=='category':assert {row['apple_id'] for row in sync._stored_links()}=={'a','b'}
  finally:case.tearDown()
`])
})
