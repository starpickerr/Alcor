"""Only demonstrably unstarted operations may release cooperative claims."""
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

FIXTURES = {}
FIXTURES['move']=runpy.run_path(str(Path(__file__).with_name('apple-calendar-move-cas.py')))['CalendarMove']
FIXTURES['rebind']=runpy.run_path(str(Path(__file__).with_name('apple-calendar-rebind-safe.py')))['Rebind']

class Unstarted(unittest.TestCase):
    def test_move_owner_preflight_failure_releases_without_writes(self):
        case=FIXTURES['move']();case.setUp()
        try:
            with patch.object(case.owner,'apple_verify_calendar_move',side_effect=ValueError('synthetic preflight conflict')):
                result=case.sync.sync()
            self.assertNotEqual(result['status'],'success')
            self.assertEqual(case.writes(),[])
            self.assertEqual(case.sync.status()['scan_claims'],[])
            self.assertTrue(case.sync._get('calendar_move_errors'))
            current=case.sync._maps()[0]
            self.assertEqual(current['state'],'conflict')
            self.assertEqual({**current,'state':case.mapping['state'],'conflict':case.mapping['conflict']},case.mapping)
        finally:case.tearDown()

    def test_move_changed_claim_is_not_released(self):
        case=FIXTURES['move']();case.setUp()
        try:
            def changed(*args):
                with case.sync._connect() as db:
                    db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                raise ValueError('synthetic concurrent evidence change')
            with patch.object(case.owner,'apple_verify_calendar_move',side_effect=changed):case.sync.sync()
            self.assertEqual(case.writes(),[])
            self.assertEqual(len(case.sync.status()['scan_claims']),2)
        finally:case.tearDown()

    def test_move_another_process_advances_sending_and_claim_stays(self):
        import os,subprocess,sys
        case=FIXTURES['move']();case.setUp()
        try:
            def advance(*args):
                script="import json,sqlite3,sys;db=sqlite3.connect(sys.argv[1]);rows=db.execute(\"SELECT attempt_id,evidence FROM calendar_scan_attempts WHERE state='scanning'\").fetchall();[(db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',(json.dumps({**json.loads(e),'stage':'sending'}),t))) for t,e in rows];db.commit()"
                result=subprocess.run([sys.executable,'-B','-c',script,str(case.sync.db_path)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                raise ValueError('synthetic claim advanced')
            with patch.object(case.owner,'apple_verify_calendar_move',side_effect=advance):case.sync.sync()
            self.assertEqual(case.writes(),[])
            self.assertEqual(len(case.sync.status()['scan_claims']),2)
        finally:case.tearDown()

    def test_rebind_duplicate_identity_releases_without_owner_commit(self):
        case=FIXTURES['rebind']();case.setUp()
        try:
            case.bridge.rows.append({**case.bridge.rows[0],'event_id':'duplicate'})
            with patch.object(case.owner,'apple_commit_calendar_rebind',wraps=case.owner.apple_commit_calendar_rebind) as commit:
                result=case.sync.sync()
            self.assertNotEqual(result['status'],'success')
            self.assertEqual(commit.call_count,0)
            self.assertEqual(case.sync.status()['scan_claims'],[])
            self.assertEqual(case.sync._maps()[0]['calendar_id'],'a')
            self.assertTrue(case.sync._get('category_conflicts'))
        finally:case.tearDown()

    def test_rebind_dispatched_owner_error_remains_unknown(self):
        case=FIXTURES['rebind']();case.setUp()
        try:
            with patch.object(case.owner,'apple_commit_calendar_rebind',side_effect=RuntimeError('RPC outcome unknown')):
                case.sync.sync()
            self.assertEqual(len(case.sync.status()['scan_claims']),2)
        finally:case.tearDown()

if __name__=='__main__':unittest.main(verbosity=2)
