"""Same external IDs in two sources, observed through ordinary full synchronization."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-native-recurring-delete.py')))

class CalendarCandidates(unittest.TestCase):
    def test_full_selection_coexists_updates_and_deletes_only_chosen_source(self):
        with M['case'](all_sources=True) as (s,o,b):
            self.assertEqual(len(o.list_events()),4);self.assertFalse(s.status()['scan_claims'])
            foreign=next(r for r in o.list_events() if r['apple']['source_id']=='account2')
            b.rows[-1]['title']='Independent other source edit';self.assertEqual(s.sync()['status'],'success')
            self.assertEqual(o.get_event(foreign['id'])['title'],'Independent other source edit')
            item,op,args,local,plan=M['preview'](s,o,'future')
            M['owner_commit'](o,'calendar','delete_event',args,op,local['snapshot'],plan['native'])
            M['run'](s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw))
            self.assertEqual(s.sync()['status'],'success');self.assertEqual(s.sync()['status'],'success')
            self.assertEqual(len(o.list_events()),2);self.assertEqual(o.get_event(foreign['id'])['title'],'Independent other source edit')
            self.assertFalse(s.status()['scan_claims']);self.assertFalse(s.status().get('identity_candidates'))
    def test_missing_original_candidate_is_held_without_duplicate_or_group_block(self):
        with M['case']() as (s,o,b):
            original=next(r for r in o.list_events() if r['start_at'].startswith('2026-11-09'))
            b.rows=[r for r in b.rows if r['event_id']!='series-9']
            fresh={**copy.deepcopy(b.rows[-1]),'event_id':'genuinely-new','external_id':'different-external','title':'Healthy independent event'}
            b.rows.append(fresh);b.calls.clear()
            s.start_sync();s.sync();s.sync()
            self.assertEqual(len(o.list_events()),4);self.assertTrue(o.get_event(original['id']))
            self.assertEqual(sum(r['title']=='Healthy independent event' for r in o.list_events()),1)
            self.assertEqual(len(s.status()['identity_candidates']),1);self.assertFalse(s.status()['scan_claims'])
            self.assertFalse(any(op in ('delete','upsert') for op,_ in b.calls))
            fresh['title']='Healthy later edit';s.sync()
            self.assertEqual(sum(r['title']=='Healthy later edit' for r in o.list_events()),1)
    def test_unavailable_original_stays_candidate_then_verified_return_can_import(self):
        with M['case']() as (s,o,b):
            original=b.call
            def offline(op,**kw):
                if op=='lookup' and kw.get('calendar_id')=='a':raise RuntimeError('original source read unavailable')
                return original(op,**kw)
            with patch.object(b,'call',side_effect=offline):s.start_sync();s.sync()
            self.assertEqual(len(o.list_events()),3);self.assertEqual(len(s.status()['identity_candidates']),1);self.assertFalse(s.status()['scan_claims'])
            self.assertEqual(s.sync()['status'],'success');self.assertEqual(len(o.list_events()),4);self.assertFalse(s.status()['identity_candidates'])
    def test_changed_claim_cannot_import_or_write_candidate_evidence(self):
        with M['case']() as (s,o,b):
            original=b.call;changed=[]
            def raced(op,**kw):
                result=original(op,**kw)
                if op=='lookup' and kw.get('calendar_id')=='a' and not changed:
                    with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id IN (SELECT attempt_id FROM calendar_scan_groups WHERE group_id='other')",('{"changed":true}',))
                    changed.append(True)
                return result
            with patch.object(b,'call',side_effect=raced):s.start_sync()
            self.assertTrue(changed);self.assertEqual(len(o.list_events()),3);self.assertFalse(s.status()['identity_candidates'])
            self.assertEqual({row['group_id'] for row in s.status()['scan_claims']},{'other'})
    def test_owner_database_error_is_not_converted_to_missing_identity(self):
        with M['case']() as (s,o,b):
            original=o.get_event
            def broken(identity):raise RuntimeError('synthetic owner database read error')
            # The direct helper call isolates the entry from unrelated owner reads during sync.
            from apple_sync.group_scan_claims import acquire
            token=acquire(s,'account2','other',['synthetic'])
            with patch.object(o,'get_event',side_effect=broken),self.assertRaisesRegex(RuntimeError,'database read error'):
                s._hold_cross_container_candidate(b.rows[-1],s._maps(),token)
            self.assertFalse(s.status()['identity_candidates']);self.assertEqual(len(o.list_events()),3)

if __name__=='__main__':unittest.main(verbosity=2)
