"""Exact existing local-missing choice with synthetic native rows and real SQLite."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))
from apple_sync import item_restore
from apple_sync.group_scan_claims import WorkClaim

class MissingLocalManager(unittest.TestCase):
    def setup(self,s,o,b,kind):
        s.start_sync();key='todo_id' if kind=='reminders' else 'alcor_id';identity=s._maps()[0][key]
        o.apple_remove_item(identity);b.rows[0]['title']='Chosen Apple content';s.sync();b.calls.clear()
        return identity,item_restore.prepare(s,o,kind,identity)
    def no_writes(self,b):self.assertFalse(any(op in ('upsert','delete','reminder_upsert','reminder_delete','reminder_complete','create_calendar','reminder_list_create') for op,_ in b.calls))
    def test_normal_choice_restores_one_and_scan_never_reexports(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                identity,plan=self.setup(s,o,b,kind);s.resolve_conflict(identity,'keep_apple')
                self.assertEqual(s._local(identity)['title'],'Chosen Apple content');self.assertEqual(s._maps()[0]['state'],'active');self.assertEqual(s.status()['scan_claims'],[])
                s.sync();s.sync();self.assertEqual(len(s._maps()),1);self.assertEqual(len(b.rows),1);self.no_writes(b)
                self.assertEqual(s._local(identity)['title'],'Chosen Apple content')
    def test_frozen_plan_refuses_changed_native_or_local_target(self):
        for kind in ('calendar','reminders'):
            for change in ('native','local'):
                with self.subTest(kind=kind,change=change),M['case'](kind) as (s,o,b):
                    identity,plan=self.setup(s,o,b,kind)
                    if change=='native':b.rows[0]['title']='Later Apple edit'
                    else:(o.create_event if kind=='calendar' else o.create_todo)({**plan['owner_expected']['payload'],'id':identity,'title':'New local choice'})
                    with self.assertRaises(ValueError):item_restore.run(s,o,kind,plan,lambda:True)
                    if change=='local':self.assertEqual(s._local(identity)['title'],'New local choice')
                    else:self.assertIsNone(s._local(identity))
                    self.assertEqual(s._maps()[0]['state'],'conflict');self.no_writes(b);self.assertEqual(s.status()['scan_claims'],[])
    def test_missing_live_entry_context_cannot_write(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                identity,plan=self.setup(s,o,b,kind)
                with self.assertRaises(PermissionError):item_restore.run(s,o,kind,plan,lambda:False)
                self.assertIsNone(s._local(identity));self.assertEqual(s.status()['scan_claims'],[]);self.no_writes(b)
    def test_concurrent_owner_recreation_is_never_overwritten(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                identity,plan=self.setup(s,o,b,kind);original=o.apple_restore_item
                def created(*args):
                    (o.create_event if kind=='calendar' else o.create_todo)({**plan['owner_expected']['payload'],'id':identity,'title':'Concurrent local incarnation'})
                    return original(*args)
                with patch.object(o,'apple_restore_item',side_effect=created),self.assertRaisesRegex(ValueError,'INBOUND_CHANGED'):item_restore.run(s,o,kind,plan,lambda:True)
                self.assertEqual(s._local(identity)['title'],'Concurrent local incarnation');self.assertEqual(s._maps()[0]['state'],'conflict');self.assertEqual(s.status()['scan_claims'],[]);self.no_writes(b)
    def test_exact_owner_receipt_recovers_lost_response_without_recreating(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                identity,plan=self.setup(s,o,b,kind);original=o.apple_restore_item
                def lost(*args):original(*args);raise RuntimeError('synthetic lost reply after owner commit')
                with patch.object(o,'apple_restore_item',side_effect=lost),self.assertRaises(RuntimeError):item_restore.run(s,o,kind,plan,lambda:True)
                before=copy.deepcopy(s._local(identity));self.assertTrue(s.status()['scan_claims'])
                with patch.object(o,'apple_restore_item',side_effect=AssertionError('must only read receipt')):s.resolve_conflict(identity,'keep_apple')
                self.assertEqual(s._local(identity),before);self.assertEqual(s._maps()[0]['state'],'active');self.assertEqual(s.status()['scan_claims'],[]);self.no_writes(b)
    def test_unknown_without_receipt_keeps_hold_and_never_recreates(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                identity,plan=self.setup(s,o,b,kind)
                with patch.object(o,'apple_restore_item',side_effect=RuntimeError('unknown transport')),self.assertRaises(RuntimeError):item_restore.run(s,o,kind,plan,lambda:True)
                self.assertTrue(s.status()['scan_claims']);self.assertIsNone(s._local(identity))
                with patch.object(o,'apple_restore_item',side_effect=AssertionError('must not retry')),self.assertRaises(ValueError):s.resolve_conflict(identity,'keep_apple')
                self.assertIsNone(s._local(identity));self.no_writes(b)
    def test_later_owner_edit_prevents_receipt_finalization(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                identity,plan=self.setup(s,o,b,kind);original=o.apple_restore_item
                def changed(*args):
                    result=original(*args);(o.update_event if kind=='calendar' else o.update_todo)(identity,{'title':'Later local edit'});return result
                with patch.object(o,'apple_restore_item',side_effect=changed),self.assertRaisesRegex(ValueError,'INBOUND_CHANGED'):item_restore.run(s,o,kind,plan,lambda:True)
                self.assertEqual(s._local(identity)['title'],'Later local edit');self.assertEqual(s._maps()[0]['state'],'conflict');self.assertTrue(s.status()['scan_claims']);self.no_writes(b)
    def test_claim_evidence_changed_after_owner_commit_keeps_mapping_conflict(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                identity,plan=self.setup(s,o,b,kind);original=o.apple_restore_item
                def changed(*args):
                    result=original(*args)
                    with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                    return result
                with patch.object(o,'apple_restore_item',side_effect=changed),self.assertRaises(ValueError):item_restore.run(s,o,kind,plan,lambda:True)
                self.assertIsNotNone(s._local(identity));self.assertEqual(s._maps()[0]['state'],'conflict');self.assertTrue(s.status()['scan_claims']);self.no_writes(b)
    def test_other_process_item_claim_blocks_restore(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                identity,plan=self.setup(s,o,b,kind);before=copy.deepcopy(s._maps())
                with M['M']['other_process_holds'](s,item=identity):
                    with self.assertRaises(ValueError):item_restore.run(s,o,kind,plan,lambda:True)
                    self.assertIsNone(s._local(identity));self.assertEqual(s._maps(),before);self.no_writes(b)
    def test_completed_manager_without_claim_cleanup_finishes_receipt_only(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                identity,plan=self.setup(s,o,b,kind)
                with patch.object(WorkClaim,'__exit__',return_value=False):item_restore.run(s,o,kind,plan,lambda:True)
                self.assertTrue(s.status()['scan_claims']);before=copy.deepcopy(s._local(identity))
                self.assertIs(s.status()['conflicts'][0]['local_restore_pending'],True)
                self.assertIs(s.status()['conflicts'][0]['local_missing'],False)
                with patch.object(o,'apple_restore_item',side_effect=AssertionError('no repeated owner write')):s.resolve_conflict(identity,'keep_apple')
                self.assertFalse(s.status()['scan_claims']);self.assertEqual(s._local(identity),before);self.no_writes(b)
                self.assertFalse(s.status()['conflicts'])
    def test_proven_owner_rollback_allows_new_explicit_plan(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                identity,plan=self.setup(s,o,b,kind);original=o.apple_restore_item
                def changed(*args):
                    o.update_category(plan['owner_expected']['category_id'],{'name':'Later local category name'})
                    return original(*args)
                with patch.object(o,'apple_restore_item',side_effect=changed),self.assertRaisesRegex(ValueError,'INBOUND_CHANGED'):
                    item_restore.run(s,o,kind,plan,lambda:True)
                self.assertFalse(s.status()['scan_claims']);self.assertIsNone(s._local(identity))
                fresh=item_restore.prepare(s,o,kind,identity)
                self.assertNotIn('mode',fresh)
                item_restore.run(s,o,kind,fresh,lambda:True)
                self.assertEqual(s._local(identity)['title'],'Chosen Apple content');self.no_writes(b)

if __name__=='__main__':unittest.main(verbosity=2)
