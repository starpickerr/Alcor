"""Combined receipt stage permissions use only synthetic SQLite manager claims."""
import sqlite3,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,'packages/alcor-apple-sync/python')
from apple_sync.group_scan_claims import WorkClaim,ScanHeld

class Sync:
 def __init__(self,path):
  self.path=path
  with self._connect() as db:db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT)')
 def _connect(self):
  db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;return db
 def clock(self):return '2026-11-08T00:00:00Z'

class CombinedClaimStages(unittest.TestCase):
 def test_receipt_recovery_stays_scoped_to_operation_kind_and_stage(self):
  stages=('owner_claiming','owner_committing','owner_committed','receipt_recovery')
  for kind in ('manual_item_restore','approved_import_unlink','bound_item_conflict'):
   for stage in stages:
    with self.subTest(kind=kind,stage=stage),tempfile.TemporaryDirectory(prefix='combined-claim-') as tmp:
     sync=Sync(str(Path(tmp)/'manager.sqlite3'));evidence={'kind':kind,'id':'synthetic'};groups={'group':'source'}
     old=WorkClaim(sync,groups,['local'],evidence).__enter__();old.started(stage)
     before=old.document.copy()
     # Even a matching plan and stage cannot take a scanning claim without an owner receipt.
     with self.assertRaises(ScanHeld):WorkClaim(sync,groups,['local'],evidence,recovery=True).__enter__()
     # A different operation's receipt/evidence cannot take this claim either.
     with self.assertRaises(ScanHeld):WorkClaim(sync,groups,['local'],{'kind':kind,'id':'other'},recovery=True,completed_owner_receipt=True).__enter__()
     allowed = stage != 'owner_committing' or kind == 'bound_item_conflict'
     candidate=WorkClaim(sync,groups,['local'],evidence,recovery=True,completed_owner_receipt=True)
     if not allowed:
      with self.assertRaises(ScanHeld):candidate.__enter__()
      with sync._connect() as db:old._check(db)
      self.assertEqual(old.document,before)
      continue
     claim=candidate.__enter__()
     with self.assertRaises(ValueError):
      with sync._connect() as db:old._check(db)
     with self.assertRaisesRegex(ValueError,'cannot dispatch'):claim.started()
     claim.__exit__(None,None,None)

if __name__=='__main__':unittest.main(verbosity=2)
