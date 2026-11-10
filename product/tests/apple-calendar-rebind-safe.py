"""Synthetic calendar ID rebind evidence and crash tests; no Apple or Profile."""
import copy,json,os,runpy,subprocess,sys,threading,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
M=runpy.run_path(str(ROOT/'product/tests/apple-contracts.py'))
class Rebind(unittest.TestCase):
 def setUp(self):
  self.c=M['Contracts']('test_identity_reset_rebinds_without_changing_local_ids_or_losing_notes');self.c.setUp();self.bridge=M['NativeBridge']('calendar');self.sync=self.c.calendar(self.bridge)
  self.sync.start_sync();self.mapping=self.sync._maps()[0];self.id=self.mapping['alcor_id'];self.owner=self.c.business.calendar
  self.owner.update_event(self.id,{'description':'synthetic local note'})
  self.bridge.sources[0]['id']='new-group';self.bridge.rows[0].update(calendar_id='new-group',event_id='new-event')
 def tearDown(self):self.c.tearDown()
 def test_unique_external_id_and_occurrence_rebinds_without_changing_local(self):
  result=self.sync.sync();self.assertEqual(result['status'],'success',result)
  self.assertEqual(self.owner.get_event(self.id)['description'],'synthetic local note')
  self.assertEqual(self.owner.get_event(self.id)['apple']['group_id'],'new-group')
  self.assertEqual(self.sync._maps()[0]['event_id'],'new-event')
  with self.sync._connect() as db:self.assertEqual(db.execute('SELECT state FROM calendar_rebind_intents').fetchone()[0],'committed')
 def test_duplicate_native_identity_is_not_guessed(self):
  self.bridge.rows.append({**self.bridge.rows[0],'event_id':'another-event'})
  result=self.sync.sync();self.assertNotEqual(result['status'],'success')
  self.assertEqual(self.owner.get_event(self.id)['apple']['group_id'],'a');self.assertEqual(self.sync._maps()[0]['calendar_id'],'a')
  self.assertTrue(result.get('status') in ('error','partial'))
  self.assertEqual(self.sync.status()['scan_claims'],[])
  self.assertTrue(self.sync._get('category_conflicts'))
 def test_duplicate_external_identity_in_other_calendar_same_source_is_ambiguous(self):
  self.bridge.sources.append({**self.bridge.sources[0],'id':'other-group','title':'Other'})
  self.bridge.rows.append({**self.bridge.rows[0],'calendar_id':'other-group','event_id':'duplicate-elsewhere'})
  before=copy.deepcopy(self.owner.get_event(self.id));maps=copy.deepcopy(self.sync._maps());self.bridge.calls.clear()
  result=self.sync.sync();self.assertNotEqual(result['status'],'success')
  self.assertEqual(self.owner.get_event(self.id)['apple']['group_id'],'a');self.assertEqual(self.sync._maps()[0]['calendar_id'],'a')
  for _ in range(2):
   status=self.sync.status();self.assertEqual(status['scan_claims'],[])
   self.assertTrue(self.sync._get('category_conflicts'))
   self.assertEqual(len(status['identity_candidates']),1);candidate=status['identity_candidates'][0]
   self.assertEqual(candidate['state'],'pending');self.assertEqual(candidate['native']['event_id'],'duplicate-elsewhere')
   self.assertEqual(candidate['mapping_ids'],[self.id]);self.assertEqual(self.sync._maps(),maps)
   self.assertEqual(self.owner.get_event(self.id),before)
   self.assertFalse(any(op in ('upsert','delete','create_calendar','delete_calendar') for op,_ in self.bridge.calls))
   self.assertNotEqual(self.sync.sync()['status'],'success')
 def test_incomplete_other_calendar_scan_cannot_prove_uniqueness(self):
  self.bridge.sources.append({**self.bridge.sources[0],'id':'other-group','title':'Other'})
  old=self.bridge.call
  def incomplete(op,**kwargs):
   result=old(op,**kwargs)
   if op=='events' and 'other-group' in kwargs.get('calendar_ids',[]):
    result['successful_calendar_ids']=[x for x in result['successful_calendar_ids'] if x!='other-group'];result['failed_calendar_ids']=['other-group']
   return result
  self.bridge.call=incomplete;result=self.sync.sync();self.assertNotEqual(result['status'],'success')
  self.assertEqual(self.owner.get_event(self.id)['apple']['group_id'],'a');self.assertEqual(self.sync._maps()[0]['calendar_id'],'a')
 def test_other_source_same_name_not_auto_rebound(self):
  self.bridge.sources[0]['source_id']='another-account';self.bridge.rows[0]['source_id']='another-account'
  result=self.sync.sync();self.assertNotEqual(result['status'],'success')
  self.assertEqual(self.owner.get_event(self.id)['apple']['group_id'],'a');self.assertEqual(self.sync._maps()[0]['calendar_id'],'a')
 def _edit_during_scan(self,recreate=False):
  ready,release=threading.Event(),threading.Event();old=self.bridge.call
  def paused(op,**kwargs):
   if op=='events' and kwargs.get('calendar_ids')==['new-group']:ready.set();assert release.wait(5)
   return old(op,**kwargs)
  self.bridge.call=paused
  with ThreadPoolExecutor(max_workers=1) as pool:
   f=pool.submit(self.sync.sync);self.assertTrue(ready.wait(5))
   if recreate:
    self.owner.apple_remove_item(self.id);self.owner.create_event({'id':self.id,'title':'synthetic replacement','start_at':'2026-11-07T01:00:00Z','end_at':'2026-11-07T02:00:00Z'})
   else:self.owner.update_event(self.id,{'description':'synthetic later note'})
   release.set();result=f.result(timeout=8)
  self.assertNotEqual(result['status'],'success')
  self.assertEqual(self.owner.get_event(self.id)['title' if recreate else 'description'],'synthetic replacement' if recreate else 'synthetic later note')
  self.assertEqual(self.sync._maps()[0]['calendar_id'],'a');self.assertTrue(self.sync.status()['scan_claims'])
 def test_concurrent_user_edit_preserved(self):self._edit_during_scan()
 def test_same_id_recreated_during_scan_not_rebound(self):self._edit_during_scan(True)
 def test_owner_commit_then_manager_failure_preserves_evidence_and_holds(self):
  with patch('apple_sync.calendar_rebind._finish',side_effect=RuntimeError('synthetic manager interruption')):
   result=self.sync.sync()
  self.assertNotEqual(result['status'],'success')
  self.assertEqual(self.owner.get_event(self.id)['apple']['group_id'],'new-group')
  self.assertEqual(self.sync._maps()[0]['calendar_id'],'a')
  with self.sync._connect() as db:self.assertEqual(db.execute('SELECT state FROM calendar_rebind_intents').fetchone()[0],'committing')
  self.assertEqual(len(self.sync.status()['scan_claims']),2)
  again=self.sync.sync();self.assertNotEqual(again['status'],'success')
  self.assertEqual(self.sync._maps()[0]['calendar_id'],'a')
 def test_rebind_group_claim_blocks_other_process_reservation(self):
  ready,release=threading.Event(),threading.Event();old=self.bridge.call
  def paused(op,**kwargs):
   if op=='events' and kwargs.get('calendar_ids')==['new-group']:ready.set();assert release.wait(5)
   return old(op,**kwargs)
  self.bridge.call=paused
  with ThreadPoolExecutor(max_workers=1) as pool:
   f=pool.submit(self.sync.sync);self.assertTrue(ready.wait(5))
   approved={'operation_id':'a'*64,'approval_fingerprint':'f'*64,'plan':{'items':[{'local_id':self.id,'group_id':'new-group','source_id':'account1','destination_snapshot':{'id':'new-group','source_id':'account1'}}]}}
   code='''import json,sys\nfrom apple_sync.calendar import AppleCalendarSync\nfrom apple_sync.export_reservations import reserve\ns=AppleCalendarSync(bridge=object(),db_path=sys.argv[1],calendar_port=object())\ntry: reserve(s,json.load(sys.stdin)); print("reserved")\nexcept ValueError: print("blocked")\n'''
   env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'packages/alcor-apple-sync/python')
   child=subprocess.run([sys.executable,'-B','-c',code,str(self.sync.db_path)],input=json.dumps(approved),capture_output=True,text=True,env=env,check=True)
   self.assertEqual(child.stdout.strip(),'blocked');release.set();self.assertEqual(f.result(timeout=8)['status'],'success')
 def _committed_doc(self):
  with self.sync._connect() as db:row=db.execute('SELECT * FROM calendar_rebind_intents').fetchone()
  return json.loads(row['document'])
 def test_receipt_replay_does_not_overwrite_later_user_edit(self):
  self.assertEqual(self.sync.sync()['status'],'success');doc=self._committed_doc()
  self.owner.update_event(self.id,{'description':'synthetic later edit'})
  result=self.owner.apple_commit_calendar_rebind(doc['operation_id'],doc['owner_expected'],doc['new_category'],doc['native_items'])
  self.assertTrue(result['replayed']);self.assertEqual(self.owner.get_event(self.id)['description'],'synthetic later edit')
 def test_receipt_replay_does_not_touch_recreated_id(self):
  self.assertEqual(self.sync.sync()['status'],'success');doc=self._committed_doc()
  self.owner.apple_remove_item(self.id);self.owner.create_event({'id':self.id,'title':'synthetic new generation','start_at':'2026-11-07T01:00:00Z','end_at':'2026-11-07T02:00:00Z'})
  with self.assertRaisesRegex(ValueError,'incarnation|conflicts'):self.owner.apple_commit_calendar_rebind(doc['operation_id'],doc['owner_expected'],doc['new_category'],doc['native_items'])
  self.assertEqual(self.owner.get_event(self.id)['title'],'synthetic new generation')
 def test_owner_failure_before_commit_preserves_all_old_state_and_evidence(self):
  with patch.object(self.owner,'apple_commit_calendar_rebind',side_effect=RuntimeError('synthetic before commit')):
   result=self.sync.sync()
  self.assertNotEqual(result['status'],'success')
  self.assertEqual(self.owner.get_event(self.id)['apple']['group_id'],'a');self.assertEqual(self.sync._maps()[0]['calendar_id'],'a')
  with self.sync._connect() as db:self.assertEqual(db.execute('SELECT state FROM calendar_rebind_intents').fetchone()[0],'committing')
  self.assertEqual(len(self.sync.status()['scan_claims']),2)
 def test_source_changes_after_scan_never_rebinds(self):
  old=self.bridge.call;seen=[False]
  def change(op,**kwargs):
   value=old(op,**kwargs)
   if op=='events' and kwargs.get('calendar_ids')==['new-group'] and not seen[0]:
    seen[0]=True;self.bridge.sources[0]['source_id']='other-account';self.bridge.rows[0]['source_id']='other-account'
   return value
  self.bridge.call=change;result=self.sync.sync();self.assertNotEqual(result['status'],'success')
  self.assertEqual(self.owner.get_event(self.id)['apple']['group_id'],'a');self.assertEqual(self.sync._maps()[0]['calendar_id'],'a')
 def test_manager_change_after_owner_commit_never_overwrites_changed_mapping(self):
  import apple_sync.calendar_rebind as rebind
  old=rebind._finish
  def change(sync,doc,receipt):
   with sync._connect() as db:db.execute("UPDATE mappings SET conflict='synthetic concurrent change' WHERE id=?",(self.mapping['id'],))
   return old(sync,doc,receipt)
  with patch.object(rebind,'_finish',side_effect=change):result=self.sync.sync()
  self.assertNotEqual(result['status'],'success');self.assertEqual(self.sync._maps()[0]['conflict'],'synthetic concurrent change')
  self.assertEqual(self.sync._maps()[0]['calendar_id'],'a');self.assertEqual(self.owner.get_event(self.id)['apple']['group_id'],'new-group')
  self.assertEqual(len(self.sync.status()['scan_claims']),2)
 def test_recreated_after_owner_commit_never_receives_old_mapping(self):
  import apple_sync.calendar_rebind as rebind
  old=rebind._finish
  def recreate(sync,doc,receipt):
   self.owner.apple_remove_item(self.id)
   self.owner.create_event({'id':self.id,'title':'synthetic post-commit replacement','start_at':'2026-11-07T01:00:00Z','end_at':'2026-11-07T02:00:00Z'})
   return old(sync,doc,receipt)
  with patch.object(rebind,'_finish',side_effect=recreate): result=self.sync.sync()
  self.assertNotEqual(result['status'],'success',result)
  self.assertEqual(self.owner.get_event(self.id)['title'],'synthetic post-commit replacement')
  again=self.sync.sync();self.assertNotEqual(again['status'],'success',again)
  self.assertEqual(self.owner.get_event(self.id)['title'],'synthetic post-commit replacement')
  self.assertTrue(self.sync._maps()[0].get('conflict'))
 def test_missing_delete_owner_cas_preserves_later_edits_and_recreated_ids(self):
  self.assertEqual(self.sync.sync()['status'],'success')
  missing=self.owner.apple_capture_calendar_missing(self.id);snap=missing['capture'];marker=self.sync._get('calendar-binding-generation:'+self.id)
  self.owner.update_event(self.id,{'description':'synthetic concurrent edit'})
  with self.assertRaisesRegex(ValueError,'INBOUND_CHANGED'):self.owner.apple_remove_calendar_inbound(snap,'account1','new-group','new-event',marker['generation'],missing['deletion_snapshot'])
  self.assertEqual(self.owner.get_event(self.id)['description'],'synthetic concurrent edit')
  missing=self.owner.apple_capture_calendar_missing(self.id);snap=missing['capture']
  self.owner.apple_remove_item(self.id);self.owner.create_event({'id':self.id,'title':'synthetic replacement','start_at':'2026-11-07T01:00:00Z','end_at':'2026-11-07T02:00:00Z'})
  with self.assertRaisesRegex(ValueError,'INBOUND_CHANGED'):self.owner.apple_remove_calendar_inbound(snap,'account1','new-group','new-event',marker['generation'],missing['deletion_snapshot'])
  self.assertEqual(self.owner.get_event(self.id)['title'],'synthetic replacement')
 def test_missing_delete_owner_cas_archives_unchanged_target(self):
  self.assertEqual(self.sync.sync()['status'],'success')
  missing=self.owner.apple_capture_calendar_missing(self.id);snap=missing['capture'];marker=self.sync._get('calendar-binding-generation:'+self.id)
  with self.assertRaisesRegex(ValueError,'INBOUND_CHANGED'):self.owner.apple_remove_calendar_inbound(snap,'other','new-group','new-event',marker['generation'],missing['deletion_snapshot'])
  self.assertTrue(self.owner.apple_remove_calendar_inbound(snap,'account1','new-group','new-event',marker['generation'],missing['deletion_snapshot']))
  with self.assertRaises(KeyError):self.owner.get_event(self.id)
  self.assertTrue(self.owner.list_apple_inbound_recoveries())
 def test_missing_after_rebind_recreated_same_fingerprint_is_preserved(self):
  from apple_sync.calendar import _fingerprint
  import apple_sync.calendar_rebind as rebind
  self.sync.clock=lambda:'2026-11-07T00:00:00+00:00'
  self.sync._set('sync_deletions',True)
  with self.sync._connect() as db:db.execute("UPDATE mappings SET missing_since='2026-11-06T00:00:00+00:00' WHERE id=?",(self.mapping['id'],))
  old_finish=rebind._finish;old_call=self.bridge.call
  def call(op,**kwargs):
   if op=='lookup' and not self.bridge.rows:return {'event':None}
   return old_call(op,**kwargs)
  self.bridge.call=call
  def recreate(sync,doc,receipt):
   old=self.owner.get_event(self.id)
   self.owner.apple_remove_item(self.id)
   self.owner.create_event({k:old[k] for k in ('id','title','description','start_at','end_at','all_day','location','category_id','repeat_frequency','repeat_days','repeat_until','repeat_timezone')})
   replacement=self.owner.get_event(self.id)
   self.assertEqual(_fingerprint(old),_fingerprint(replacement))
   self.bridge.rows=[]
   return old_finish(sync,doc,receipt)
  with patch.object(rebind,'_finish',side_effect=recreate):result=self.sync.sync()
  self.assertEqual(result['deleted_local'],0,result)
  self.assertEqual(self.sync._maps()[0]['state'],'conflict')
  self.assertEqual(self.owner.get_event(self.id)['title'],'Remote event')
 def test_missing_after_rebind_requires_deletion_evidence_even_if_local_unchanged(self):
  self.sync.clock=lambda:'2026-11-07T00:00:00+00:00'
  self.assertEqual(self.sync.sync()['status'],'success')
  self.sync._set('sync_deletions',True)
  with self.sync._connect() as db:db.execute("UPDATE mappings SET missing_since='2026-11-06T00:00:00+00:00' WHERE alcor_id=?",(self.id,))
  self.bridge.rows=[];old=self.bridge.call
  self.bridge.call=lambda op,**kwargs: {'event':None} if op=='lookup' else old(op,**kwargs)
  result=self.sync.sync();self.assertEqual(result['deleted_local'],0,result)
  self.assertTrue(self.owner.get_event(self.id))
  self.assertEqual(self.sync._maps()[0]['state'],'conflict')
  self.assertEqual(self.owner.list_apple_inbound_recoveries(),[])
 def test_missing_delete_edit_during_lookup_is_conflict(self):
  self.sync.clock=lambda:'2026-11-07T00:00:00+00:00'
  self.assertEqual(self.sync.sync()['status'],'success');self.sync._set('sync_deletions',True)
  with self.sync._connect() as db:db.execute("UPDATE mappings SET missing_since='2026-11-06T00:00:00+00:00' WHERE alcor_id=?",(self.id,))
  self.bridge.rows=[];old=self.bridge.call; edited=[False]
  def call(op,**kwargs):
   if op=='lookup':
    if not edited[0]:self.owner.update_event(self.id,{'description':'synthetic concurrent local edit'});edited[0]=True
    return {'event':None}
   return old(op,**kwargs)
  self.bridge.call=call;result=self.sync.sync()
  self.assertEqual(result['deleted_local'],0,result);self.assertEqual(self.sync._maps()[0]['state'],'conflict')
  self.assertEqual(self.owner.get_event(self.id)['description'],'synthetic concurrent local edit')
 def test_missing_delete_dependency_change_is_not_lost(self):
  self.assertEqual(self.sync.sync()['status'],'success')
  missing=self.owner.apple_capture_calendar_missing(self.id);snap=missing['capture'];marker=self.sync._get('calendar-binding-generation:'+self.id)
  with self.owner._connect() as db:db.execute('INSERT INTO calendar_occurrence_exclusions VALUES (?,?)',(self.id,'2026-11-08'))
  with self.assertRaisesRegex(ValueError,'INBOUND_CHANGED'):self.owner.apple_remove_calendar_inbound(snap,'account1','new-group','new-event',marker['generation'],missing['deletion_snapshot'])
  self.assertTrue(self.owner.get_event(self.id))
  with self.owner._connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM calendar_occurrence_exclusions WHERE series_id=?',(self.id,)).fetchone()[0],1)
 def test_missing_without_legacy_marker_requires_reconciliation(self):
  self.sync.clock=lambda:'2026-11-07T00:00:00+00:00'
  self.assertEqual(self.sync.sync()['status'],'success');self.sync._set('sync_deletions',True)
  with self.sync._connect() as db:
   db.execute("UPDATE mappings SET missing_since='2026-11-06T00:00:00+00:00' WHERE alcor_id=?",(self.id,))
   db.execute('DELETE FROM settings WHERE key=?',('calendar-binding-generation:'+self.id,))
  self.bridge.rows=[];old=self.bridge.call
  self.bridge.call=lambda op,**kwargs: {'event':None} if op=='lookup' else old(op,**kwargs)
  result=self.sync.sync();self.assertEqual(result['deleted_local'],0,result)
  self.assertEqual(self.sync._maps()[0]['state'],'conflict');self.assertTrue(self.owner.get_event(self.id))
 def test_private_rebind_owner_methods_not_public_business_commands(self):
  for method in ('apple_prepare_calendar_rebind','apple_commit_calendar_rebind','apple_capture_calendar_missing','apple_remove_calendar_inbound'):
   with self.assertRaises((ValueError,KeyError,TypeError)):self.c.business.invoke('calendar',method,{})
if __name__=='__main__':unittest.main(verbosity=2)
