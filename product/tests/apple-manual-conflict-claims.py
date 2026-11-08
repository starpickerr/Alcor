"""Existing manual conflict choices retain normal behavior and unknown effects."""
import runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))

class ManualConflicts(unittest.TestCase):
    def setUp(self):self.base=M['CommonClaims']();self.base.setUp()
    def tearDown(self):self.base.tearDown()
    def setup(self,kind):
        sync,bridge,owner=self.base.setup_domain(kind);mapping=sync._maps()[0];identity=mapping['todo_id' if kind=='reminders' else 'alcor_id']
        (owner.update_todo if kind=='reminders' else owner.update_event)(identity,{'title':'Local chosen title'})
        bridge.rows[0]['title']='Native chosen title';sync._conflict(mapping,'Both changed');bridge.calls.clear()
        return sync,bridge,owner,identity
    def test_both_existing_choices_still_apply_without_duplicate(self):
        for kind in ('calendar','reminders'):
            for choice in ('keep_apple','keep_alcor'):
                with self.subTest(kind=kind,choice=choice):
                    sync,bridge,owner,identity=self.setup(kind);sync.resolve_conflict(identity,choice)
                    title='Native chosen title' if choice=='keep_apple' else 'Local chosen title'
                    self.assertEqual(sync._local(identity)['title'],title);self.assertEqual(bridge.rows[0]['title'],title)
                    self.assertEqual(sync._maps()[0]['state'],'active');self.assertEqual(len(sync._maps()),1);self.assertEqual(len(bridge.rows),1)
                    self.assertEqual(sync.status()['scan_claims'],[])
    def test_local_edit_at_owner_apply_is_not_overwritten_or_permanently_held(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner,identity=self.setup(kind);method='apple_apply_reminder_inbound' if kind=='reminders' else 'apple_apply_calendar_inbound';original=getattr(owner,method)
                def apply(*args):
                    (owner.update_todo if kind=='reminders' else owner.update_event)(identity,{'title':'Latest local choice'})
                    return original(*args)
                with patch.object(owner,method,side_effect=apply),self.assertRaisesRegex(ValueError,'INBOUND_CHANGED:'):sync.resolve_conflict(identity,'keep_apple')
                self.assertEqual(sync._local(identity)['title'],'Latest local choice');self.assertEqual(sync._maps()[0]['state'],'conflict');self.assertEqual(sync.status()['scan_claims'],[])
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in bridge.calls))
    def test_native_save_response_loss_is_visible_and_never_repeated(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner,identity=self.setup(kind);original=bridge.call;sent=[]
                def call(op,**payload):
                    value=original(op,**payload)
                    if op in ('upsert','reminder_upsert'):sent.append(payload);raise RuntimeError('synthetic response loss')
                    return value
                with patch.object(bridge,'call',side_effect=call),self.assertRaises(RuntimeError):sync.resolve_conflict(identity,'keep_alcor')
                self.assertEqual(len(sent),1);self.assertTrue(sync.status()['scan_claims'])
                with self.assertRaises(ValueError):sync.resolve_conflict(identity,'keep_alcor')
                self.assertEqual(sum(op in ('upsert','reminder_upsert') for op,_ in bridge.calls),1)
    def test_two_process_owner_commit_holds_group(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner,identity=self.setup(kind);method='apple_apply_reminder_inbound' if kind=='reminders' else 'apple_apply_calendar_inbound';original=getattr(owner,method)
                def apply(*args):
                    self.assertEqual(self.base.competing_scan(sync,'a'),'blocked');self.assertEqual(self.base.competing_scan(sync,'unrelated'),'acquired')
                    return original(*args)
                with patch.object(owner,method,side_effect=apply):sync.resolve_conflict(identity,'keep_apple')

if __name__=='__main__':unittest.main(verbosity=2)
