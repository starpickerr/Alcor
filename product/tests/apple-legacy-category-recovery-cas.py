"""Existing legacy category restore entry, with synthetic owner/manager databases only."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))
from apple_sync.categories import synchronize_categories

class LegacyCategoryRecovery(unittest.TestCase):
    def legacy(self,s,o,b):
        s.start_sync();link=s._stored_links()[0];item=s._maps()[0]['alcor_id' if b.kind=='calendar' else 'todo_id']
        fallback=o.create_category({'name':'Legacy retained'});o.apple_place_item(item,fallback['id'])
        with o._connect() as db:db.execute(f'DELETE FROM {o.APPLE_CATEGORIES} WHERE id=?',(link['alcor_id'],))
        b.calls.clear();return link,item,fallback
    def test_normal_legacy_restoration_preserves_members_and_never_deletes_native(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item,fallback=self.legacy(s,o,b)
                synchronize_categories(s,o,kind)
                self.assertEqual(s._local(item)['category_id'],link['alcor_id'])
                self.assertTrue(o.get_category(link['alcor_id']))
                self.assertTrue(any(row['kind']=='legacy_retained' for row in s.status()['category_conflicts']))
                self.assertFalse(any(op in ('delete_calendar','reminder_list_delete','delete','reminder_delete') for op,_ in b.calls))
    def test_user_move_after_capture_before_atomic_restore_is_not_overwritten(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item,fallback=self.legacy(s,o,b);target=o.create_category({'name':'My new choice'})
                original=o.apple_restore_legacy_category
                def moved(*args):
                    (o.update_event if kind=='calendar' else o.update_todo)(item,{'category_id':target['id']})
                    return original(*args)
                with patch.object(o,'apple_restore_legacy_category',side_effect=moved):synchronize_categories(s,o,kind)
                self.assertEqual(s._local(item)['category_id'],target['id'])
    def test_same_item_held_in_other_group_cannot_be_relocated_by_legacy_restore(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item,fallback=self.legacy(s,o,b);before=copy.deepcopy(s._local(item))
                with M['M']['other_process_holds'](s,group='other',item=item):
                    synchronize_categories(s,o,kind)
                self.assertEqual(s._local(item),before)

    def test_inventory_read_race_uses_snapshot_taken_before_native_observation(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item,fallback=self.legacy(s,o,b);target=o.create_category({'name':'Chosen during inventory'})
                original=b.call;edited=False
                def read(op,**kw):
                    nonlocal edited
                    if not edited and op in ('calendars','reminder_lists'):
                        edited=True;(o.update_event if kind=='calendar' else o.update_todo)(item,{'category_id':target['id']})
                    return original(op,**kw)
                with patch.object(b,'call',side_effect=read):synchronize_categories(s,o,kind)
                self.assertTrue(edited);self.assertEqual(s._local(item)['category_id'],target['id'])
                with self.assertRaises(KeyError):o.get_category(link['alcor_id'])
    def test_existing_user_edit_is_not_assumed_to_be_legacy_fallback(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item,fallback=self.legacy(s,o,b)
                (o.update_event if kind=='calendar' else o.update_todo)(item,{'title':'My current choice'})
                before=copy.deepcopy(s._local(item));synchronize_categories(s,o,kind)
                self.assertEqual(s._local(item),before)
                self.assertTrue(any(row['kind']=='category_waiting' for row in s.status()['category_conflicts']))
    def test_same_id_category_created_after_capture_is_preserved(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item,fallback=self.legacy(s,o,b);original=o.apple_restore_legacy_category
                def recreated(*args):
                    with o._connect() as db:db.execute(f'INSERT INTO {o.APPLE_CATEGORIES} VALUES (?,?,?,?)',(link['alcor_id'],'New incarnation','#112233','2026-11-08'))
                    return original(*args)
                with patch.object(o,'apple_restore_legacy_category',side_effect=recreated):synchronize_categories(s,o,kind)
                self.assertEqual(o.get_category(link['alcor_id'])['name'],'New incarnation')
                self.assertEqual(s._local(item)['category_id'],fallback['id']);self.assertFalse(s.status()['scan_claims'])
    def test_actual_commit_lost_reply_retains_claim_and_does_not_replay(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item,fallback=self.legacy(s,o,b);original=o.apple_restore_legacy_category;sent=[]
                def lost(*args):sent.append(args);original(*args);raise RuntimeError('reply lost after owner commit')
                with patch.object(o,'apple_restore_legacy_category',side_effect=lost):
                    with self.assertRaisesRegex(RuntimeError,'reply lost'):synchronize_categories(s,o,kind)
                    synchronize_categories(s,o,kind)
                self.assertEqual(len(sent),1);self.assertEqual(s._local(item)['category_id'],link['alcor_id'])
                self.assertTrue(o.get_category(link['alcor_id']));self.assertTrue(s.status()['scan_claims'])
                self.assertFalse(s._get('legacy-restored:'+link['apple_id']))
                with self.assertRaisesRegex(ValueError,'already committed'):original(*sent[0])
    def test_owner_transaction_rolls_back_all_local_repair_on_error(self):
        import business.legacy_category_recovery as recovery
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item,fallback=self.legacy(s,o,b);before=copy.deepcopy(s._local(item))
                with patch.object(recovery,'save_receipt',side_effect=RuntimeError('before commit failed')):
                    with self.assertRaisesRegex(RuntimeError,'before commit'):synchronize_categories(s,o,kind)
                self.assertEqual(s._local(item),before)
                with self.assertRaises(KeyError):o.get_category(link['alcor_id'])
                self.assertTrue(s.status()['scan_claims']);self.assertFalse(s._get('legacy-restored:'+link['apple_id']))
    def test_manager_evidence_changed_after_owner_commit_cannot_mark_completed(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                link,item,fallback=self.legacy(s,o,b);original=o.apple_restore_legacy_category
                def changed(*args):
                    result=original(*args)
                    with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                    return result
                with patch.object(o,'apple_restore_legacy_category',side_effect=changed):synchronize_categories(s,o,kind)
                self.assertEqual(s._local(item)['category_id'],link['alcor_id'])
                self.assertFalse(s._get('legacy-restored:'+link['apple_id']));self.assertTrue(s.status()['scan_claims'])

if __name__=='__main__':unittest.main(verbosity=2)
