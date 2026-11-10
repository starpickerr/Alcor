"""Full identity query absence, temporary owner/manager DBs and fake EventKit only."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))

class IdentityAbsence(unittest.TestCase):
    def setUp(self):
        self.c=M['Contracts']();self.c.setUp();self.b=M['NativeBridge']('calendar');self.s=self.c.calendar(self.b);self.o=self.c.business.calendar
        self.s.start_sync();self.s.sync();self.s._set('sync_deletions',True);self.mapping=self.s._maps()[0];self.id=self.mapping['alcor_id'];self.before=copy.deepcopy(self.s._local(self.id));self.original=self.b.call
        self.probe_status='absent';self.on_probe=None
        def bridge(op,**kw):
            if op=='status':return {**self.original(op,**kw),'identity_lookup_version':1}
            if op=='lookup_identity':
                self.b.calls.append((op,copy.deepcopy(kw)))
                if self.on_probe:self.on_probe()
                candidates=[copy.deepcopy(row) for row in self.b.rows if row['event_id']==kw['event_id'] or row.get('external_id')==kw['external_id']]
                return {'identity_observation':{'version':1,'status':'present' if candidates else self.probe_status,'event_id':kw['event_id'],'external_id':kw['external_id'],'calendar_id':kw['calendar_id'],'source_id':kw['expected_source_id'],'source_type':'caldav','candidates':candidates}}
            return self.original(op,**kw)
        self.b.call=bridge;self.b.calls.clear()
    def tearDown(self):self.c.tearDown()
    def assert_no_native_writes(self):self.assertFalse(any(op in ('upsert','delete','create_calendar','rename','delete_calendar') for op,_ in self.b.calls))
    def test_exact_complete_nonrecurring_absence_archives_once(self):
        self.b.rows=[];result=self.s.sync();self.assertEqual(result['deleted_local'],1,result)
        self.assertIsNone(self.s._local(self.id));self.assertEqual(self.s._maps()[0]['state'],'remote_deleted');self.assertEqual(len(self.o.list_apple_inbound_recoveries()),1)
        self.assertFalse(self.s.status()['scan_claims']);self.assert_no_native_writes();self.assertEqual(self.s.sync()['deleted_local'],0)
        recovery=self.o.list_apple_inbound_recoveries()[0]
        restored=self.o.restore_apple_inbound_recovery(recovery['id']);self.s.sync();self.s.sync()
        self.assertTrue(restored['ids']);self.assertIsNotNone(self.s._local(restored['ids'][0]));self.assert_no_native_writes()
    def test_moved_outside_window_or_changed_id_candidate_is_preserved(self):
        self.b.rows[0].update(event_id='new-identity',start_at='2032-11-07T01:00:00Z',end_at='2032-11-07T02:00:00Z',occurrence_date='2032-11-07T01:00:00Z')
        result=self.s.sync();self.assertEqual(result['deleted_local'],0);self.assertEqual(self.s._local(self.id),self.before);self.assert_no_native_writes()
    def test_unknown_source_or_incomplete_observation_cannot_archive(self):
        for value in ('unknown','source_unavailable','changed'):
            with self.subTest(value=value):
                self.probe_status=value;self.b.rows=[];result=self.s.sync();self.assertEqual(result['deleted_local'],0);self.assertEqual(self.s._local(self.id),self.before)
        self.assert_no_native_writes()
    def test_local_edit_during_identity_query_preserves_content(self):
        self.b.rows=[];self.on_probe=lambda:self.o.update_event(self.id,{'description':'Local change while absent'})
        result=self.s.sync();self.assertEqual(result['deleted_local'],0,result);self.assertEqual(self.s._local(self.id)['description'],'Local change while absent');self.assertEqual(self.o.list_apple_inbound_recoveries(),[]);self.assert_no_native_writes()
    def test_owner_cas_rejects_same_id_recreation(self):
        self.b.rows=[];original=self.o.apple_remove_calendar_inbound
        def recreated(*args):
            self.o.apple_remove_item(self.id);self.o.create_event({'id':self.id,'title':'New incarnation','start_at':'2026-11-07T01:00:00Z','end_at':'2026-11-07T02:00:00Z'})
            return original(*args)
        with patch.object(self.o,'apple_remove_calendar_inbound',side_effect=recreated):result=self.s.sync()
        self.assertEqual(result['deleted_local'],0,result);self.assertEqual(self.s._local(self.id)['title'],'New incarnation');self.assert_no_native_writes()
    def test_disabled_deletion_or_export_direction_preserves_local(self):
        self.b.rows=[];self.s._set('sync_deletions',False);self.assertEqual(self.s.sync()['deleted_local'],0);self.assertEqual(self.s._local(self.id),self.before)
        self.assert_no_native_writes()
    def test_original_old_helper_window_null_stays_pending(self):
        self.b.rows=[];self.b.call=self.original;result=self.s.sync();self.assertEqual(result['deleted_local'],0);self.assertEqual(self.s._local(self.id),self.before)
        self.assert_no_native_writes()
    def test_missing_generation_or_server_id_or_recurring_item_stays_pending(self):
        for field in ('generation','external','recurrence','unknown_recurrence','detached'):
            with self.subTest(field=field):
                other=IdentityAbsence();other.setUp()
                try:
                    if field=='generation':other.s._set('calendar-binding-generation:'+other.id,None)
                    elif field=='external':
                        with other.s._connect() as db:db.execute("UPDATE mappings SET external_id='' WHERE alcor_id=?",(other.id,))
                    else:
                        binding=copy.deepcopy(other.s._local(other.id)['apple'])
                        if field=='recurrence':binding['native']['recurrence']={'frequency':'daily'}
                        elif field=='unknown_recurrence':binding['native'].pop('recurrence')
                        else:binding['native']['is_detached']=True
                        other.o.apple_bind('item',other.id,binding)
                    other.b.rows=[];self.assertEqual(other.s.sync()['deleted_local'],0);self.assertIsNotNone(other.s._local(other.id));other.assert_no_native_writes()
                finally:other.tearDown()
    def test_export_only_does_not_archive(self):
        self.s.replace_links([{**self.s._stored_links()[0],'direction':'export','enabled':True}]);self.b.rows=[]
        self.assertEqual(self.s.sync()['deleted_local'],0);self.assertIsNotNone(self.s._local(self.id));self.assert_no_native_writes()
    def test_read_permission_loss_does_not_archive(self):
        self.b.rows=[];original=self.b.call
        self.b.call=lambda op,**kw:{'authorization':'denied'} if op=='status' else original(op,**kw)
        self.assertEqual(self.s.sync()['deleted_local'],0);self.assertEqual(self.s._local(self.id),self.before);self.assert_no_native_writes()
    def test_changed_manager_evidence_cannot_delete_owner(self):
        self.b.rows=[]
        def changed():
            with self.s._connect() as db:db.execute("UPDATE mappings SET conflict='newer evidence' WHERE alcor_id=?",(self.id,))
        self.on_probe=changed;result=self.s.sync();self.assertEqual(result['deleted_local'],0);self.assertEqual(self.s._local(self.id),self.before)
        self.assertTrue(self.s.status()['scan_claims']);self.assert_no_native_writes()
    def test_changed_claim_after_owner_commit_keeps_archive_and_unknown(self):
        self.b.rows=[];original=self.o.apple_remove_calendar_inbound
        def changed(*args):
            result=original(*args)
            with self.s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
            return result
        with patch.object(self.o,'apple_remove_calendar_inbound',side_effect=changed):result=self.s.sync()
        self.assertEqual(result['status'],'error');self.assertEqual(self.s._maps()[0]['state'],'active');self.assertTrue(self.s.status()['scan_claims'])
        self.assertTrue(self.o.list_apple_inbound_recoveries());self.assert_no_native_writes();self.s.sync();self.assert_no_native_writes()

if __name__=='__main__':unittest.main(verbosity=2)
