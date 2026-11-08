"""Queued completion and receipt recovery through the actual manager, synthetic Apple only."""
import copy
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-reminder-data-safety.py')))
COMMON=M['COMMON']
from apple_sync.contracts import process_operations


class Completion(unittest.TestCase):
    def setUp(self):
        self.base=M['ReminderSafety']();self.base.setUp()
        for key in ('case','owner','bridge','sync','id'):setattr(self,key,getattr(self.base,key))
        self.bridge.rows[0].update(due_on='2026-11-07',recurrence={'frequency':'daily'})
        self.sync.sync();self.owner.complete_todo(self.id,True);self.bridge.calls.clear()
    def tearDown(self):self.base.tearDown()
    def sends(self):return sum(op=='reminder_complete' for op,_ in self.bridge.calls)
    def test_normal_completion_and_next_scan_preserve_one_local_series(self):
        _,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertEqual(errors,[]);self.assertEqual(self.sends(),1)
        self.assertEqual(self.sync.sync()['status'],'success')
        local=self.owner.get_todo(self.id)
        self.assertEqual(local['due_on'],'2026-11-08');self.assertEqual(len(local['completion_history']),1)
        self.assertEqual(len(self.owner.list_todos()),1);self.assertEqual(self.owner.apple_operations(),[])
        self.assertEqual(self.sync.status()['scan_claims'],[])
    def test_other_process_group_claim_blocks_completion_before_begin(self):
        with COMMON['other_process_holds'](self.sync):_,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertTrue(errors);self.assertEqual(self.sends(),0)
        self.assertEqual(self.owner.apple_operations()[0]['state'],'pending')
    def test_completion_claim_blocks_other_process_and_allows_unrelated_group(self):
        other=COMMON['CommonClaims']();original=self.bridge.call
        def checked(op,**payload):
            if op=='reminder_complete':
                self.assertEqual(other.competing_scan(self.sync),'blocked')
                self.assertEqual(other.competing_scan(self.sync,'other'),'acquired')
            return original(op,**payload)
        with patch.object(self.bridge,'call',side_effect=checked):_,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertEqual(errors,[]);self.assertEqual(self.sends(),1)
    def test_owner_committed_but_manager_interrupted_recovers_without_resending(self):
        with patch.object(self.sync,'_commit_reminder_snapshot',side_effect=RuntimeError('lost after owner commit')):
            _,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertTrue(errors);self.assertEqual(self.owner.apple_operations(),[]);self.assertEqual(self.sends(),1)
        restarted=self.case.reminder(self.bridge)
        _,errors=process_operations(restarted,self.owner,'reminders')
        self.assertEqual(errors,[]);self.assertEqual(self.sends(),1)
        self.assertEqual(restarted.status()['scan_claims'],[])
        self.assertEqual(len(self.owner.get_todo(self.id)['completion_history']),1)
        self.assertEqual(restarted.sync()['status'],'success')
        self.assertEqual(len(self.owner.list_todos()),1)
    def test_receipt_before_owner_commit_recovers_locally_once(self):
        with patch.object(self.owner,'apple_finalize_reminder_completion',side_effect=RuntimeError('owner unavailable')):
            _,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertTrue(errors);self.assertEqual(self.sends(),1)
        restarted=self.case.reminder(self.bridge)
        _,errors=process_operations(restarted,self.owner,'reminders')
        self.assertEqual(errors,[]);self.assertEqual(self.sends(),1)
        self.assertEqual(len(self.owner.get_todo(self.id)['completion_history']),1)
        process_operations(restarted,self.owner,'reminders');self.assertEqual(self.sends(),1)
    def test_owner_edit_after_save_preserved_and_claim_kept(self):
        original=self.bridge.call
        def raced(op,**payload):
            result=original(op,**payload)
            if op=='reminder_complete':self.owner.update_todo(self.id,{'notes':'Edited while saving'})
            return result
        with patch.object(self.bridge,'call',side_effect=raced):_,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertTrue(errors);self.assertEqual(self.sends(),1)
        self.assertEqual(self.owner.get_todo(self.id)['notes'],'Edited while saving')
        self.assertTrue(self.sync.status()['scan_claims'])
        _,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertTrue(errors);self.assertEqual(self.sends(),1)
    def test_changed_claim_evidence_prevents_receipt_recovery(self):
        with patch.object(self.owner,'apple_finalize_reminder_completion',side_effect=RuntimeError('owner unavailable')):
            process_operations(self.sync,self.owner,'reminders')
        with self.sync._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='uncertain'")
        _,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertTrue(errors);self.assertEqual(self.sends(),1)
        self.assertTrue(self.owner.apple_operations())
    def test_similar_title_without_external_identity_never_becomes_successor(self):
        original=self.bridge.call
        def changed(op,**payload):
            result=original(op,**payload)
            if op=='reminder_complete':self.bridge.rows[-1]['external_id']='unrelated'
            return result
        with patch.object(self.bridge,'call',side_effect=changed):_,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertTrue(errors);self.assertEqual(self.sends(),1)
        self.assertEqual(self.owner.get_todo(self.id)['due_on'],'2026-11-07')
        self.assertTrue(self.sync.status()['scan_claims'])

    def test_proven_presave_rejection_releases_and_retry_succeeds(self):
        from apple_sync.bridge import BridgeError
        original=self.bridge.call
        def denied(op,**payload):
            if op=='reminder_complete':raise BridgeError('definite pre-save rejection',native_no_side_effects=True)
            return original(op,**payload)
        with patch.object(self.bridge,'call',side_effect=denied):_,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertTrue(errors);self.assertEqual(self.sends(),0)
        self.assertEqual(self.owner.apple_operations()[0]['state'],'pending')
        self.assertEqual(self.sync.status()['scan_claims'],[])
        _,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertEqual(errors,[]);self.assertEqual(self.sends(),1)

    def test_unqualified_native_error_is_unknown_and_never_retried(self):
        from apple_sync.bridge import BridgeError
        original=self.bridge.call;attempts=[]
        def denied(op,**payload):
            if op=='reminder_complete':attempts.append(op);raise BridgeError('failure without native proof')
            return original(op,**payload)
        with patch.object(self.bridge,'call',side_effect=denied):
            process_operations(self.sync,self.owner,'reminders');process_operations(self.sync,self.owner,'reminders')
        self.assertEqual(attempts,['reminder_complete'])
        self.assertEqual(self.owner.apple_operations()[0]['state'],'uncertain')
        self.assertTrue(self.sync.status()['scan_claims'])

    def test_presave_rejection_with_changed_claim_cannot_release(self):
        from apple_sync.bridge import BridgeError
        original=self.bridge.call
        def changed(op,**payload):
            if op=='reminder_complete':
                with self.sync._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                raise BridgeError('pre-save rejection but claim changed',native_no_side_effects=True)
            return original(op,**payload)
        with patch.object(self.bridge,'call',side_effect=changed):_,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertTrue(errors);self.assertTrue(self.sync.status()['scan_claims'])
        self.assertEqual(self.sends(),0)

    def test_unproven_immediate_successor_reply_does_not_finalize(self):
        original=self.bridge.call
        def next_only(op,**payload):
            result=original(op,**payload)
            if op=='reminder_complete':return {'reminder':copy.deepcopy(self.bridge.rows[-1])}
            return result
        with patch.object(self.bridge,'call',side_effect=next_only):_,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertTrue(errors);self.assertEqual(self.sends(),1)
        self.assertEqual(self.owner.get_todo(self.id)['due_on'],'2026-11-07')
        self.assertTrue(self.sync.status()['scan_claims'])

    def test_wrong_completed_snapshot_identity_keeps_unknown(self):
        original=self.bridge.call
        def wrong(op,**payload):
            result=original(op,**payload)
            if op=='reminder_complete':result['completed_snapshot']={**result['reminder'],'source_id':'other-account'}
            return result
        with patch.object(self.bridge,'call',side_effect=wrong):_,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertTrue(errors);self.assertEqual(self.sends(),1)
        self.assertEqual(self.owner.get_todo(self.id)['due_on'],'2026-11-07')
        self.assertTrue(self.sync.status()['scan_claims'])

    def test_confirmed_immediate_same_id_successor_stays_active_on_later_scans(self):
        original=self.bridge.call;native_id=self.bridge.rows[0]['reminder_id'];source=self.bridge.rows[0]['source_id']
        def immediate(op,**payload):
            if op!='reminder_complete':return original(op,**payload)
            result=original(op,**payload)
            completed=copy.deepcopy(result['reminder']);following=copy.deepcopy(self.bridge.rows[-1])
            following['reminder_id']=native_id;self.bridge.rows[:]=[following]
            return {'reminder':copy.deepcopy(following),'completed_snapshot':completed}
        with patch.object(self.bridge,'call',side_effect=immediate):_,errors=process_operations(self.sync,self.owner,'reminders')
        self.assertEqual(errors,[]);self.assertEqual(self.sends(),1)
        for _ in range(3):
            self.assertEqual(self.sync.sync()['status'],'success')
            local=self.owner.get_todo(self.id)
            self.assertEqual((local['due_on'],local['status']),('2026-11-08','todo'))
            self.assertEqual(len(local['completion_history']),1)
            self.assertEqual(local['apple']['source_id'],source)
            self.assertEqual(local['apple']['apple_id'],native_id)
            self.assertEqual(len(self.owner.list_todos()),1);self.assertEqual(len(self.bridge.rows),1)
        self.assertEqual(self.sends(),1)

if __name__=='__main__':unittest.main(verbosity=2)
