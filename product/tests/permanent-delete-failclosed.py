"""Real business owners in synthetic SQLite only. No Profile, model or EventKit."""
import json,sys,tempfile,unittest,sqlite3
from pathlib import Path
sys.path[:0]=['packages/alcor-secretary','packages/alcor-apple-sync/python']
from business.worker import Business
from apple_sync.bridge import AppleCalendarBridge,BridgeError
from apple_sync.contracts import process_operations

class DeletionBoundary(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='alcor-delete-boundary-');self.addCleanup(self.tmp.cleanup)
  self.b=Business(self.tmp.name);self.addCleanup(self.b.meta.close)
  self.task=self.b.tasks.create_todo({'title':'Synthetic parent'})
  self.child=self.b.tasks.create_todo({'title':'Synthetic child','parent_id':self.task['id']})
  self.event=self.b.calendar.create_event({'title':'Synthetic event','start_at':'2026-11-01T09:00:00+00:00','end_at':'2026-11-01T10:00:00+00:00'})
  self.bill=self.b.invoke('bills','create_ledger',{'name':'Synthetic bill','amount_minor':123,'currency':'CNY','entry_type':'expense','occurred_at':'2026-11-01T09:00:00Z','category':'Test'})
 def denied(self,fn):
  with self.assertRaisesRegex((PermissionError,ValueError,BridgeError),'人工确认'):fn()
 def test_raw_business_and_service_deletions_preserve_primary_rows(self):
  for domain,method,args in [('tasks','delete_todo',{'todo_id':self.task['id']}),('tasks','delete_external_todo',{'source_key':'synthetic','confirm':True}),('calendar','delete_event',{'id':self.event['id']}),('bills','delete_ledger',{'id':self.bill['id']})]:
   with self.subTest(domain=domain,method=method):self.denied(lambda:self.b.invoke(domain,method,args))
  for domain in ['tasks','calendar']:
   svc=getattr(self.b,domain);category=svc.create_category({'name':'Synthetic category'})
   self.denied(lambda:svc.delete_category(category['id']))
   plan=self.b.local_delete_prepare(domain,category['id'],'a'*64,category=True)
   self.assertTrue(plan['snapshot'])
   self.assertEqual(svc.get_category(category['id'])['id'],category['id'])
  self.assertEqual(self.b.tasks.get_todo(self.child['id'])['parent_id'],self.task['id'])
  self.assertEqual(self.b.calendar.get_event(self.event['id'])['id'],self.event['id'])
  self.assertEqual(self.b.bills.get_ledger(self.bill['id'])['id'],self.bill['id'])
 def test_private_conditional_commits_reject_changed_versions_and_preserve_exact_receipts(self):
  # These private actions are called only behind the Host approval gate; raw invoke remains denied above.
  for domain,row in [('tasks',self.task),('calendar',self.event)]:
   plan=self.b.local_delete_prepare(domain,row['id'],'b'*64)
   with self.assertRaises(ValueError):self.b.local_delete_commit(domain,row['id'],'b'*64,snapshot='0'*64)
   self.b.local_delete_commit(domain,row['id'],'b'*64,snapshot=plan['snapshot'])
   self.assertTrue(self.b.local_delete_commit(domain,row['id'],'b'*64,snapshot=plan['snapshot'])['replayed'])
   category=getattr(self.b,domain).create_category({'name':'Private synthetic'})
   plan=self.b.local_delete_prepare(domain,category['id'],'c'*64,category=True)
   self.b.local_delete_commit(domain,category['id'],'c'*64,snapshot=plan['snapshot'],category=True)
   with self.assertRaises((ValueError,KeyError)):getattr(self.b,domain).get_category(category['id'])
  with self.assertRaises(ValueError):self.b.commit_bill_delete(self.bill['id'],'d'*64,self.bill['revision'],{**self.bill,'name':'stale snapshot'})
  with self.assertRaises((ValueError,PermissionError)):
   self.b.commit_apple_delete('tasks','delete_todo',{'todo_id':'missing'},'e'*64,'f'*64,{'owner':True,'allowed-once':True})
 def test_only_local_future_cutoff_is_reversible_and_allowed(self):
  svc=self.b.calendar;row=svc.create_event({'title':'Synthetic series','start_at':'2026-11-01T09:00:00+00:00','end_at':'2026-11-01T10:00:00+00:00','repeat_frequency':'daily','repeat_timezone':'UTC'})
  for method in [lambda:svc.delete_event(row['id'],'future','2026-11-01'),lambda:svc.delete_event(row['id'],'this','2026-11-02'),lambda:svc.exclude_occurrence(row['id'],'2026-11-02')]:self.denied(method)
  plan=self.b.prepare_local_occurrence_delete(row['id'],'2026-11-03','future','f'*64)
  self.assertTrue(plan['reversible'])
  self.b.commit_local_occurrence_delete(row['id'],'2026-11-03','future','f'*64,plan['snapshot'])
  self.assertEqual(svc.get_event(row['id'])['repeat_until'],'2026-11-02')
  svc.update_event(row['id'],{'repeat_until':None});self.assertFalse(svc.get_event(row['id'])['repeat_until'])
  self.b.invoke('calendar','delete_event',{'id':row['id'],'scope':'future','occurrence_on':'2026-11-03'})
  svc.update_event(row['id'],{'repeat_until':None})
  svc.apple_bind('item',row['id'],{'apple_id':'native','source_id':'source','group_id':'group','can_delete':True,'can_modify':True})
  with self.assertRaises(ValueError):svc.delete_event(row['id'],'future','2026-11-03')
  self.assertEqual(svc.apple_operations(),[])
 def test_pending_and_uncertain_apple_deletes_never_dispatch_or_finish(self):
  svc=self.b.tasks;svc.apple_bind('item',self.task['id'],{'apple_id':'native','source_id':'source','group_id':'group','can_delete':True})
  for action in ['delete','truncate','exclude']:
   with svc._connect() as db:
    if action=='delete':
     with self.assertRaisesRegex(ValueError,'不一致'):
      svc._apple_queue(db,'item',self.task['id'],action,{},operation_id='1'*64,approved_snapshot='2'*64,approved_native={'allowed-once':True})
    db.execute('INSERT INTO apple_operations VALUES (?,?,?,?,?,?,?,?)',(action,'item',self.task['id'],action,'{}','pending','','2026-11-01'))
   self.denied(lambda:svc.apple_begin_operation(action,{},svc.get_todo(self.task['id'])['apple']))
   self.denied(lambda:svc.apple_finish_operation(action))
  class Trap:
   reads=0
   def _connect(inner):
    inner.reads+=1
    db=sqlite3.connect(':memory:');db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT)')
    db.set_authorizer(lambda action,*_: sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION) else sqlite3.SQLITE_DENY)
    return db
   continue_approved_delete=None
   finish_approved_delete=None
   def _set(inner,key,value):
    self.assertEqual(key,'operation_counts');self.assertEqual(value,{'deleted_remote':0,'exported':0})
   def __getattr__(self,key):raise AssertionError('No transport or sync mutation expected: '+key)
  trap=Trap();result=process_operations(trap,svc,'calendar');self.assertEqual(trap.reads,1);self.assertEqual(result[0],0);self.assertEqual(len(result[1]),3)
  self.assertEqual(self.b.tasks.get_todo(self.task['id'])['id'],self.task['id'])
 def test_native_transport_does_not_launch_without_private_conditional_contract(self):
  bridge=AppleCalendarBridge(helper_path='/synthetic/helper-not-executed',platform='darwin')
  bridge._start=lambda *a,**k: self.fail('native helper launched')
  for op in ['delete','delete_calendar','reminder_delete','reminder_list_delete']:
   with self.assertRaisesRegex(BridgeError,'人工确认') as caught:bridge.call(op,confirm=True,allowed_once=True)
   self.assertTrue(caught.exception.native_no_side_effects)
 def test_normal_updates_archive_undo_and_rule_config_removal_still_work(self):
  self.b.tasks.update_todo(self.task['id'],{'title':'Updated'})
  self.b.calendar.update_event(self.event['id'],{'title':'Updated event'})
  self.b.bills.update_ledger(self.bill['id'],{'name':'Updated bill'},expected_revision=self.bill['revision'])
  row=next(x for x in self.b.records() if x['id']==self.task['id'])
  hidden=self.b.archive(row['id'],row['revision'],True);self.assertTrue(hidden['archived'])
  shown=self.b.archive(row['id'],hidden['revision'],False);self.assertFalse(shown['archived'])
  rule=self.b.briefing.create_rule({'text':'Synthetic preference'});self.b.briefing.delete_rule(rule.id)
  self.assertIsNone(self.b.briefing.get_rule(rule.id))
  rule=self.b.briefing.create_action_rule({'source':'synthetic','account_id':'synthetic','information_type':'assignment','kind':'todo','mode':'suggest'})
  self.b.briefing.delete_action_rule(rule['id']);self.assertEqual(self.b.briefing.list_action_rules(),[])

if __name__=='__main__':unittest.main()
