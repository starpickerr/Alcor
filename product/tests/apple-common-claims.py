"""Two-process contention against the real temporary manager SQLite."""
import contextlib
import json
import os
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
M = runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))


@contextlib.contextmanager
def other_process_holds(sync, group='a', item=''):
    code = '''import sqlite3,sys
from apple_sync import group_scan_claims as claims
class Store:
 def _connect(self):
  db=sqlite3.connect(sys.argv[1]);db.row_factory=sqlite3.Row;return db
 def clock(self):return '2026-11-08T00:00:00Z'
s=Store();token=claims.acquire(s,'account1',sys.argv[2],['synthetic-window'])
if sys.argv[3]:claims.claim_items(s,token,[sys.argv[3]])
print(token,flush=True);sys.stdin.readline()
claims.uncertain(s,token)
'''
    child = subprocess.Popen([sys.executable, '-B', '-c', code, str(sync.db_path), group, item],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, 'PYTHONPATH': str(ROOT/'packages/alcor-apple-sync/python')})
    try:
        token = child.stdout.readline().strip()
        if not token:
            raise AssertionError(child.communicate()[1])
        yield token
    finally:
        out, err = child.communicate('\n', timeout=10)
        if child.returncode:
            raise AssertionError((child.returncode, out, err))


class CommonClaims(unittest.TestCase):
    def setUp(self):
        self.case=M['Contracts']();self.case.setUp()

    def tearDown(self):
        self.case.tearDown()

    def setup_domain(self, kind):
        bridge=M['NativeBridge'](kind)
        sync=(self.case.calendar if kind=='calendar' else self.case.reminder)(bridge)
        sync.start_sync();bridge.calls.clear()
        return sync, bridge, self.case.business.calendar if kind=='calendar' else self.case.business.tasks

    def test_replace_links_respects_other_process_group_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind)
                before=sync._stored_links()
                with other_process_holds(sync):
                    with self.assertRaises(ValueError):
                        sync.replace_links([])
                self.assertEqual(sync._stored_links(),before)

    def test_outbox_does_not_delete_under_other_process_group_claim(self):
        from apple_sync.contracts import process_operations
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind)
                key='alcor_id' if kind=='calendar' else 'todo_id';identity=sync._maps()[0][key]
                (owner.delete_event if kind=='calendar' else owner.delete_todo)(identity)
                with other_process_holds(sync):
                    _,errors=process_operations(sync,owner,kind)
                self.assertTrue(errors)
                self.assertFalse(any(op in ('delete','reminder_delete') for op,_ in bridge.calls))
                self.assertEqual(len(bridge.rows),1)
                self.assertTrue((owner.get_event if kind=='calendar' else owner.get_todo)(identity))


    def competing_scan(self, sync, group='a'):
        code = "import sys\nfrom apple_sync.calendar import AppleCalendarSync\nfrom apple_sync import group_scan_claims as c\ns=AppleCalendarSync(bridge=object(),db_path=sys.argv[1],calendar_port=object())\ntry:t=c.acquire(s,'account1',sys.argv[2],[]);c.uncertain(s,t);print('acquired')\nexcept c.ScanHeld:print('blocked')\n"
        # Instantiate only the DB interface: reminder and calendar manager schemas differ.
        code=code.replace("from apple_sync.calendar import AppleCalendarSync", "import sqlite3\nclass AppleCalendarSync:\n def __init__(self,**kw):self.path=kw['db_path']\n def _connect(self):\n  d=sqlite3.connect(self.path);d.row_factory=sqlite3.Row;return d\n def clock(self):return '2026-11-08T00:00:00Z'")
        child=subprocess.run([sys.executable,'-B','-c',code,str(sync.db_path),group],capture_output=True,text=True,
            env={**os.environ,'PYTHONPATH':str(ROOT/'packages/alcor-apple-sync/python')})
        self.assertEqual(child.returncode,0,child.stderr)
        return child.stdout.strip()

    def test_outbox_holds_before_native_and_unrelated_group_can_proceed(self):
        from apple_sync.contracts import process_operations
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind)
                identity=sync._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id']
                (owner.delete_event if kind=='calendar' else owner.delete_todo)(identity)
                original=bridge.call
                def checked(op,**payload):
                    if op in ('delete','reminder_delete'):
                        self.assertEqual(self.competing_scan(sync),'blocked')
                        self.assertEqual(self.competing_scan(sync,'unrelated'),'acquired')
                    return original(op,**payload)
                with patch.object(bridge,'call',side_effect=checked):_,errors=process_operations(sync,owner,kind)
                self.assertEqual(errors,[])
                self.assertEqual(bridge.rows,[])
                self.assertFalse(any(row['group_id']=='a' for row in sync.status()['scan_claims']))
                self.assertEqual(owner.apple_operations(),[])

    def test_link_claim_blocks_scan_before_owner_bind_and_releases_after_commit(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind);links=[{**row,'enabled':bool(row['enabled']),'direction':'import'} for row in sync._stored_links()]
                original=owner.apple_connect_category
                def checked(*args):
                    self.assertEqual(self.competing_scan(sync),'blocked')
                    return original(*args)
                with patch.object(owner,'apple_connect_category',side_effect=checked):sync.replace_links(links)
                self.assertEqual(sync._stored_links()[0]['direction'],'import')
                self.assertEqual(sync.status()['scan_claims'],[])
                self.assertEqual(self.competing_scan(sync),'acquired')

    def test_unchanged_held_link_survives_an_unrelated_addition(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind);before=sync._stored_links()[0]
                bridge.sources.append({**bridge.sources[0],'id':'other','title':'Other'})
                links=[{**before,'enabled':bool(before['enabled'])},{'apple_id':'other','create_name':'Other','direction':'bidirectional','enabled':True}]
                with other_process_holds(sync):sync.replace_links(links)
                self.assertEqual(next(row for row in sync._stored_links() if row['apple_id']=='a'),before)
                self.assertEqual(len(sync._stored_links()),2)
                self.assertEqual({row['group_id'] for row in sync.status()['scan_claims']},{'a'})

    def test_unknown_native_delete_survives_restart_and_does_not_resend(self):
        from apple_sync.contracts import process_operations
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind);identity=sync._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id']
                (owner.delete_event if kind=='calendar' else owner.delete_todo)(identity)
                original=bridge.call;sent=[]
                def interrupted(op,**payload):
                    if op in ('delete','reminder_delete'):
                        sent.append(op);original(op,**payload);raise RuntimeError('lost native reply')
                    return original(op,**payload)
                with patch.object(bridge,'call',side_effect=interrupted):_,errors=process_operations(sync,owner,kind)
                self.assertTrue(errors);self.assertEqual(len(sent),1)
                restarted=(self.case.calendar if kind=='calendar' else self.case.reminder)(bridge)
                _,errors=process_operations(restarted,owner,kind)
                self.assertTrue(errors);self.assertEqual(len(sent),1)
                self.assertEqual({row['group_id'] for row in restarted.status()['scan_claims']},{'a'})
                self.assertTrue((owner.get_event if kind=='calendar' else owner.get_todo)(identity))
                self.assertEqual(self.competing_scan(sync),'blocked')

    def test_bound_delete_receipt_recovers_locally_once(self):
        from apple_sync.contracts import process_operations
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind);identity=sync._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id']
                (owner.delete_event if kind=='calendar' else owner.delete_todo)(identity)
                with patch.object(owner,'apple_finish_operation',side_effect=RuntimeError('owner reply lost')):
                    _,errors=process_operations(sync,owner,kind)
                self.assertTrue(errors)
                self.assertEqual(len(sync.status()['scan_claims']),1)
                before=len([op for op,_ in bridge.calls if op in ('delete','reminder_delete')])
                self.assertEqual(before,1)
                restarted=(self.case.calendar if kind=='calendar' else self.case.reminder)(bridge)
                _,errors=process_operations(restarted,owner,kind)
                self.assertEqual(errors,[])
                self.assertEqual(restarted.status()['scan_claims'],[])
                self.assertEqual(owner.apple_operations(),[])
                process_operations(restarted,owner,kind)
                self.assertEqual(len([op for op,_ in bridge.calls if op in ('delete','reminder_delete')]),1)

    def test_link_owner_unknown_keeps_link_snapshot_and_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind);before=sync._stored_links()
                links=[{**row,'enabled':bool(row['enabled']),'direction':'import'} for row in before]
                with patch.object(owner,'apple_connect_category',side_effect=RuntimeError('owner response lost')):
                    with self.assertRaises(RuntimeError):sync.replace_links(links)
                self.assertEqual(sync._stored_links(),before)
                self.assertEqual(len(sync.status()['scan_claims']),1)
                self.assertEqual(self.competing_scan(sync),'blocked')

    def test_receipt_recovery_cannot_dispatch_when_receipt_changes(self):
        from apple_sync.group_scan_claims import WorkClaim
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind)
                with self.assertRaises(ValueError):
                    with WorkClaim(sync,{'a':'account1'},[],{'test':'exact-receipt'},recovery=True) as claim:
                        claim.started()
                self.assertEqual(len(sync.status()['scan_claims']),1)
                self.assertEqual(bridge.rows[0]['title'],'Remote event' if kind=='calendar' else 'Remote task')



    def test_outbox_respects_other_group_item_claim(self):
        from apple_sync.contracts import process_operations
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind);identity=sync._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id']
                (owner.delete_event if kind=='calendar' else owner.delete_todo)(identity)
                with other_process_holds(sync,'other',identity):_,errors=process_operations(sync,owner,kind)
                self.assertTrue(errors)
                self.assertFalse(any(op in ('delete','reminder_delete') for op,_ in bridge.calls))
                self.assertEqual(len(bridge.rows),1)
                self.assertEqual({row['group_id'] for row in sync.status()['scan_claims']},{'other'})

    def test_changed_manager_links_are_not_overwritten_after_owner_call(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind);links=[{**row,'enabled':bool(row['enabled']),'direction':'import'} for row in sync._stored_links()]
                original=owner.apple_connect_category
                def changed(*args):
                    result=original(*args)
                    script="import sqlite3,sys;db=sqlite3.connect(sys.argv[1]);db.execute(\"UPDATE category_links SET name_conflict='new independent evidence' WHERE apple_id='a'\");db.commit()"
                    child=subprocess.run([sys.executable,'-B','-c',script,str(sync.db_path)],capture_output=True,text=True)
                    self.assertEqual(child.returncode,0,child.stderr)
                    return result
                with patch.object(owner,'apple_connect_category',side_effect=changed):
                    with self.assertRaisesRegex(ValueError,'links changed'):sync.replace_links(links)
                self.assertEqual(sync._stored_links()[0]['name_conflict'],'new independent evidence')
                self.assertEqual(sync._stored_links()[0]['direction'],'bidirectional')
                self.assertEqual(len(sync.status()['scan_claims']),1)



if __name__=='__main__':unittest.main(verbosity=2)
