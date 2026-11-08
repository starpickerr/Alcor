"""Actual reminder-manager paths with only temporary owners and synthetic native data."""
import copy
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

M=runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))
COMMON=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))


class ReminderSafety(unittest.TestCase):
    def setUp(self):
        self.case=M['Contracts']();self.case.setUp()
        self.owner=self.case.business.tasks;self.bridge=M['NativeBridge']('reminders')
        self.sync=self.case.reminder(self.bridge);self.sync.start_sync()
        self.mapping=copy.deepcopy(self.sync._maps()[0]);self.id=self.mapping['todo_id'];self.bridge.calls.clear()

    def tearDown(self):self.case.tearDown()

    def test_actual_scanner_does_not_cross_another_process_claim(self):
        before=self.owner.get_todo(self.id);self.bridge.rows[0]['title']='Remote later'
        with COMMON['other_process_holds'](self.sync):result=self.sync.sync()
        self.assertNotEqual(result['status'],'success')
        self.assertEqual(self.owner.get_todo(self.id)['title'],before['title'])
        self.assertEqual(self.sync._maps()[0],self.mapping)

    def test_owner_edit_between_compare_and_inbound_commit_is_preserved(self):
        self.bridge.rows[0]['title']='Remote later'
        original=self.sync._apply_remote
        def raced(*args,**kwargs):
            self.owner.update_todo(self.id,{'notes':'Later local note'})
            return original(*args,**kwargs)
        with patch.object(self.sync,'_apply_remote',side_effect=raced):result=self.sync.sync()
        self.assertEqual(self.owner.get_todo(self.id)['notes'],'Later local note')
        self.assertNotEqual(result['status'],'success')
        self.assertEqual(self.sync._maps()[0]['state'],'conflict')

    def test_owner_edit_during_native_update_is_preserved(self):
        self.owner.update_todo(self.id,{'title':'Local outbound'})
        original=self.bridge.call
        def raced(op,**payload):
            result=original(op,**payload)
            if op=='reminder_upsert':self.owner.update_todo(self.id,{'notes':'Later local note'})
            return result
        with patch.object(self.bridge,'call',side_effect=raced):result=self.sync.sync()
        self.assertEqual(self.owner.get_todo(self.id)['notes'],'Later local note')
        self.assertNotEqual(result['status'],'success')
        self.assertEqual(self.sync._maps()[0]['state'],'conflict')

    def test_same_id_recreated_at_missing_delete_is_not_removed(self):
        self.bridge.rows=[];self.sync.sync();self.sync.clock=lambda:'2026-11-07T00:02:00Z'
        original=self.owner.apple_remove_reminder_inbound
        def raced(expected,*args,**kwargs):
            self.owner.apple_remove_item(self.id)
            self.owner.create_todo({'id':self.id,'title':'Replacement task'})
            return original(expected,*args,**kwargs)
        with patch.object(self.owner,'apple_remove_reminder_inbound',side_effect=raced):self.sync.sync()
        self.assertEqual(self.owner.get_todo(self.id)['title'],'Replacement task')

    def test_complete_absence_archives_unchanged_reminder(self):
        self.bridge.rows=[];self.sync.sync();self.sync.clock=lambda:'2026-11-07T00:02:00Z'
        result=self.sync.sync()
        self.assertEqual(result['deleted_local'],1)
        with self.assertRaises(KeyError):self.owner.get_todo(self.id)
        self.assertEqual(self.sync._maps()[0]['state'],'remote_deleted')

    def test_owner_edit_after_native_absence_before_first_marker_is_preserved(self):
        self.bridge.rows=[];original=self.bridge.call;edited=[]
        def raced(op,**payload):
            result=original(op,**payload)
            if op=='reminders' and not edited:
                self.owner.update_todo(self.id,{'notes':'First absence concurrent note'});edited.append(True)
            return result
        with patch.object(self.bridge,'call',side_effect=raced):self.sync.sync()
        self.sync.clock=lambda:'2026-11-07T00:02:00Z';self.sync.sync()
        self.assertEqual(self.owner.get_todo(self.id)['notes'],'First absence concurrent note')
        self.assertEqual(self.sync._maps()[0]['state'],'conflict')

    def test_moved_reminder_in_unselected_list_is_not_deleted(self):
        self.sync._set('sync_all',False)
        self.bridge.sources.append({**self.bridge.sources[0],'id':'other','title':'Other'})
        self.bridge.rows[0]['list_id']='other'
        self.sync.sync();self.sync.clock=lambda:'2026-11-07T00:02:00Z';self.sync.sync()
        self.assertEqual(self.owner.get_todo(self.id)['id'],self.id)
        self.assertEqual(next(row for row in self.sync._maps() if row['todo_id']==self.id)['state'],'conflict')

    def test_legacy_empty_item_source_needs_exact_runtime_identity(self):
        with self.owner._connect() as db:
            import json
            row=db.execute("SELECT document FROM apple_bindings WHERE entity='item' AND local_id=?",(self.id,)).fetchone()
            binding=json.loads(row[0]);binding['source_id']=''
            db.execute("UPDATE apple_bindings SET document=? WHERE entity='item' AND local_id=?",(json.dumps(binding),self.id))
        self.bridge.rows[0]['title']='Verified old ID'
        self.sync.sync()
        self.assertEqual(self.owner.get_todo(self.id)['title'],'Verified old ID')
        self.assertEqual(self.owner.get_todo(self.id)['apple']['source_id'],'account1')

    def test_changed_scan_evidence_cannot_release_claim(self):
        original=self.sync._complete_reminder_scan
        def changed(token,group_id):
            with self.sync._connect() as db:
                db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',('{"stage":"sending"}',token))
            return original(token,group_id)
        with patch.object(self.sync,'_complete_reminder_scan',side_effect=changed):result=self.sync.sync()
        self.assertNotEqual(result['status'],'success')
        self.assertTrue(self.sync.status()['scan_claims'])

    def test_unknown_completion_is_never_sent_twice(self):
        self.bridge.rows[0].update(due_on='2026-11-07',recurrence={'frequency':'daily'})
        self.sync.sync();self.owner.complete_todo(self.id,True)
        original=self.bridge.call;sent=[]
        def lost(op,**payload):
            result=original(op,**payload)
            if op=='reminder_complete':
                sent.append(copy.deepcopy(payload));raise RuntimeError('synthetic lost native reply after save')
            return result
        with patch.object(self.bridge,'call',side_effect=lost):first=self.sync.sync()
        self.assertNotEqual(first['status'],'success')
        restarted=self.case.reminder(self.bridge)
        restarted.sync()
        calls=[op for op,_ in self.bridge.calls if op=='reminder_complete']
        self.assertEqual(len(calls),1)
        self.assertEqual(len([row for row in self.bridge.rows if not row['completed']]),1)
        self.assertTrue(self.owner.apple_operations())


if __name__=='__main__':unittest.main(verbosity=2)
