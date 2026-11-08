"""Only untouched pre-inventory reminder scan claims may be released."""
import copy
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-reminder-data-safety.py')))
from apple_sync import group_scan_claims as claims

class UnstartedScan(unittest.TestCase):
    def setUp(self):
        self.case=M['ReminderSafety']();self.case.setUp()
        for key in ('bridge','sync','owner','id'):setattr(self,key,getattr(self.case,key))
    def tearDown(self):self.case.tearDown()
    def run_changed_inventory(self, mutate=None, fail=False, invalid=False):
        original=self.bridge.call;changed=[]
        def intercepted(op,**payload):
            result=original(op,**payload)
            active=[row for row in claims.status(self.sync) if json.loads(row['window']).get('kind')=='reminder_scan']
            if op=='reminder_lists' and active and not changed:
                changed.append(active[0]['attempt_id'])
                if mutate:
                    with self.sync._connect() as db:mutate(db,active[0]['attempt_id'])
                if fail:raise RuntimeError('synthetic inventory read failure before item reads')
                if invalid:result['lists']=None
                else:result['lists'][0]['title']='Transiently changed inventory'
            return result
        with patch.object(self.bridge,'call',side_effect=intercepted):result=self.sync.sync()
        self.assertEqual(len(changed),1)
        return result
    def test_changed_second_inventory_releases_and_next_scan_recovers(self):
        before=copy.deepcopy(self.sync._maps());local=self.owner.get_todo(self.id)
        result=self.run_changed_inventory()
        self.assertNotEqual(result['status'],'success')
        self.assertEqual(self.sync._maps(),before)
        self.assertEqual(self.owner.get_todo(self.id),local)
        self.assertEqual(claims.status(self.sync),[])
        self.assertEqual(self.sync.sync()['status'],'success')
    def test_failed_second_inventory_releases_only_unstarted_scan(self):
        self.run_changed_inventory(fail=True)
        self.assertEqual(claims.status(self.sync),[])
        self.assertEqual(self.sync.sync()['status'],'success')
    def test_changed_claim_evidence_is_not_released(self):
        self.run_changed_inventory(lambda db,token:db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',('{"stage":"sending"}',token)))
        self.assertTrue(claims.status(self.sync))
        self.assertNotEqual(self.sync.sync()['status'],'success')
    def test_new_item_claim_is_not_released(self):
        self.run_changed_inventory(lambda db,token:db.execute('INSERT INTO calendar_scan_items VALUES (?,?)',(self.id,token)))
        self.assertTrue(claims.status(self.sync))
        self.assertNotEqual(self.sync.sync()['status'],'success')
    def test_advanced_attempt_is_not_released(self):
        self.run_changed_inventory(lambda db,token:db.execute("UPDATE calendar_scan_attempts SET state='uncertain' WHERE attempt_id=?",(token,)))
        self.assertTrue(claims.status(self.sync))

    def test_invalid_second_inventory_releases_before_item_phase(self):
        result=self.run_changed_inventory(invalid=True)
        self.assertEqual(result['status'],'error')
        self.assertEqual(claims.status(self.sync),[])
        self.assertEqual(self.sync.sync()['status'],'success')

    def test_actual_sqlite_rejection_rolls_back_release_and_retains_claim(self):
        original=claims.schema
        def reject_release(db,**kwargs):
            result=original(db,**kwargs)
            if db.execute("SELECT 1 FROM calendar_scan_attempts WHERE state='scanning'").fetchone():
                db.execute("CREATE TEMP TRIGGER fail_scan_release BEFORE UPDATE OF state ON calendar_scan_attempts WHEN NEW.state='not_sent' BEGIN SELECT RAISE(ABORT, 'synthetic SQLite release failure'); END")
            return result
        with patch.object(claims,'schema',side_effect=reject_release):result=self.run_changed_inventory()
        self.assertEqual(result['status'],'error')
        self.assertTrue(claims.status(self.sync))
        with self.sync._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM calendar_scan_attempts WHERE state='not_sent'").fetchone()[0],0)
        self.assertNotEqual(self.sync.sync()['status'],'success')

    def test_unknown_after_native_write_keeps_scan_and_never_resends(self):
        self.owner.update_todo(self.id,{'title':'Outbound before lost reply'})
        original=self.bridge.call
        def lost(op,**payload):
            result=original(op,**payload)
            if op=='reminder_upsert':raise RuntimeError('saved then lost reply')
            return result
        with patch.object(self.bridge,'call',side_effect=lost):result=self.sync.sync()
        self.assertEqual(result['status'],'error');self.assertTrue(claims.status(self.sync))
        self.assertEqual(sum(op=='reminder_upsert' for op,_ in self.bridge.calls),1)
        self.sync.sync()
        self.assertEqual(sum(op=='reminder_upsert' for op,_ in self.bridge.calls),1)

if __name__=='__main__':unittest.main(verbosity=2)
