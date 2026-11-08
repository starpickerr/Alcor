"""Local move intent and native placement are compared against durable bindings/links."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-reminder-move-contention.py')))

class Intent(unittest.TestCase):
    def setUp(self):
        self.base=M['Contention']();self.base.setUp()
        for key in ('sync','owner','bridge','id','target','original'):setattr(self,key,getattr(self.base,key))
        self.bridge.sources.append({**self.bridge.sources[0],'id':'c','title':'Third'})
        # Create the third ordinary link before testing a changed target.
        self.owner.update_todo(self.id,{'category_id':self.original['alcor_id']});self.sync.sync()
        self.third=next(row['alcor_id'] for row in self.sync._stored_links() if row['apple_id']=='c')
        self.owner.update_todo(self.id,{'category_id':self.target});self.bridge.calls.clear()
    def tearDown(self):self.base.tearDown()
    def test_held_b_preserves_local_choice_and_can_retry(self):
        before=copy.deepcopy(self.sync._maps()[0])
        with M['competing_work'](self.sync):result=self.sync.sync()
        self.assertNotEqual(result['status'],'success')
        self.assertEqual(self.sync._maps()[0],before)
        self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.target)
        self.assertFalse(any(op=='reminder_upsert' for op,_ in self.bridge.calls))
        self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(self.sync._maps()[0]['list_id'],'b')
    def test_user_withdraws_waiting_b_then_ordinary_inbound_update_works(self):
        with M['competing_work'](self.sync):self.sync.sync()
        self.owner.update_todo(self.id,{'category_id':self.original['alcor_id']})
        self.bridge.rows[0]['title']='Fresh inbound after withdrawal'
        result=self.sync.sync()
        self.assertEqual(result['status'],'success')
        self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.original['alcor_id'])
        self.assertEqual(self.owner.get_todo(self.id)['title'],'Fresh inbound after withdrawal')
        self.assertEqual(self.sync._maps()[0]['list_id'],'a')
        self.assertFalse(any(op=='reminder_upsert' for op,_ in self.bridge.calls))
    def test_user_changes_waiting_target_to_c_no_old_b_move_is_sent(self):
        with M['competing_work'](self.sync):
            self.sync.sync();self.owner.update_todo(self.id,{'category_id':self.third})
            result=self.sync.sync()
        self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.third)
        self.assertEqual(self.sync._maps()[0]['list_id'],'c')
        self.assertEqual([payload['list_id'] for op,payload in self.bridge.calls if op=='reminder_upsert'],['c'])
        self.assertEqual(self.sync.sync()['status'],'success')
    def test_late_withdrawal_or_new_target_survives_old_b_native_response(self):
        for choice in ('withdraw','third'):
            with self.subTest(choice=choice):
                # Use an independent full synthetic owner for each in-flight outcome.
                other=Intent();other.setUp()
                try:
                    destination=other.original['alcor_id'] if choice=='withdraw' else other.third
                    original_call=other.bridge.call
                    def changed(op,**payload):
                        result=original_call(op,**payload)
                        if op=='reminder_upsert':other.owner.update_todo(other.id,{'category_id':destination})
                        return result
                    with patch.object(other.bridge,'call',side_effect=changed):result=other.sync.sync()
                    self.assertNotEqual(result['status'],'success')
                    self.assertEqual(other.owner.get_todo(other.id)['category_id'],destination)
                    self.assertEqual(other.sync._maps()[0]['list_id'],'a')
                    self.assertTrue(other.sync.status()['scan_claims'])
                    before=sum(op=='reminder_upsert' for op,_ in other.bridge.calls);other.sync.sync()
                    self.assertEqual(sum(op=='reminder_upsert' for op,_ in other.bridge.calls),before)
                finally:other.tearDown()
    def test_native_cross_account_move_is_retained_without_duplicate_or_guess(self):
        self.owner.update_todo(self.id,{'category_id':self.original['alcor_id']})
        self.bridge.rows[0].update(list_id='b',source_id='account2')
        next(row for row in self.bridge.sources if row['id']=='b')['source_id']='account2'
        for _ in range(3):
            result=self.sync.sync()
            self.assertNotEqual(result['status'],'success')
            self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.original['alcor_id'])
            self.assertEqual(len(self.owner.list_todos()),1)
            self.assertEqual(len(self.sync._maps()),1)
            self.assertEqual(len(self.bridge.rows),1)
        self.assertEqual(self.sync._maps()[0]['state'],'conflict')
        self.assertFalse(any(op=='reminder_upsert' for op,_ in self.bridge.calls))
    def test_ordinary_same_group_inbound_update_is_not_deferred(self):
        self.owner.update_todo(self.id,{'category_id':self.original['alcor_id']})
        self.bridge.rows[0]['notes']='Normal native edit'
        result=self.sync.sync()
        self.assertEqual(result['status'],'success')
        self.assertEqual(self.owner.get_todo(self.id)['notes'],'Normal native edit')
        self.assertEqual(self.sync._maps()[0]['state'],'active')

    def test_unverified_durable_placement_is_not_treated_as_waiting_move(self):
        original_call=self.bridge.call;changed=[]
        def invalid_baseline(op,**payload):
            result=original_call(op,**payload)
            if op=='reminders' and payload.get('list_ids')==['a'] and not changed:
                binding=copy.deepcopy(self.owner.get_todo(self.id)['apple'])
                binding['native']['list_id']='unrelated-old-container'
                self.owner.apple_bind('item',self.id,binding);changed.append(True)
            return result
        with M['competing_work'](self.sync),patch.object(self.bridge,'call',side_effect=invalid_baseline):result=self.sync.sync()
        self.assertEqual(len(changed),1)
        self.assertNotEqual(result['status'],'success')
        self.assertEqual(self.sync._maps()[0]['state'],'conflict')
        self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.target)
        self.assertFalse(any(op=='reminder_upsert' for op,_ in self.bridge.calls))

    def test_target_changed_before_capture_never_sends_old_b(self):
        original_capture=self.owner.apple_capture_reminder_sync;changed=[]
        def change(identity,target_category_id=None):
            if target_category_id==self.target and not changed:
                self.owner.update_todo(self.id,{'category_id':self.third});changed.append(True)
            return original_capture(identity,target_category_id)
        with patch.object(self.owner,'apple_capture_reminder_sync',side_effect=change):self.sync.sync()
        self.assertEqual(len(changed),1)
        self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.third)
        self.assertFalse(any(op=='reminder_upsert' and payload['list_id']=='b' for op,payload in self.bridge.calls))
        self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(self.sync._maps()[0]['list_id'],'c')

    def test_target_changed_after_capture_before_verify_never_sends_old_b(self):
        original_verify=self.owner.apple_verify_reminder_sync;changed=[]
        def change(expected,native=None):
            if expected.get('target_category_id')==self.target and not changed:
                self.owner.update_todo(self.id,{'category_id':self.third});changed.append(True)
            return original_verify(expected,native)
        with patch.object(self.owner,'apple_verify_reminder_sync',side_effect=change):self.sync.sync()
        self.assertEqual(len(changed),1)
        self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.third)
        self.assertFalse(any(op=='reminder_upsert' and payload['list_id']=='b' for op,payload in self.bridge.calls))
        self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(self.sync._maps()[0]['list_id'],'c')

if __name__=='__main__':unittest.main(verbosity=2)
