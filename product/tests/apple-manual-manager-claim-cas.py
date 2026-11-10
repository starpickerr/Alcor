"""Manager state may commit only while the same claim is still valid."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-manual-conflict-claims.py')))

class ManualManagerCAS(unittest.TestCase):
    def test_owner_committed_but_claim_changed_preserves_manager_state(self):
        for kind in ('calendar','reminders'):
            for change in ('evidence','state'):
                with self.subTest(kind=kind,change=change):
                    case=M['ManualConflicts']();case.setUp()
                    try:
                        sync,bridge,owner,identity=case.setup(kind);before=copy.deepcopy(sync._maps())
                        baseline=sync._reminder_baselines(identity) if kind=='reminders' else sync._get('item-metadata:'+identity)
                        method='_commit_reminder_snapshot' if kind=='reminders' else '_commit_inbound_snapshot';original=getattr(sync,method)
                        def commit(*args,**kwargs):
                            with sync._connect() as db:
                                db.execute("UPDATE calendar_scan_attempts SET "+("evidence='{}'" if change=='evidence' else "state='sending'")+" WHERE state='scanning'")
                            return original(*args,**kwargs)
                        with patch.object(sync,method,side_effect=commit):
                            with self.assertRaises(ValueError):sync.resolve_conflict(identity,'keep_apple')
                        self.assertEqual(sync._maps(),before)
                        self.assertEqual(sync._reminder_baselines(identity) if kind=='reminders' else sync._get('item-metadata:'+identity),baseline)
                        self.assertEqual(sync._local(identity)['title'],'Native chosen title')
                        self.assertTrue(sync.status()['scan_claims'])
                        with self.assertRaises(ValueError):sync.resolve_conflict(identity,'keep_apple')
                        self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in bridge.calls))
                    finally:case.tearDown()

if __name__=='__main__':unittest.main(verbosity=2)
