"""Local-only import receipt cleanup after controlled independent process exits."""
import copy,json,os,runpy,subprocess,sys,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-import-unlink-run.py')))
from apple_sync.approved_delete import run
from apple_sync.group_scan_claims import WorkClaim

class UnlinkRecovery(unittest.TestCase):
 def exit_at(self,s,o,b,plan,point):
  path=s.db_path.parent/'unlink-child.json';path.write_text(json.dumps({'manager':str(s.db_path),'owner':str(o.db_path),'plan':plan,'point':point,'rows':b.rows,'sources':b.sources}))
  child=subprocess.run([sys.executable,'-B',__file__,'--child',str(path)],capture_output=True,text=True)
  self.assertEqual(child.returncode,23,child.stderr)
 def test_committed_receipt_finishes_after_restart_without_owner_or_native_write(self):
  helper=M['ImportUnlink']()
  for kind in ('calendar','reminders'):
   for point in ('after_owner','after_manager'):
    with self.subTest(kind=kind,point=point),M['M']['case'](kind) as (s,o,b):
     plan,commit,link=helper.setup(s,o,b,kind);native=copy.deepcopy(b.rows);self.exit_at(s,o,b,plan,point)
     self.assertTrue(s.status()['scan_claims']);b.calls.clear()
     s=type(s)(bridge=b,db_path=s.db_path,**({'calendar_port':o} if kind=='calendar' else {'todo_port':o}),clock=s.clock);s.start_auto=lambda:None
     with patch('business.import_category_delete.commit',side_effect=AssertionError('receipt cleanup cannot delete again')):
      s.sync();s.sync();s.disconnect();s.start_sync()
     self.assertFalse(s.status()['scan_claims']);self.assertFalse(any(r['apple_id']==link['apple_id'] for r in s.status()['category_conflicts']));self.assertFalse(any(r['alcor_id']==link['alcor_id'] for r in s._stored_links() if r['enabled']))
     self.assertFalse(any(r['id']==link['alcor_id'] for r in o.categories()));self.assertEqual(len(s._maps()),1)
     self.assertEqual(s._get('approved-delete:'+plan['operation_id'])['state'],'done')
     self.assertEqual(b.rows,native);self.assertFalse(helper.writes(b));self.assertEqual(len(o.list_apple_inbound_recoveries()),1)
     b.rows[1]['title']='Healthy after local cleanup';s.sync()
     self.assertEqual((o.list_events() if kind=='calendar' else o.list_todos())[0]['title'],'Healthy after local cleanup')
 def test_missing_receipt_stays_held_and_does_not_repeat_owner_action(self):
  helper=M['ImportUnlink']()
  for kind in ('calendar','reminders'):
   with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
    plan,commit,link=helper.setup(s,o,b,kind);before=copy.deepcopy(s._maps());self.exit_at(s,o,b,plan,'before_owner');b.calls.clear()
    with patch('business.import_category_delete.commit',side_effect=AssertionError('unknown cannot retry')):s.sync();s.sync()
    self.assertTrue(s.status()['scan_claims']);self.assertEqual([r for r in s._maps() if r['calendar_id' if kind=='calendar' else 'list_id']=='a'],[r for r in before if r['calendar_id' if kind=='calendar' else 'list_id']=='a']);self.assertTrue(o.get_category(link['alcor_id']));self.assertFalse(helper.writes(b))
 def test_changed_evidence_or_recreated_target_never_cleans_old_mapping(self):
  helper=M['ImportUnlink']()
  for kind in ('calendar','reminders'):
   for change in ('claim','mapping','link','receipt','item'):
    with self.subTest(kind=kind,change=change),M['M']['case'](kind) as (s,o,b):
     plan,commit,link=helper.setup(s,o,b,kind);item=next(r for r in plan['local_plan']['targets'] if not r.get('category'))
     self.exit_at(s,o,b,plan,'after_owner')
     if change=='claim':
      with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
     elif change=='mapping':
      with s._connect() as db:db.execute("UPDATE mappings SET conflict='later choice' WHERE "+('calendar_id' if kind=='calendar' else 'list_id')+"='a'")
     elif change=='link':
      with s._connect() as db:db.execute("UPDATE category_links SET direction='export' WHERE apple_id='a'")
     elif change=='receipt':
      with o._connect() as db:
       row=db.execute('SELECT target_id FROM alcor_security_receipts WHERE operation_id=?',(plan['operation_id'],)).fetchone();doc=json.loads(row[0]);doc['_snapshot']='0'*64
       db.execute('UPDATE alcor_security_receipts SET target_id=? WHERE operation_id=?',(json.dumps(doc),plan['operation_id']))
     else:
      restored=o.restore_apple_inbound_recovery(o.list_apple_inbound_recoveries()[0]['id'])
      data={'id':item['id'],'title':'Recreated local item','category_id':restored['categoryIds'][0]}
      if kind=='calendar':o.create_event({**data,'start_at':'2026-11-09T01:00:00Z','end_at':'2026-11-09T02:00:00Z'})
      else:o.create_todo(data)
     before=copy.deepcopy(s._maps());b.calls.clear();s.sync();s.sync()
     self.assertEqual([r for r in s._maps() if r['calendar_id' if kind=='calendar' else 'list_id']=='a'],[r for r in before if r['calendar_id' if kind=='calendar' else 'list_id']=='a']);self.assertTrue(s.status()['scan_claims']);self.assertFalse(helper.writes(b))
     if change=='item':self.assertEqual(s._local(item['id'])['title'],'Recreated local item')

 def test_postcommit_value_error_keeps_link_disabled_until_receipt_cleanup(self):
  helper=M['ImportUnlink']()
  for kind in ('calendar','reminders'):
   with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
    plan,commit,link=helper.setup(s,o,b,kind)
    def lost(action,**kw):commit(action,**kw);raise ValueError('postcommit response error')
    with self.assertRaisesRegex(ValueError,'postcommit'):run(s,plan,lambda *a:True,lost)
    self.assertFalse(next(r for r in s._stored_links() if r['apple_id']=='a')['enabled'])
    s.sync();self.assertFalse(s.status()['scan_claims']);self.assertFalse(helper.writes(b))
 def test_readonly_owner_peer_verifies_only_the_exact_receipt(self):
  import importlib
  from business.worker import Business
  helper=M['ImportUnlink']()
  for kind in ('calendar','reminders'):
   with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
    plan,commit,link=helper.setup(s,o,b,kind);self.exit_at(s,o,b,plan,'after_owner')
    domain=plan['domain'];business=object.__new__(Business);setattr(business,domain,o);business.entries={domain:importlib.import_module('business.'+domain+'.entry')}
    args=[plan['operation_id'],link['alcor_id'],plan['local_plan']['snapshot']]
    self.assertEqual(business.apple_peer(domain,'apple_verify_import_unlink',args,{})['state'],'done')
    with self.assertRaises(ValueError):business.apple_peer(domain,'apple_verify_import_unlink',[*args[:2],'0'*64],{})
    with self.assertRaises(ValueError):business.apple_peer(domain,'commit_import_category_delete',args,{})
    with self.assertRaises(ValueError):business.invoke(domain,'apple_verify_import_unlink',{})
    self.assertTrue(s.status()['scan_claims']);self.assertFalse(helper.writes(b))

 def test_receipt_cleanup_fences_a_late_original_manager(self):
  helper=M['ImportUnlink']()
  for kind in ('calendar','reminders'):
   with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
    plan,commit,link=helper.setup(s,o,b,kind);path=s.db_path.parent/'unlink-pause.json'
    path.write_text(json.dumps({'manager':str(s.db_path),'owner':str(o.db_path),'plan':plan,'point':'pause_after_owner','rows':b.rows,'sources':b.sources}))
    child=subprocess.Popen([sys.executable,'-B',__file__,'--child',str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
     self.assertEqual(child.stdout.readline().strip(),'owner-committed');s.sync()
     self.assertEqual(s._get('approved-delete:'+plan['operation_id'])['state'],'done');self.assertFalse(s.status()['scan_claims'])
    finally:out,err=child.communicate('continue\n',timeout=10)
    self.assertEqual(child.returncode,1,err);self.assertIn('manager operation claim or evidence changed',err)
    self.assertEqual(s._get('approved-delete:'+plan['operation_id'])['state'],'done');self.assertFalse(s.status()['scan_claims']);self.assertFalse(helper.writes(b))

def child(path):
 data=json.loads(Path(path).read_text());plan=data['plan'];kind=plan['kind'];point=data['point']
 from business.calendar.domain.service import CalendarService
 from business.tasks.domain.service import TodoService
 from apple_sync.calendar import AppleCalendarSync
 from apple_sync.reminders import AppleReminderSync
 contracts=runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')));b=contracts['NativeBridge'](kind);b.rows=data['rows'];b.sources=data['sources']
 o=(CalendarService if kind=='calendar' else TodoService)(data['owner']);s=(AppleCalendarSync if kind=='calendar' else AppleReminderSync)(bridge=b,db_path=data['manager'],**({'calendar_port':o} if kind=='calendar' else {'todo_port':o}))
 def commit(action,**kw):
  if point=='before_owner':os._exit(23)
  result=M['owner_commit'](o,plan['domain'],**kw)
  if point=='after_owner':os._exit(23)
  if point=='pause_after_owner':print('owner-committed',flush=True);sys.stdin.readline()
  return result
 original=WorkClaim.__exit__
 def leave(claim,error_type,*args):
  if point=='after_manager' and error_type is None:os._exit(23)
  return original(claim,error_type,*args)
 with patch.object(WorkClaim,'__exit__',leave):run(s,plan,lambda *a:True,commit)
 raise AssertionError('exit point not reached')

if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='--child':child(sys.argv[2])
 else:unittest.main(verbosity=2)
