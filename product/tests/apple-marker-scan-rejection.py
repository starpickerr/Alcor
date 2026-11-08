"""Known rolled-back marker work must not strand a healthy group; fake native only."""
import copy,runpy,unittest,subprocess,sys
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-marker-owner-cas.py')))
case=M['M']['case']

class MarkerScanRejection(unittest.TestCase):
    def test_known_rollback_preserves_marker_and_next_healthy_update(self):
        c=M['MarkerOwnerCAS']()
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                local,remote=c.marker(s,o,b,verified=True);original=o.apple_bind_marker_if_unchanged
                healthy=s._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id'];calls=[]
                def edit(*args):
                    calls.append(args)
                    (o.update_event if kind=='calendar' else o.update_todo)(local['id'],{'url':'https://example.com/user-choice'})
                    return original(*args)
                with patch.object(o,'apple_bind_marker_if_unchanged',side_effect=edit):s.sync()
                self.assertEqual(s._local(local['id'])['url'],'https://example.com/user-choice')
                self.assertEqual(s.status()['scan_claims'],[])
                self.assertEqual(len(s._maps()),1)
                for index in range(2):
                    b.rows[0]['title']='Healthy changed '+str(index)
                    with patch.object(o,'apple_bind_marker_if_unchanged',side_effect=AssertionError('pending marker retried')):s.sync()
                    self.assertEqual(s._local(healthy)['title'],b.rows[0]['title'])
                    self.assertEqual(s._local(local['id'])['url'],'https://example.com/user-choice')
                    self.assertEqual(s._local(local['id'])['apple']['apple_id'],'marker-native')
                    self.assertEqual(len(s._maps()),1);self.assertEqual(s.status()['scan_claims'],[])
                self.assertEqual(len(calls),1)
                self.assertTrue(s.status()['marker_conflicts'])
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))
    def test_unknown_commit_or_changed_claim_remains_held(self):
        c=M['MarkerOwnerCAS']()
        for kind in ('calendar','reminders'):
            for alteration in ('committed_lost','evidence','state','items'):
                with self.subTest(kind=kind,alteration=alteration),case(kind) as (s,o,b):
                    local,remote=c.marker(s,o,b,verified=True);original=o.apple_bind_marker_if_unchanged
                    healthy=s._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id'];before=s._local(healthy)['title'];calls=[]
                    def fail(*args):
                        calls.append(args)
                        if alteration=='committed_lost':original(*args);raise RuntimeError('actual commit reply lost')
                        (o.update_event if kind=='calendar' else o.update_todo)(local['id'],{'url':'https://example.com/user-choice'})
                        with s._connect() as db:
                            if alteration=='evidence':db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                            elif alteration=='state':
                                result=subprocess.run([sys.executable,'-c',"import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); db.execute(\"UPDATE calendar_scan_attempts SET state='sending' WHERE state='scanning'\"); db.commit()",str(s.db_path)],capture_output=True,text=True)
                                self.assertEqual(result.returncode,0,result.stderr)
                            else:db.execute("DELETE FROM calendar_scan_items WHERE local_id=?",(local['id'],))
                        return original(*args)
                    with patch.object(o,'apple_bind_marker_if_unchanged',side_effect=fail):s.sync()
                    b.rows[0]['title']='Must wait';s.sync()
                    self.assertEqual(len(calls),1);self.assertTrue(s.status()['scan_claims'])
                    self.assertEqual(s._local(healthy)['title'],before);self.assertEqual(len(s._maps()),1)
                    self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))

    def test_earlier_unknown_item_effect_prevents_scan_release(self):
        c=M['MarkerOwnerCAS']()
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                local,remote=c.marker(s,o,b,verified=True);original=o.apple_bind_marker_if_unchanged
                method='apple_apply_calendar_inbound' if kind=='calendar' else 'apple_apply_reminder_inbound'
                apply=getattr(o,method);calls=[]
                def lost(*args,**kwargs):apply(*args,**kwargs);raise RuntimeError('prior item commit response lost')
                def edit(*args):
                    calls.append(args)
                    (o.update_event if kind=='calendar' else o.update_todo)(local['id'],{'url':'https://example.com/user-choice'})
                    return original(*args)
                with patch.object(o,method,side_effect=lost),patch.object(o,'apple_bind_marker_if_unchanged',side_effect=edit):s.sync()
                self.assertTrue(s.status()['scan_claims']);self.assertEqual(s.status()['marker_conflicts'],[])
                self.assertEqual(len(calls),0 if kind=='calendar' else 1)
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))

    def test_pending_marker_survives_reconnect_and_missing_native_but_new_item_syncs(self):
        c=M['MarkerOwnerCAS']()
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                local,remote=c.marker(s,o,b,verified=True);original=o.apple_bind_marker_if_unchanged
                def edit(*args):
                    (o.update_event if kind=='calendar' else o.update_todo)(local['id'],{'url':'https://example.com/user-choice'})
                    return original(*args)
                with patch.object(o,'apple_bind_marker_if_unchanged',side_effect=edit):s.sync()
                b.rows=[b.rows[0]];b.calls.clear();s.start_sync()
                fresh=copy.deepcopy(remote);fresh.pop('alcor_id');fresh['title']='Fresh normal local'
                if kind=='calendar':
                    from apple_sync.calendar import _payload
                    new=o.create_event(_payload(fresh,local['category_id']))
                else:
                    from apple_sync.reminders import _payload
                    new=o.create_todo(_payload(fresh,local['category_id']))
                s.sync()
                self.assertEqual(s._local(local['id'])['url'],'https://example.com/user-choice')
                self.assertEqual(s._local(local['id'])['apple']['apple_id'],'marker-native')
                self.assertTrue(s._local(new['id']).get('apple'));self.assertEqual(len(s._maps()),2)
                writes=[payload for op,payload in b.calls if op in ('upsert','reminder_upsert')]
                self.assertEqual(len(writes),1);self.assertEqual(writes[0]['alcor_id'],new['id'])
                self.assertEqual(s.status()['scan_claims'],[])

    def test_unbound_historical_marker_stays_pending_across_reconnect_without_duplicates(self):
        c=M['MarkerOwnerCAS']()
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                local,remote=c.marker(s,o,b);before=copy.deepcopy(s._local(local['id']));native=copy.deepcopy(b.rows)
                for restart in (False,True,False):
                    (s.start_sync if restart else s.sync)()
                    self.assertEqual(s._local(local['id']),before);self.assertEqual(b.rows,native)
                    self.assertEqual(len(s._maps()),1);self.assertEqual(s.status()['scan_claims'],[])
                    self.assertEqual(len(s.status()['marker_conflicts']),1)
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))

if __name__=='__main__':unittest.main(verbosity=2)
