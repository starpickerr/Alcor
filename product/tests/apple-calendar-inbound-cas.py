"""Synthetic real calendar owner/sync interleavings; no native Apple or Profile."""
import json,runpy,threading,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
M=runpy.run_path(str(ROOT/'product/tests/apple-contracts.py'))
class Inbound(unittest.TestCase):
 def setUp(self):
  self.case=M['Contracts']('test_identity_reset_rebinds_without_changing_local_ids_or_losing_notes');self.case.setUp()
  self.bridge=M['NativeBridge']('calendar');self.sync=self.case.calendar(self.bridge)
  self.sync.start_sync();self.mapping=self.sync._maps()[0];self.id=self.mapping['alcor_id']
 def tearDown(self):self.case.tearDown()
 def conflict_race(self,recreate=False):
  self.bridge.rows[0]['title']='synthetic remote changed';ready=threading.Event();release=threading.Event()
  old=self.case.business.calendar.apple_apply_calendar_inbound
  def pause(*a,**kw):ready.set();assert release.wait(4);return old(*a,**kw)
  self.case.business.calendar.apple_apply_calendar_inbound=pause
  with ThreadPoolExecutor(max_workers=1) as pool:
   future=pool.submit(self.sync.sync);self.assertTrue(ready.wait(4))
   if recreate:
    self.case.business.calendar.apple_remove_item(self.id)
    self.case.business.calendar.create_event({'id':self.id,'title':'synthetic replacement','start_at':'2026-11-07T01:00:00Z','end_at':'2026-11-07T02:00:00Z'})
   else:self.case.business.calendar.update_event(self.id,{'title':'synthetic local edit'})
   release.set();result=future.result(timeout=5)
  self.assertEqual(result['status'],'partial',result)
  self.assertEqual(self.case.business.calendar.get_event(self.id)['title'],'synthetic replacement' if recreate else 'synthetic local edit')
  mapping=self.sync._maps()[0];self.assertEqual(mapping['state'],'conflict')
  record=self.sync._get('calendar-inbound-conflict:'+self.id)
  self.assertEqual(record['remote']['title'],'synthetic remote changed')
  self.assertEqual(record['current_local']['title'],'synthetic replacement' if recreate else 'synthetic local edit')
 def test_local_edit_during_inbound_is_preserved_and_reported(self):self.conflict_race()
 def test_recreated_same_id_is_not_overwritten(self):self.conflict_race(True)
 def test_uncontended_remote_change_updates_and_sets_consistent_baseline(self):
  self.bridge.rows[0]['title']='synthetic remote changed';result=self.sync.sync()
  self.assertEqual(result['status'],'success',result)
  self.assertEqual(self.case.business.calendar.get_event(self.id)['title'],'synthetic remote changed')
  self.assertEqual(self.sync._maps()[0]['state'],'active')
 def test_missing_metadata_baseline_is_seeded_without_content_loss(self):
  self.sync._set('item-metadata:'+self.id,None)
  old=self.case.business.calendar.get_event(self.id)['title'];result=self.sync.sync()
  self.assertEqual(result['status'],'success',result)
  self.assertEqual(self.case.business.calendar.get_event(self.id)['title'],old)
  self.assertIsInstance(self.sync._get('item-metadata:'+self.id),list)
 def test_alcor_origin_concurrent_edit_is_preserved(self):
  local=self.case.business.calendar.create_event({'title':'synthetic local origin','start_at':'2026-11-07T01:00:00Z','end_at':'2026-11-07T02:00:00Z'})
  exports=runpy.run_path(str(ROOT/'product/tests/apple-approved-export.py'))
  bridge=exports['ConditionalBridge']('calendar');bridge.rows=self.bridge.rows;bridge.sources=self.bridge.sources
  self.bridge=bridge;self.sync.bridge=bridge
  from apple_sync.approved_export import prepare,run
  plan=prepare(self.sync,'calendar','one_time',{'kind':'calendar','direction':'export','calendar_id':'a','item_ids':[local['id']]},'a'*64)
  self.assertEqual(run(self.sync,'calendar',plan,lambda *args:True)['status'],'success')
  mapping=next(row for row in self.sync._maps() if row['alcor_id']==local['id'])
  remote=next(row for row in self.bridge.rows if row['event_id']==mapping['event_id']);remote['title']='synthetic remote change'
  ready,release=threading.Event(),threading.Event();old=self.case.business.calendar.apple_apply_calendar_inbound
  def pause(expected,*args,**kwargs):
   if expected['local_id']==local['id']:ready.set();assert release.wait(4)
   return old(expected,*args,**kwargs)
  self.case.business.calendar.apple_apply_calendar_inbound=pause
  with ThreadPoolExecutor(max_workers=1) as pool:
   f=pool.submit(self.sync.sync);self.assertTrue(ready.wait(4));self.case.business.calendar.update_event(local['id'],{'title':'synthetic local later'});release.set();result=f.result(timeout=5)
  self.assertEqual(result['status'],'partial',result)
  self.assertEqual(self.case.business.calendar.get_event(local['id'])['title'],'synthetic local later')
  self.assertEqual(next(row for row in self.sync._maps() if row['alcor_id']==local['id'])['state'],'conflict')
 def test_local_edit_after_owner_commit_remains_detectably_unsynced(self):
  self.bridge.rows[0]['title']='synthetic remote changed';ready,release=threading.Event(),threading.Event();old=self.sync._commit_inbound_snapshot
  def pause(*args,**kwargs):ready.set();assert release.wait(4);return old(*args,**kwargs)
  self.sync._commit_inbound_snapshot=pause
  with ThreadPoolExecutor(max_workers=1) as pool:
   f=pool.submit(self.sync.sync);self.assertTrue(ready.wait(4));self.case.business.calendar.update_event(self.id,{'title':'synthetic later edit'});release.set();f.result(timeout=5)
  from apple_sync.calendar import _fingerprint
  local=self.case.business.calendar.get_event(self.id);mapping=self.sync._maps()[0]
  self.assertEqual(local['title'],'synthetic later edit')
  self.assertNotEqual(_fingerprint(local,series=mapping['origin']=='alcor'),mapping['local_hash'])
 def test_category_change_during_inbound_is_detected(self):
  self.bridge.rows[0]['title']='synthetic remote changed';ready,release=threading.Event(),threading.Event();old=self.case.business.calendar.apple_apply_calendar_inbound
  def pause(*args,**kwargs):ready.set();assert release.wait(4);return old(*args,**kwargs)
  self.case.business.calendar.apple_apply_calendar_inbound=pause
  category=self.case.business.calendar.get_event(self.id)['category_id']
  with ThreadPoolExecutor(max_workers=1) as pool:
   f=pool.submit(self.sync.sync);self.assertTrue(ready.wait(4));self.case.business.calendar.update_category(category,{'name':'synthetic changed group'});release.set();result=f.result(timeout=5)
  self.assertEqual(result['status'],'partial',result)
  self.assertEqual(self.sync._maps()[0]['state'],'conflict')
 def test_external_upsert_is_private_even_with_reconciled_true(self):
  name='upsert_external_event'; args={'provider':'apple_calendar','source_key':'synthetic-forged','payload':{'title':'forged','start_at':'2026-11-07T01:00:00Z','end_at':'2026-11-07T02:00:00Z'},'event_id':self.id,'reconciled':True}
  self.assertFalse(any(row['name']==name for row in self.case.business.invoke('calendar','catalog')))
  with self.assertRaisesRegex(ValueError,'Unknown business operation'):self.case.business.invoke('calendar',name,args)
  made=self.case.business.apple_peer('calendar',name,['apple_calendar','synthetic-trusted',{'title':'synthetic peer','start_at':'2026-11-07T01:00:00Z','end_at':'2026-11-07T02:00:00Z'}])
  self.assertEqual(made['title'],'synthetic peer')
 def test_private_methods_are_not_public_business_commands(self):
  for name in ('apple_capture_calendar_sync','apple_apply_calendar_inbound'):
   with self.assertRaises((ValueError,KeyError,TypeError)):self.case.business.invoke('calendar',name,{'event_id':self.id})
  capture=self.case.business.apple_peer('calendar','apple_capture_calendar_sync',[self.id])
  self.assertEqual(capture['local_id'],self.id)
if __name__=='__main__':unittest.main(verbosity=2)
