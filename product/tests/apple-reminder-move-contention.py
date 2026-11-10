"""Move contention is temporary waiting; identity conflicts are recorded under ownership."""
import contextlib,os,runpy,subprocess,sys,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-reminder-move-cas.py')))
from apple_sync.reminder_moves import move
from apple_sync.group_scan_claims import ScanHeld
ROOT=Path(__file__).resolve().parents[2]

@contextlib.contextmanager
def competing_work(sync):
    script='''import sqlite3,sys
from apple_sync.group_scan_claims import WorkClaim
class S:
 def _connect(self):
  db=sqlite3.connect(sys.argv[1]);db.row_factory=sqlite3.Row;return db
 def clock(self):return '2026-11-08T00:00:00Z'
with WorkClaim(S(),{'b':'account1'},[],{'kind':'synthetic_competing_work'}):
 print('ready',flush=True);sys.stdin.readline()
'''
    child=subprocess.Popen([sys.executable,'-B','-c',script,str(sync.db_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env={**os.environ,'PYTHONPATH':str(ROOT/'packages/alcor-apple-sync/python')})
    try:
        assert child.stdout.readline().strip()=='ready'
        yield
    finally:
        out,err=child.communicate('\n',timeout=10)
        assert child.returncode==0,(out,err)

class Contention(unittest.TestCase):
    def setUp(self):
        self.base=M['Moves']();self.base.setUp()
        for key in ('sync','owner','bridge','id','target'):setattr(self,key,getattr(self.base,key))
        self.mapping=self.sync._maps()[0];links=self.sync._stored_links()
        self.original=next(row for row in links if row['apple_id']=='a');self.destination=next(row for row in links if row['apple_id']=='b')
    def tearDown(self):self.base.tearDown()
    def test_other_process_claim_does_not_write_conflict_and_move_can_retry(self):
        with competing_work(self.sync):
            with self.assertRaises(ScanHeld):move(self.sync,self.owner,dict(self.mapping),self.original,self.destination)
            self.assertEqual(self.sync._maps()[0],self.mapping)
        self.assertEqual(move(self.sync,self.owner,dict(self.mapping),self.original,self.destination),1)
        self.assertEqual(self.sync._maps()[0]['list_id'],'b')
    def test_identity_conflict_is_written_before_claim_release(self):
        original_call=self.bridge.call;original_conflict=self.sync._reminder_conflict;seen=[]
        other=M['M']['CommonClaims']()
        def gone(op,**payload):
            if op=='reminder_lookup':return {'reminder':None}
            return original_call(op,**payload)
        def record(*args,**kwargs):
            seen.append(True)
            self.assertEqual(other.competing_scan(self.sync,'a'),'blocked')
            self.assertEqual(other.competing_scan(self.sync,'b'),'blocked')
            return original_conflict(*args,**kwargs)
        with patch.object(self.bridge,'call',side_effect=gone),patch.object(self.sync,'_reminder_conflict',side_effect=record):
            with self.assertRaises(ValueError):move(self.sync,self.owner,dict(self.mapping),self.original,self.destination)
        self.assertTrue(seen);self.assertEqual(self.sync._maps()[0]['state'],'conflict')
        self.assertEqual(self.sync.status()['scan_claims'],[])
    def test_sync_defers_user_move_while_destination_is_held(self):
        with competing_work(self.sync):result=self.sync.sync()
        self.assertEqual(self.sync._maps()[0]['list_id'],'a')
        self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.target)
        self.assertNotEqual(result['status'],'success')
        self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(self.sync._maps()[0]['list_id'],'b')

    def test_changed_claim_cannot_write_identity_conflict(self):
        original=self.bridge.call
        def changed(op,**payload):
            if op=='reminder_lookup':
                with self.sync._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                return {'reminder':None}
            return original(op,**payload)
        with patch.object(self.bridge,'call',side_effect=changed):
            with self.assertRaises(ValueError):move(self.sync,self.owner,dict(self.mapping),self.original,self.destination)
        self.assertEqual(self.sync._maps()[0],self.mapping)
        self.assertEqual({row['group_id'] for row in self.sync.status()['scan_claims']},{'a','b'})

if __name__=='__main__':unittest.main(verbosity=2)
