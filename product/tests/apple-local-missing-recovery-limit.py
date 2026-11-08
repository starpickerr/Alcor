"""Raw conflict writes stay denied; the exact private local restore is supported."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))
W=runpy.run_path(str(Path(__file__).resolve().parents[2]/'packages/alcor-apple-sync/python/worker.py'))['AppleWorker']

class LocalMissingLimit(unittest.TestCase):
    def test_public_guard_preserves_data_and_private_exact_restore_stays_local(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                s.start_sync();key='alcor_id' if kind=='calendar' else 'todo_id';identity=s._maps()[0][key]
                o.apple_remove_item(identity);b.rows[0]['title']='Native retained';s.sync()
                worker=object.__new__(W);worker.verification_no_apple=False;worker.managers={kind:s}
                before=copy.deepcopy(s._maps());native=copy.deepcopy(b.rows);b.calls.clear()
                with o._connect() as db:owner_before=list(db.iterdump())
                with s._connect() as db:manager_before=list(db.iterdump())
                with patch.object(o,'apple_restore_item',side_effect=AssertionError('owner write invoked')):
                    with self.assertRaisesRegex(PermissionError,'单次审批'):
                        worker.invoke('resolve_conflict',{'kind':kind,key:identity,'choice':'keep_apple'})
                with o._connect() as db:self.assertEqual(list(db.iterdump()),owner_before)
                with s._connect() as db:self.assertEqual(list(db.iterdump()),manager_before)
                self.assertEqual(s._maps(),before);self.assertEqual(b.rows,native);self.assertIsNone(s._local(identity))
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))
                s.resolve_conflict(identity,'keep_apple');s.sync();s.sync()
                self.assertEqual(s._local(identity)['title'],'Native retained');self.assertEqual(len(s._maps()),1);self.assertEqual(b.rows,native)
                self.assertFalse(any(op in ('upsert','reminder_upsert','delete','reminder_delete') for op,_ in b.calls))
    def test_private_existing_local_choices_and_normal_inbound_remain_supported(self):
        for kind in ('calendar','reminders'):
            for choice in ('keep_apple','keep_alcor'):
                with self.subTest(kind=kind,choice=choice),M['case'](kind) as (s,o,b):
                    s.start_sync();key='alcor_id' if kind=='calendar' else 'todo_id';mapping=s._maps()[0];identity=mapping[key]
                    b.rows[0]['title']='Normal inbound';s.sync();self.assertEqual(s._local(identity)['title'],'Normal inbound')
                    (o.update_event if kind=='calendar' else o.update_todo)(identity,{'title':'Local choice'})
                    b.rows[0]['title']='Native choice';s._conflict(s._maps()[0],'both changed')
                    s.resolve_conflict(identity,choice)
                    self.assertEqual(s._local(identity)['title'],'Native choice' if choice=='keep_apple' else 'Local choice')
                    self.assertEqual(len(s._maps()),1);self.assertEqual(s.status()['scan_claims'],[])

if __name__=='__main__':unittest.main(verbosity=2)
