"""Category absence participates in the existing cross-process group/item claims."""
import copy,runpy,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-category-absence-sync.py')))
C=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))

class AbsenceClaims(unittest.TestCase):
    def setUp(self):self.base=M['CategoryAbsence']()
    def test_owner_archive_holds_group_and_member_against_other_process(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),self.base.case(kind) as (sync,owner,bridge,clock,link):
                self.base.missing(sync,bridge,clock);original=owner.apple_remove_category_if_unchanged;seen=[]
                def remove(expected):
                    self.assertEqual(C['CommonClaims']().competing_scan(sync),'blocked');seen.append(True)
                    return original(expected)
                with patch.object(owner,'apple_remove_category_if_unchanged',side_effect=remove):result=sync.sync()
                self.assertEqual(len(seen),1);self.assertEqual(result.get('deleted_categories'),1,result)
                self.assertEqual(sync.status()['scan_claims'],[])
    def test_manual_absence_cannot_archive_under_other_process_hold(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),self.base.case(kind) as (sync,owner,bridge,clock,link):
                self.base.missing(sync,bridge,clock);before=copy.deepcopy(sync._get('category-absence:a'))
                with C['other_process_holds'](sync):
                    with self.assertRaises(ValueError):sync.resolve_category_conflict('a','keep_apple')
                self.assertTrue(owner.get_category(link['alcor_id']));self.assertEqual(owner.list_apple_inbound_recoveries(),[])
                self.assertEqual(sync._get('category-absence:a'),before)
    def test_lost_owner_response_keeps_claim_and_recovery_without_retry(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),self.base.case(kind) as (sync,owner,bridge,clock,link):
                self.base.missing(sync,bridge,clock);original=owner.apple_remove_category_if_unchanged;calls=[]
                def lost(expected):
                    result=original(expected);calls.append(result);raise RuntimeError('owner response lost')
                with patch.object(owner,'apple_remove_category_if_unchanged',side_effect=lost):sync.sync();sync.sync()
                self.assertEqual(len(calls),1);self.assertEqual(len(owner.list_apple_inbound_recoveries()),1)
                self.assertEqual(sync._get('category-absence:a')['phase'],'uncertain')
                self.assertEqual(sync.status()['scan_claims'][0]['state'],'uncertain')
    def test_cross_container_descendant_is_not_archived_by_parent_absence(self):
        with self.base.case('reminders') as (sync,owner,bridge,clock,link):
            bridge.sources.append({**bridge.sources[0],'id':'b','title':'Other list'})
            parent=sync._maps()[0]['todo_id'];other={**bridge.rows[0],'reminder_id':'child','external_id':'child-external','list_id':'b'}
            bridge.rows.append(other);sync.sync();child=next(row for row in sync._maps() if row['reminder_id']=='child')
            owner.move_todo(child['todo_id'],parent_id=parent);sync.sync()
            self.assertEqual(owner.get_todo(child['todo_id'])['parent_id'],parent)
            bridge.sources=[row for row in bridge.sources if row['id']!='a'];bridge.rows=[row for row in bridge.rows if row['list_id']!='a']
            sync.sync();clock[0]='2026-11-07T00:02:00Z';result=sync.sync()
            self.assertEqual(result.get('deleted_categories'),0,result)
            self.assertTrue(owner.get_category(link['alcor_id']));self.assertTrue(owner.get_todo(child['todo_id']))
            self.assertEqual(owner.list_apple_inbound_recoveries(),[])

    def test_pre_owner_inventory_or_manager_failure_releases_and_can_retry(self):
        from apple_sync import category_absence
        for kind in ('calendar','reminders'):
            for failure in ('inventory','database'):
                with self.subTest(kind=kind,failure=failure),self.base.case(kind) as (sync,owner,bridge,clock,link):
                    self.base.missing(sync,bridge,clock);before=copy.deepcopy(sync._get('category-absence:a'))
                    inventory=bridge.call('reminder_lists' if kind=='reminders' else 'calendars')
                    call=bridge.call;manager=category_absence._manager;reads=[]
                    def broken_inventory(op,**payload):
                        if op in ('calendars','reminder_lists'):raise RuntimeError('synthetic inventory read failure')
                        return call(op,**payload)
                    def broken_manager(*args):
                        reads.append(True)
                        if len(reads)==2:raise sqlite3.OperationalError('synthetic manager read failure')
                        return manager(*args)
                    target=patch.object(bridge,'call',side_effect=broken_inventory) if failure=='inventory' else patch.object(category_absence,'_manager',side_effect=broken_manager)
                    with target:deleted,reason=category_absence.reconcile(sync,owner,kind,link,inventory,True)
                    self.assertFalse(deleted);self.assertIn('failure',reason)
                    self.assertEqual(sync.status()['scan_claims'],[]);self.assertEqual(sync._get('category-absence:a'),before)
                    self.assertTrue(owner.get_category(link['alcor_id']));self.assertEqual(owner.list_apple_inbound_recoveries(),[])
                    self.assertEqual(sync.sync().get('deleted_categories'),1)

    def test_known_owner_rejection_releases_but_preserves_first_conflict(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),self.base.case(kind) as (sync,owner,bridge,clock,link):
                self.base.missing(sync,bridge,clock);expected=copy.deepcopy(sync._get('category-absence:a')['expected'])
                original=owner.apple_remove_category_if_unchanged;changed=[]
                def race(snapshot):
                    changed.append(self.base.create(owner,kind,link['alcor_id']))
                    return original(snapshot)
                with patch.object(owner,'apple_remove_category_if_unchanged',side_effect=race):sync.sync();sync.sync()
                self.assertEqual(len(changed),1);self.assertEqual(sync.status()['scan_claims'],[])
                self.assertEqual(sync._get('category-absence:a')['expected'],expected)
                self.assertEqual(sync._get('category-absence:a')['phase'],'uncertain')
                self.assertEqual(owner.list_apple_inbound_recoveries(),[])

    def test_owner_database_error_stays_unknown_not_retried(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),self.base.case(kind) as (sync,owner,bridge,clock,link):
                self.base.missing(sync,bridge,clock)
                with patch.object(owner,'apple_remove_category_if_unchanged',side_effect=sqlite3.OperationalError('synthetic uncertain owner error')) as call:
                    sync.sync();sync.sync()
                self.assertEqual(call.call_count,1);self.assertTrue(owner.get_category(link['alcor_id']))
                self.assertEqual(sync.status()['scan_claims'][0]['state'],'uncertain')

    def test_known_owner_rejection_does_not_release_altered_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),self.base.case(kind) as (sync,owner,bridge,clock,link):
                self.base.missing(sync,bridge,clock);original=owner.apple_remove_category_if_unchanged
                def race(snapshot):
                    self.base.create(owner,kind,link['alcor_id'])
                    with sync._connect() as db:
                        db.execute("UPDATE calendar_scan_attempts SET state='sending' WHERE attempt_id IN (SELECT attempt_id FROM calendar_scan_groups)")
                    return original(snapshot)
                with patch.object(owner,'apple_remove_category_if_unchanged',side_effect=race):sync.sync()
                self.assertTrue(sync.status()['scan_claims']);self.assertTrue(owner.get_category(link['alcor_id']))
                self.assertEqual(owner.list_apple_inbound_recoveries(),[])

if __name__=='__main__':unittest.main(verbosity=2)
