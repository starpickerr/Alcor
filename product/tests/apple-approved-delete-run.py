"""Real owner/manager deletion transactions, fake EventKit and live peer callback."""
import copy,json,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))
from business.apple_delete_approval import prepare as owner_prepare,commit as owner_commit
from apple_sync.approved_delete import prepare,run
from apple_sync.bridge import BridgeError

class ApprovedDelete(unittest.TestCase):
    def setup(self,s,o,b,kind,entity='item',confirmed=False):
        original=b.call
        def native(op,**payload):
            result=original(op,**payload)
            if op in ('status','reminder_status'):return {**result,'delete_write_version':1}
            if op in ('delete','reminder_delete','delete_calendar','reminder_list_delete'):
                proof={'version':1,'binding':payload['receipt_binding'],'op':op,'source_id':payload['expected_source_id'],
                       'expected_snapshot':payload['expected_snapshot'],'series':payload.get('series',False)}
                result={'deleted':True,'delete_receipt':proof}
                Path(payload['receipt_file']).write_text(json.dumps({'binding':payload['receipt_binding'],**result}))
            return result
        b.call=native;s.start_sync();b.calls.clear();domain='tasks' if kind=='reminders' else 'calendar'
        ident=s._stored_links()[0]['alcor_id'] if entity=='category' else s._maps()[0]['todo_id' if kind=='reminders' else 'alcor_id']
        method='delete_category' if entity=='category' else 'delete_todo' if kind=='reminders' else 'delete_event'
        args={('category_id' if entity=='category' else 'todo_id') if domain=='tasks' else 'id':ident}
        op='a'*64;local=owner_prepare(o,domain,method,args,op);plan=prepare(s,domain,op,local)
        owner_commit(o,domain,method,args,op,local['snapshot'],plan['native'],confirmed_intent={'version':1,'plan_fingerprint':plan['plan_fingerprint']} if confirmed else None)
        return plan,lambda action,**kw:getattr(o,action)(**kw)
    def writes(self,b):return [op for op,_ in b.calls if op in ('delete','reminder_delete','delete_calendar','reminder_list_delete')]
    def test_concrete_item_and_category_complete_once_and_keep_archive(self):
        for kind in ('calendar','reminders'):
            for entity in ('item','category'):
                with self.subTest(kind=kind,entity=entity),M['case'](kind) as (s,o,b):
                    plan,owner=self.setup(s,o,b,kind,entity)
                    self.assertEqual(run(s,plan,lambda *a:True,owner)['state'],'done')
                    self.assertEqual(run(s,plan,lambda *a:False,owner)['state'],'done')
                    self.assertEqual(len(self.writes(b)),1);self.assertFalse(s.status()['scan_claims'])
                    self.assertTrue(o.list_apple_inbound_recoveries())
    def test_missing_live_approval_retains_pending_and_zero_native(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                plan,owner=self.setup(s,o,b,kind)
                with self.assertRaises(PermissionError):run(s,plan,lambda *a:False,owner)
                self.assertFalse(self.writes(b));self.assertEqual(o.apple_operations()[0]['state'],'pending');self.assertFalse(s.status()['scan_claims'])
    def test_proven_not_sent_can_retry_same_plan(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                plan,owner=self.setup(s,o,b,kind);original=b.call
                def refuse(op,**kw):
                    if op in ('delete','reminder_delete'):raise BridgeError('synthetic verified pre-effect refusal',native_quiesced=True,native_no_side_effects=True)
                    return original(op,**kw)
                with patch.object(b,'call',side_effect=refuse):
                    with self.assertRaises(BridgeError):run(s,plan,lambda *a:True,owner)
                self.assertEqual(o.apple_operations()[0]['state'],'pending');self.assertFalse(s.status()['scan_claims'])
                self.assertEqual(s._get('approved-delete:'+'a'*64)['state'],'not_sent')
                self.assertEqual(run(s,plan,lambda *a:True,owner)['state'],'done');self.assertEqual(len(self.writes(b)),1)
    def test_unknown_without_receipt_never_resends(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                plan,owner=self.setup(s,o,b,kind);original=b.call;calls=[]
                def lost(op,**kw):
                    if op in ('delete','reminder_delete'):
                        calls.append(op);raise BridgeError('synthetic lost response')
                    return original(op,**kw)
                with patch.object(b,'call',side_effect=lost):
                    with self.assertRaises(BridgeError):run(s,plan,lambda *a:True,owner)
                    with self.assertRaises(ValueError):run(s,plan,lambda *a:True,owner)
                self.assertEqual(len(calls),1);self.assertEqual(o.apple_operations()[0]['state'],'uncertain')
                self.assertEqual(s.status()['scan_claims'][0]['state'],'uncertain')
    def test_owner_commit_reply_lost_recovers_from_native_receipt_without_resend(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                plan,owner=self.setup(s,o,b,kind)
                def lost(action,**kw):
                    result=owner(action,**kw)
                    if action=='apple_finish_approved_delete':raise RuntimeError('owner reply lost after commit')
                    return result
                with self.assertRaises(RuntimeError):run(s,plan,lambda *a:True,lost)
                self.assertEqual(run(s,plan,lambda *a:False,owner)['state'],'done')
                self.assertEqual(len(self.writes(b)),1);self.assertFalse(s.status()['scan_claims'])
    def test_import_link_cannot_delete_apple_even_when_container_allows_delete(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                plan,owner=self.setup(s,o,b,kind,'category');link=s._stored_links()[0]
                s.replace_links([{**link,'direction':'import','enabled':True}])
                with self.assertRaisesRegex(ValueError,'仅从 Apple 导入'):run(s,plan,lambda *a:True,owner)
                self.assertFalse(self.writes(b));self.assertIsNotNone(o.get_category(link['alcor_id']))

    def test_other_process_claim_blocks_without_mutating_owner(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                plan,owner=self.setup(s,o,b,kind);before=copy.deepcopy(o.apple_operations())
                with M['M']['other_process_holds'](s):
                    with self.assertRaises(ValueError):run(s,plan,lambda *a:True,owner)
                self.assertEqual(o.apple_operations(),before);self.assertFalse(self.writes(b))
                with self.assertRaises(ValueError):run(s,plan,lambda *a:True,owner)
                self.assertEqual(s.status()['scan_claims'][0]['state'],'uncertain')
    def test_changed_owner_before_begin_keeps_new_content_and_zero_native(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                plan,owner=self.setup(s,o,b,kind);ident=plan['local_plan']['id']
                def edit(*args):
                    with o._connect() as db:db.execute('UPDATE '+o.APPLE_ITEMS+' SET title=? WHERE id=?',('New local content',ident))
                    return True
                with self.assertRaises(ValueError):run(s,plan,edit,owner)
                self.assertEqual(s._local(ident)['title'],'New local content');self.assertFalse(self.writes(b));self.assertFalse(s.status()['scan_claims'])
    def test_changed_manager_evidence_after_send_keeps_mapping_and_unknown(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                plan,owner=self.setup(s,o,b,kind);before=copy.deepcopy(s._maps());original=b.call
                def changed(op,**kw):
                    result=original(op,**kw)
                    if op in ('delete','reminder_delete'):
                        with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                    return result
                with patch.object(b,'call',side_effect=changed):
                    with self.assertRaises(ValueError):run(s,plan,lambda *a:True,owner)
                self.assertEqual(s._maps(),before);self.assertIsNotNone(s._local(plan['local_plan']['id']))
                self.assertEqual(o.apple_operations()[0]['state'],'uncertain');self.assertEqual(s.status()['scan_claims'][0]['state'],'uncertain')
                with self.assertRaises(ValueError):run(s,plan,lambda *a:True,owner)
                self.assertEqual(len(self.writes(b)),1)

if __name__=='__main__':unittest.main(verbosity=2)
