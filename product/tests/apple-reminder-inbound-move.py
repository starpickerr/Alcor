"""Exact-ID same-source inbound moves; temporary SQLite and synthetic native rows only."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-reminder-move-intent.py')))

class InboundMove(unittest.TestCase):
    def setUp(self):
        self.base=M['Intent']();self.base.setUp()
        for key in ('sync','owner','bridge','id','target','third','original'):setattr(self,key,getattr(self.base,key))
        self.owner.update_todo(self.id,{'category_id':self.original['alcor_id']})
        self.before=copy.deepcopy(self.sync._maps()[0]);self.bridge.rows[0]['list_id']='b';self.bridge.calls.clear()
    def tearDown(self):self.base.tearDown()
    def assert_no_send_or_duplicate(self):
        self.assertFalse(any(op in ('reminder_upsert','reminder_delete','reminder_complete') for op,_ in self.bridge.calls))
        self.assertEqual(len(self.owner.list_todos()),1);self.assertEqual(len(self.sync._maps()),1);self.assertEqual(len(self.bridge.rows),1)
    def test_same_id_same_source_move_and_content_import_normally(self):
        self.bridge.rows[0]['notes']='Native move plus edit'
        for _ in range(3):
            self.assertEqual(self.sync.sync()['status'],'success')
            row=self.owner.get_todo(self.id)
            self.assertEqual(row['category_id'],self.target);self.assertEqual(row['notes'],'Native move plus edit')
            self.assertEqual(row['apple']['group_id'],'b');self.assertEqual(self.sync._maps()[0]['list_id'],'b')
            self.assert_no_send_or_duplicate()
        self.assertEqual(self.sync.status()['scan_claims'],[])
    def test_both_groups_held_at_owner_commit_unrelated_group_free(self):
        check=M['M']['M']['M']['CommonClaims']();original=self.owner.apple_apply_reminder_move;seen=[]
        def apply(*args):
            seen.append(True)
            self.assertEqual(check.competing_scan(self.sync,'a'),'blocked');self.assertEqual(check.competing_scan(self.sync,'b'),'blocked')
            self.assertEqual(check.competing_scan(self.sync,'unrelated'),'acquired')
            return original(*args)
        with patch.object(self.owner,'apple_apply_reminder_move',side_effect=apply):result=self.sync.sync()
        self.assertEqual(result['status'],'success');self.assertEqual(len(seen),1);self.assert_no_send_or_duplicate()
    def test_competing_destination_defers_without_poison_and_recovers(self):
        with M['M']['competing_work'](self.sync):
            result=self.sync.sync();self.assertNotEqual(result['status'],'success')
            self.assertEqual(self.sync._maps()[0],self.before);self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.original['alcor_id'])
        self.assertEqual(self.sync.sync()['status'],'success');self.assertEqual(self.sync._maps()[0]['list_id'],'b');self.assert_no_send_or_duplicate()
    def test_edit_after_inventory_preserved_by_first_observation(self):
        original=self.bridge.call;seen=[]
        def call(op,**payload):
            result=original(op,**payload)
            if op=='reminders' and len(payload.get('list_ids',[]))>1 and not seen:
                self.owner.update_todo(self.id,{'notes':'User edit during inventory'});seen.append(True)
            return result
        with patch.object(self.bridge,'call',side_effect=call):result=self.sync.sync()
        self.assertTrue(seen);self.assertNotEqual(result['status'],'success')
        self.assertEqual(self.owner.get_todo(self.id)['notes'],'User edit during inventory')
        self.assertEqual(self.sync._maps()[0]['list_id'],'a');self.assertEqual(self.sync._maps()[0]['state'],'conflict')
        self.assertEqual(self.sync.status()['scan_claims'],[]);self.assert_no_send_or_duplicate()
    def test_owner_cas_rejection_preserves_new_target_and_releases(self):
        original=self.owner.apple_apply_reminder_move
        def change(*args):
            self.owner.update_todo(self.id,{'category_id':self.third});return original(*args)
        with patch.object(self.owner,'apple_apply_reminder_move',side_effect=change):result=self.sync.sync()
        self.assertNotEqual(result['status'],'success');self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.third)
        self.assertEqual(self.sync._maps()[0]['list_id'],'a');self.assertEqual(self.sync.status()['scan_claims'],[]);self.assert_no_send_or_duplicate()
    def test_owner_commit_response_loss_stays_held_and_is_not_repeated(self):
        original=self.owner.apple_apply_reminder_move;seen=[]
        def lost(*args):
            seen.append(True);original(*args);raise RuntimeError('synthetic lost owner commit response')
        with patch.object(self.owner,'apple_apply_reminder_move',side_effect=lost):self.sync.sync();self.sync.sync()
        self.assertEqual(len(seen),1);self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.target)
        self.assertEqual(self.sync._maps()[0]['list_id'],'a');self.assertEqual({x['group_id'] for x in self.sync.status()['scan_claims']},{'a','b'})
        self.assert_no_send_or_duplicate()
    def test_owner_rejection_with_changed_claim_cannot_release(self):
        original=self.owner.apple_apply_reminder_move
        def change(*args):
            self.owner.update_todo(self.id,{'notes':'Changed user data'})
            with self.sync._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
            return original(*args)
        with patch.object(self.owner,'apple_apply_reminder_move',side_effect=change):self.sync.sync()
        self.assertEqual(self.owner.get_todo(self.id)['notes'],'Changed user data');self.assertEqual(self.sync._maps()[0],self.before)
        self.assertEqual({x['group_id'] for x in self.sync.status()['scan_claims']},{'a','b'});self.assert_no_send_or_duplicate()
    def test_read_only_destination_import_updates_capabilities(self):
        next(x for x in self.bridge.sources if x['id']=='b')['writable']=False;self.bridge.rows[0]['writable']=False
        self.assertEqual(self.sync.sync()['status'],'success')
        binding=self.owner.get_todo(self.id)['apple'];self.assertFalse(binding['can_modify']);self.assertFalse(binding['can_delete'])
        self.assertEqual(self.sync._maps()[0]['list_id'],'b');self.assert_no_send_or_duplicate()
    def test_changed_native_id_is_not_guessed_from_external_identity(self):
        self.bridge.rows[0]['reminder_id']='unproven-new-id'
        for _ in range(3):self.sync.sync()
        self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.original['alcor_id'])
        self.assertEqual(self.sync._maps()[0]['reminder_id'],self.before['reminder_id']);self.assert_no_send_or_duplicate()

if __name__=='__main__':unittest.main(verbosity=2)
