"""Normal cross-list reminder moves under two manager claims, synthetic native only."""
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))

class Moves(unittest.TestCase):
    def setUp(self):
        self.case=M['M']['Contracts']();self.case.setUp()
        self.owner=self.case.business.tasks;self.bridge=M['M']['NativeBridge']('reminders')
        self.bridge.sources.append({**self.bridge.sources[0],'id':'b','title':'Other'})
        self.sync=self.case.reminder(self.bridge);self.sync.start_sync()
        self.id=self.sync._maps()[0]['todo_id'];self.target=next(row['alcor_id'] for row in self.sync._stored_links() if row['apple_id']=='b')
        self.owner.update_todo(self.id,{'category_id':self.target});self.bridge.calls.clear()
    def tearDown(self):self.case.tearDown()
    def test_normal_move_preserves_identity_and_generation(self):
        self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(self.sync._maps()[0]['todo_id'],self.id)
        self.assertEqual(self.sync._maps()[0]['list_id'],'b')
        self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.target)
        self.assertEqual(len(self.owner.list_todos()),1)
        with self.owner._connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM apple_completion_history WHERE local_id=?',(self.id,)).fetchone()[0],0)
        self.assertEqual(self.sync.status()['scan_claims'],[])
    def test_destination_held_by_other_process_prevents_move(self):
        with M['other_process_holds'](self.sync,'b'):self.sync.sync()
        self.assertFalse(any(op=='reminder_upsert' for op,_ in self.bridge.calls))
        self.assertEqual(self.sync._maps()[0]['list_id'],'a')
    def test_both_groups_are_held_at_native_send(self):
        other=M['CommonClaims']();original=self.bridge.call
        def checked(op,**payload):
            if op=='reminder_upsert':
                self.assertEqual(other.competing_scan(self.sync,'a'),'blocked')
                self.assertEqual(other.competing_scan(self.sync,'b'),'blocked')
                self.assertEqual(payload['expected_snapshot']['list_id'],'a')
                self.assertEqual(payload['expected_destination_snapshot']['id'],'b')
            return original(op,**payload)
        with patch.object(self.bridge,'call',side_effect=checked):result=self.sync.sync()
        self.assertEqual(result['status'],'success')
    def test_native_save_then_owner_edit_keeps_local_and_holds_both_groups(self):
        original=self.bridge.call
        def changed(op,**payload):
            result=original(op,**payload)
            if op=='reminder_upsert':self.owner.update_todo(self.id,{'notes':'Concurrent move edit'})
            return result
        with patch.object(self.bridge,'call',side_effect=changed):self.sync.sync()
        self.assertEqual(self.owner.get_todo(self.id)['notes'],'Concurrent move edit')
        self.assertEqual(self.sync._maps()[0]['list_id'],'a')
        self.assertEqual({row['group_id'] for row in self.sync.status()['scan_claims']},{'a','b'})
        count=sum(op=='reminder_upsert' for op,_ in self.bridge.calls);self.sync.sync()
        self.assertEqual(sum(op=='reminder_upsert' for op,_ in self.bridge.calls),count)
    def test_lookup_failure_before_native_send_releases_both_groups(self):
        original=self.bridge.call
        def gone(op,**payload):
            if op=='reminder_lookup':return {'reminder':None}
            return original(op,**payload)
        with patch.object(self.bridge,'call',side_effect=gone):self.sync.sync()
        self.assertFalse(any(op=='reminder_upsert' for op,_ in self.bridge.calls))
        self.assertEqual(self.sync.status()['scan_claims'],[])
        self.assertEqual(self.sync._maps()[0]['state'],'conflict')

if __name__=='__main__':unittest.main(verbosity=2)
