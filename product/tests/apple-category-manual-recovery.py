"""Explicit empty-container rebuild core; native and all user data are synthetic."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))
from apple_sync.category_rebuild import preview,rebuild

class ManualCategoryRecovery(unittest.TestCase):
    def setup_missing(self,s,o,b):
        s.start_sync();link=s._stored_links()[0];item=s._maps()[0]['alcor_id' if b.kind=='calendar' else 'todo_id']
        b.sources=[];b.rows=[];b.calls.clear()
        return link,item
    def perform(self,s,o,b):
        p=preview(s,o,b.kind,'a','account1')
        return rebuild(s,o,b.kind,'a','account1',p['snapshot'])
    def test_empty_container_keeps_old_members_and_evidence_through_scans(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item=self.setup_missing(s,o,b);before=copy.deepcopy(s._local(item));maps=copy.deepcopy(s._maps())
                self.perform(s,o,b)
                for _ in range(3):s.sync()
                self.assertEqual(len(b.sources),1);self.assertEqual(len(b.rows),0)
                self.assertEqual(s._local(item),before);self.assertEqual(s._maps(),maps)
                self.assertEqual(s.status()['retained_categories'][0]['member_count'],1)
                self.assertFalse(any(op in ('upsert','reminder_upsert','delete','reminder_delete') for op,_ in b.calls))
                self.assertEqual(s._stored_links()[0]['apple_id'],b.sources[0]['id'])
    def test_new_normal_items_work_without_uploading_frozen_members(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item=self.setup_missing(s,o,b);self.perform(s,o,b)
                if kind=='calendar':new=o.create_event({'title':'New item','category_id':link['alcor_id'],'start_at':'2026-11-09T01:00:00Z','end_at':'2026-11-09T02:00:00Z'})
                else:new=o.create_todo({'title':'New item','category_id':link['alcor_id'],'due_on':'2026-11-09'})
                s.sync();s.sync()
                self.assertEqual([row['title'] for row in b.rows],['New item'])
                self.assertEqual(len(s._maps()),2);self.assertEqual(s._local(item)['title'],'Remote event' if kind=='calendar' else 'Remote task')
    def test_preview_change_rejects_before_native_write(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item=self.setup_missing(s,o,b);p=preview(s,o,kind,'a','account1')
                (o.update_event if kind=='calendar' else o.update_todo)(item,{'title':'Changed after preview'})
                with self.assertRaisesRegex(ValueError,'预览'):rebuild(s,o,kind,'a','account1',p['snapshot'])
                self.assertEqual(b.sources,[]);self.assertEqual(s._stored_links()[0]['apple_id'],'a')
    def test_manual_rebuild_respects_existing_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item=self.setup_missing(s,o,b);before=copy.deepcopy(s._stored_links());p=preview(s,o,kind,'a','account1')
                with M['M']['other_process_holds'](s,'a',item):
                    with self.assertRaises(ValueError):rebuild(s,o,kind,'a','account1',p['snapshot'])
                self.assertEqual(s._stored_links(),before);self.assertEqual(b.sources,[])
    def test_lost_rebuild_reply_does_not_create_another_category(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item=self.setup_missing(s,o,b);original=b.call;sent=[]
                def lost(op,**kw):
                    result=original(op,**kw)
                    if op in ('create_calendar','reminder_list_create'):sent.append(kw);raise RuntimeError('native reply lost after save')
                    return result
                with patch.object(b,'call',side_effect=lost):
                    for _ in range(2):
                        with self.assertRaises(ValueError):self.perform(s,o,b)
                self.assertEqual(len(sent),1);self.assertEqual(len(b.sources),1)
                self.assertEqual(s._stored_links()[0]['apple_id'],'a');self.assertIsNotNone(s._local(item))
    def test_owner_reply_lost_never_changes_member_mapping_or_repeats_native(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item=self.setup_missing(s,o,b);maps=copy.deepcopy(s._maps());original=o.apple_connect_category
                def lost(*args):original(*args);raise RuntimeError('owner reply lost')
                with patch.object(o,'apple_connect_category',side_effect=lost):
                    with self.assertRaisesRegex(RuntimeError,'owner reply lost'):self.perform(s,o,b)
                with self.assertRaises(ValueError):self.perform(s,o,b)
                self.assertEqual(len(b.sources),1);self.assertEqual(s._maps(),maps);self.assertEqual(s._stored_links()[0]['apple_id'],'a')
                self.assertTrue(s.status()['scan_claims']);self.assertIsNotNone(s._local(item))
    def test_replacement_without_item_identity_does_not_change_any_binding(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item=self.setup_missing(s,o,b);before=copy.deepcopy(s._stored_links());maps=copy.deepcopy(s._maps());local=copy.deepcopy(o.get_category(link['alcor_id']))
                b.sources=[{'id':'b','title':'Replacement','color':'#112233','source_id':'account1','source':'iCloud','writable':True,'immutable':False}]
                with self.assertRaises(ValueError):s.resolve_category_conflict('a','rebind',replacement_id='b')
                self.assertEqual(s._stored_links(),before);self.assertEqual(s._maps(),maps);self.assertEqual(o.get_category(link['alcor_id']),local)
    def test_verified_same_source_replacement_preserves_item_identity_without_native_write(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                s.start_sync();old=s._maps()[0];ident=old['alcor_id' if kind=='calendar' else 'todo_id']
                b.sources[0]['id']='b';b.rows[0]['list_id' if kind=='reminders' else 'calendar_id']='b';b.calls.clear()
                s.resolve_category_conflict('a','rebind',replacement_id='b');s.sync()
                self.assertEqual(len(s._maps()),1);self.assertEqual(s._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id'],ident)
                self.assertEqual(s._stored_links()[0]['apple_id'],'b')
                self.assertFalse(any(op in ('create_calendar','reminder_list_create','upsert','reminder_upsert') for op,_ in b.calls))

    def test_unmapped_old_member_is_retained_without_upload(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item=self.setup_missing(s,o,b)
                if kind=='calendar':old=o.create_event({'title':'Unmapped old','category_id':link['alcor_id'],'start_at':'2026-11-09T01:00:00Z','end_at':'2026-11-09T02:00:00Z'})
                else:old=o.create_todo({'title':'Unmapped old','category_id':link['alcor_id'],'due_on':'2026-11-09'})
                before=copy.deepcopy(s._local(old['id']));self.perform(s,o,b)
                for _ in range(3):s.sync()
                self.assertEqual(s._local(old['id']),before);self.assertFalse(b.rows)
                self.assertEqual(s.status()['retained_categories'][0]['member_count'],2)
                self.assertEqual(len(s._maps()),1)
    def test_manager_evidence_change_after_owner_commit_keeps_mapping_and_unknown(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item=self.setup_missing(s,o,b);maps=copy.deepcopy(s._maps());original=o.apple_connect_category
                def changed(*args):
                    result=original(*args)
                    with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                    return result
                with patch.object(o,'apple_connect_category',side_effect=changed):
                    with self.assertRaisesRegex(ValueError,'claim or evidence changed'):self.perform(s,o,b)
                self.assertEqual(s._maps(),maps);self.assertEqual(s._stored_links()[0]['apple_id'],'a')
                self.assertEqual(len(b.sources),1);self.assertTrue(s.status()['scan_claims'])
                self.assertEqual(o.get_category(link['alcor_id'])['apple']['apple_id'],b.sources[0]['id'])
                with self.assertRaises(ValueError):self.perform(s,o,b)
                self.assertEqual(len(b.sources),1)
    def test_missing_rebuilt_container_never_archives_old_members(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item=self.setup_missing(s,o,b);before=copy.deepcopy(s._local(item));self.perform(s,o,b)
                b.sources=[];b.rows=[]
                for minute in (1,3,5):
                    s.clock=lambda minute=minute:'2026-11-07T00:%02d:00Z'%minute;s.sync()
                self.assertEqual(s._local(item),before);self.assertIsNotNone(o.get_category(link['alcor_id']))
                self.assertEqual(len(s._maps()),1);self.assertTrue(s.status()['category_conflicts'])
    def test_preview_recreated_category_rejects_stale_incarnation(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item=self.setup_missing(s,o,b)
                o.apple_remove_item(item)
                with s._connect() as db:db.execute('DELETE FROM mappings')
                p=preview(s,o,kind,'a','account1')
                with o._connect() as db:
                    row=tuple(db.execute(f'SELECT * FROM {o.APPLE_CATEGORIES} WHERE id=?',(link['alcor_id'],)).fetchone())
                    db.execute(f'DELETE FROM {o.APPLE_CATEGORIES} WHERE id=?',(link['alcor_id'],))
                    db.execute(f'INSERT INTO {o.APPLE_CATEGORIES} VALUES (?,?,?,?)',row)
                with self.assertRaisesRegex(ValueError,'预览'):rebuild(s,o,kind,'a','account1',p['snapshot'])
                self.assertFalse(b.sources);self.assertIsNotNone(o.get_category(link['alcor_id']))
    def test_explicit_pre_save_rejection_releases_but_unknown_does_not(self):
        from apple_sync.bridge import BridgeError
        for kind in ('calendar','reminders'):
            for known in (True,False):
                with self.subTest(kind=kind,known=known),M['case'](kind) as (s,o,b):
                    self.setup_missing(s,o,b);original=b.call
                    def reject(op,**kw):
                        if op in ('create_calendar','reminder_list_create'):
                            raise BridgeError('native declined',native_no_side_effects=known)
                        return original(op,**kw)
                    with patch.object(b,'call',side_effect=reject):
                        with self.assertRaises((RuntimeError,ValueError)):self.perform(s,o,b)
                    self.assertFalse(b.sources)
                    self.assertEqual(bool(s.status()['scan_claims']),not known)
                    self.assertEqual(bool(s.status()['category_creates']),not known)
                    if known:
                        self.perform(s,o,b);self.assertEqual(len(b.sources),1)
                    else:
                        with self.assertRaises(ValueError):self.perform(s,o,b)
                        self.assertFalse(b.sources)
    def test_claim_change_between_stage_and_create_intent_prevents_send(self):
        from apple_sync.group_scan_claims import WorkClaim
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                self.setup_missing(s,o,b);original=WorkClaim.started
                def changed(claim,stage='sending'):
                    result=original(claim,stage)
                    if stage=='sending':
                        with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                    return result
                with patch.object(WorkClaim,'started',new=changed):
                    with self.assertRaisesRegex(ValueError,'claim or evidence changed'):self.perform(s,o,b)
                self.assertFalse(b.sources);self.assertTrue(s.status()['scan_claims'])
                self.assertTrue(s.status()['category_creates'])

    def test_ordinary_invoke_cannot_self_confirm_rebuild(self):
        from worker import AppleWorker
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                self.setup_missing(s,o,b);w=AppleWorker(s.db_path.parent/'worker',lambda request:None,bridge=b);w.managers={kind:s}
                for method in ('rebuild_category','preview_category_rebuild'):
                    with self.assertRaises(ValueError):w.invoke(method,{'kind':kind,'apple_id':'a','source_id':'account1','snapshot':'a'*64})
                with self.assertRaisesRegex(ValueError,'所有者页面'):s.resolve_category_conflict('a','keep_alcor',source_id='account1')
                self.assertFalse(b.sources)

if __name__=='__main__':unittest.main(verbosity=2)
