"""Pending identity and unknown commit summaries never mutate retained evidence."""
import json,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-reminder-inbound-move.py')))
I=runpy.run_path(str(Path(__file__).with_name('apple-reminder-identity-candidates.py')))

class RetainedStatus(unittest.TestCase):
    def test_unknown_owner_commit_is_visible_without_retry_or_evidence_loss(self):
        case=M['InboundMove']();case.setUp()
        try:
            original=case.owner.apple_apply_reminder_move;calls=[]
            def lost(*args):
                calls.append(True);original(*args);raise RuntimeError('synthetic committed owner response lost')
            with patch.object(case.owner,'apple_apply_reminder_move',side_effect=lost):case.sync.sync()
            with case.sync._connect() as db:before=[dict(x) for x in db.execute('SELECT * FROM calendar_scan_attempts ORDER BY attempt_id')]
            for _ in range(2):
                rows=case.sync.status()['scan_claims'];self.assertEqual(len(rows),2)
                self.assertTrue(all(x['state']=='uncertain' and x['stage']=='owner_claiming' for x in rows))
                self.assertTrue(all('response lost' in x['reason'] for x in rows))
            with case.sync._connect() as db:self.assertEqual([dict(x) for x in db.execute('SELECT * FROM calendar_scan_attempts ORDER BY attempt_id')],before)
            case.sync.sync();self.assertEqual(len(calls),1);case.assert_no_send_or_duplicate()
        finally:case.tearDown()
    def test_candidate_first_evidence_survives_restart_and_later_native_edit(self):
        case=I['IdentityCandidates']();case.setUp()
        try:
            case.bridge.rows[0].update(list_id='b',source_id='account2');case.sync.sync()
            first=case.sync.status()['identity_candidates'];self.assertEqual(len(first),1)
            case.bridge.rows[0]['title']='Later native candidate title'
            restarted=case.case.reminder(case.bridge);restarted.sync()
            self.assertEqual(restarted.status()['identity_candidates'],first)
            self.assertEqual(len(case.owner.list_todos()),1);self.assertEqual(len(restarted._maps()),1);case.assert_no_native_write()
        finally:case.tearDown()

if __name__=='__main__':unittest.main(verbosity=2)
