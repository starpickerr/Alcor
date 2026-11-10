"""Actual scan marker recovery with fake native contents and temporary SQLite only."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))

def marker_receipt(owner,operation):
    with owner._connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alcor_security_receipts'").fetchone():return False
        return bool(db.execute("SELECT 1 FROM alcor_security_receipts WHERE operation_id=? AND method='bind_existing_marker'",(operation,)).fetchone())

class MarkerOwnerCAS(unittest.TestCase):
    def marker(self,s,o,b,verified=False):
        s.start_sync();category=s._stored_links()[0]['alcor_id'];remote=copy.deepcopy(b.rows[0])
        remote.update(title='Marker local',external_id='marker-external',url='https://example.com/native')
        remote['event_id' if b.kind=='calendar' else 'reminder_id']='marker-native'
        if b.kind=='calendar':
            from apple_sync.calendar import _payload
            local=o.create_event(_payload(remote,category))
        else:
            from apple_sync.reminders import _payload
            local=o.create_todo(_payload(remote,category))
        remote['alcor_id']=local['id'];b.rows.append(remote);b.calls.clear()
        if verified:
            from apple_sync.contracts import bind_item
            bind_item(o,local,remote,b.kind);local=s._local(local['id'])
        return local,remote
    def test_unbound_marker_is_held_without_native_write(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local,remote=self.marker(s,o,b);before=copy.deepcopy(s._local(local['id']));s.sync()
                self.assertEqual(s._local(local['id']),before)
                self.assertEqual(len(s._maps()),1);self.assertTrue(s.status()['marker_conflicts'])
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))
    def test_user_metadata_edit_before_marker_owner_write_survives(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local,remote=self.marker(s,o,b,verified=True);original=o.apple_bind_marker_if_unchanged;edited=False;attempts=[]
                def edit(operation,expected,category,native,issues):
                    attempts.append(operation)
                    identity=expected['local_id']
                    nonlocal edited
                    if identity==local['id'] and not edited:
                        edited=True;(o.update_event if kind=='calendar' else o.update_todo)(identity,{'url':'https://example.com/user-choice'})
                    return original(operation,expected,category,native,issues)
                with patch.object(o,'apple_bind_marker_if_unchanged',side_effect=edit):s.sync()
                self.assertTrue(edited)
                self.assertEqual([marker_receipt(o,operation) for operation in attempts],[False])
                self.assertEqual(s._local(local['id'])['url'],'https://example.com/user-choice')
    def test_concurrent_verified_binding_is_not_replaced_by_marker(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local,remote=self.marker(s,o,b,verified=True);original=o.apple_bind_marker_if_unchanged;bound=False
                def bind(operation,expected,category,native,issues):
                    identity=expected['local_id']
                    nonlocal bound
                    if identity==local['id'] and not bound:
                        bound=True;o.apple_bind('item',identity,{'apple_id':'verified-other','group_id':'a','source_id':'account1','can_modify':True,'can_delete':True})
                    return original(operation,expected,category,native,issues)
                with patch.object(o,'apple_bind_marker_if_unchanged',side_effect=bind):s.sync()
                self.assertTrue(bound)
                self.assertEqual(s._local(local['id'])['apple']['apple_id'],'verified-other')

    def test_existing_exact_binding_without_map_preserves_normal_recovery(self):
        from apple_sync.contracts import bind_item
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local,remote=self.marker(s,o,b,verified=True);bind_item(o,local,remote,kind)
                s.sync();s.sync()
                self.assertEqual(s._local(local['id'])['apple']['apple_id'],'marker-native')
                self.assertEqual(len(s._maps()),2)
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))
    def test_actual_owner_commit_lost_reply_does_not_map_or_retry(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local,remote=self.marker(s,o,b,verified=True);original=o.apple_bind_marker_if_unchanged;calls=[];receipts=[]
                def lost(*args):
                    calls.append(args);original(*args);receipts.append(marker_receipt(o,args[0]))
                    raise RuntimeError('owner committed reply lost')
                with patch.object(o,'apple_bind_marker_if_unchanged',side_effect=lost):s.sync();s.sync()
                self.assertEqual(len(calls),1);self.assertEqual(s._local(local['id'])['apple']['apple_id'],'marker-native')
                self.assertEqual(receipts,[True])
                self.assertEqual(len(s._maps()),1);self.assertTrue(s.status()['scan_claims'])
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))
    def test_manager_claim_change_after_owner_commit_cannot_insert_mapping(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local,remote=self.marker(s,o,b,verified=True);original=o.apple_bind_marker_if_unchanged
                receipts=[]
                def changed(*args):
                    result=original(*args);receipts.append(marker_receipt(o,args[0]))
                    with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                    return result
                with patch.object(o,'apple_bind_marker_if_unchanged',side_effect=changed):s.sync()
                self.assertEqual(receipts,[True])
                self.assertEqual(len(s._maps()),1);self.assertTrue(s.status()['scan_claims'])
                self.assertEqual(s._local(local['id'])['apple']['apple_id'],'marker-native')
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))
    def test_recreated_same_local_id_after_capture_is_never_bound(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local,remote=self.marker(s,o,b,verified=True);original=o.apple_bind_marker_if_unchanged
                def recreated(operation,expected,category,native,issues):
                    with o._connect() as db:
                        row=dict(db.execute(f'SELECT * FROM {o.APPLE_ITEMS} WHERE id=?',(local['id'],)).fetchone())
                        db.execute(f'DELETE FROM {o.APPLE_ITEMS} WHERE id=?',(local['id'],))
                        db.execute("DELETE FROM apple_bindings WHERE entity='item' AND local_id=?",(local['id'],))
                        row['title']='Recreated local identity'
                        db.execute(f'INSERT INTO {o.APPLE_ITEMS} ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
                    return original(operation,expected,category,native,issues)
                with patch.object(o,'apple_bind_marker_if_unchanged',side_effect=recreated):s.sync()
                self.assertEqual(s._local(local['id'])['title'],'Recreated local identity')
                self.assertFalse(s._local(local['id']).get('apple'));self.assertEqual(len(s._maps()),1)
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))
    def test_other_process_item_claim_preserves_marker_target_in_both_domains(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local,remote=self.marker(s,o,b,verified=True);before=copy.deepcopy(s._local(local['id']))
                with M['M']['other_process_holds'](s,'unrelated',local['id']):s.sync()
                self.assertEqual(s._local(local['id']),before);self.assertEqual(len(s._maps()),1)
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))

    def test_standalone_precommit_rejection_releases_only_unchanged_claim(self):
        from apple_sync.marker_recovery import recover
        for kind in ('calendar','reminders'):
            for altered in (False,True):
                with self.subTest(kind=kind,altered=altered),M['case'](kind) as (s,o,b):
                    local,remote=self.marker(s,o,b,verified=True);original=o.apple_bind_marker_if_unchanged
                    def changed(*args):
                        (o.update_event if kind=='calendar' else o.update_todo)(local['id'],{'url':'https://example.com/new-choice'})
                        if altered:
                            with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                        return original(*args)
                    with patch.object(o,'apple_bind_marker_if_unchanged',side_effect=changed):
                        with self.assertRaises(ValueError):recover(s,o,kind,s._local(local['id']),remote)
                    self.assertEqual(bool(s.status()['scan_claims']),altered)
                    self.assertEqual(s._local(local['id'])['url'],'https://example.com/new-choice')
                    self.assertEqual(len(s._maps()),1)
                    self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))

if __name__=='__main__':unittest.main(verbosity=2)
