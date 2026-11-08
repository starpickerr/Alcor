"""A native-ID collision is evidence to retain, not permission to merge or import."""
import copy,runpy,unittest
from pathlib import Path
M=runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))

class IdentityCandidates(unittest.TestCase):
    def setUp(self):
        self.case=M['Contracts']();self.case.setUp();self.owner=self.case.business.tasks;self.bridge=M['NativeBridge']('reminders')
        self.bridge.rows[0]['external_id']='';self.bridge.rows[0]['alcor_id']=''
        self.bridge.sources.extend([{**self.bridge.sources[0],'id':'b','title':'Other','source_id':'account2'},
                                    {**self.bridge.sources[0],'id':'c','title':'Unrelated'}])
        self.sync=self.case.reminder(self.bridge);self.sync.start_sync();self.id=self.sync._maps()[0]['todo_id'];self.before=copy.deepcopy(self.owner.get_todo(self.id));self.bridge.calls.clear()
    def tearDown(self):self.case.tearDown()
    def assert_no_native_write(self):
        self.assertFalse(any(op in ('reminder_upsert','reminder_delete','reminder_complete') for op,_ in self.bridge.calls))
    def test_cross_source_empty_external_collision_retains_both_sides_without_duplicate(self):
        self.bridge.rows[0].update(list_id='b',source_id='account2')
        for _ in range(3):
            result=self.sync.sync();self.assertNotEqual(result['status'],'success')
            self.assertEqual(len(self.owner.list_todos()),1);self.assertEqual(len(self.sync._maps()),1)
            self.assertEqual(self.owner.get_todo(self.id)['category_id'],self.before['category_id'])
            self.assertEqual(self.sync._maps()[0]['list_id'],'a')
            candidates=self.sync.status()['identity_candidates'];self.assertEqual(len(candidates),1)
            self.assertEqual(candidates[0]['native']['list_id'],'b');self.assertEqual(candidates[0]['native']['source_id'],'account2')
            self.assertEqual(candidates[0]['mapping_ids'],[self.id]);self.assertEqual(candidates[0]['state'],'pending')
        self.assertEqual(self.sync._maps()[0]['state'],'conflict');self.assert_no_native_write()
    def test_same_id_in_two_sources_does_not_freeze_unrelated_import(self):
        self.bridge.rows.append({**self.bridge.rows[0],'list_id':'b','source_id':'account2','title':'Collision candidate'})
        self.bridge.rows.append({**self.bridge.rows[0],'list_id':'c','reminder_id':'genuinely-new','title':'Unrelated new item'})
        for _ in range(3):
            self.sync.sync();self.assertEqual(len(self.owner.list_todos()),2);self.assertEqual(len(self.sync._maps()),2)
            self.assertEqual(len(self.sync.status()['identity_candidates']),1)
        self.assertEqual(self.owner.get_todo(self.id)['title'],self.before['title'])
        self.assertEqual({r['reminder_id'] for r in self.sync._maps()},{self.bridge.rows[0]['reminder_id'],'genuinely-new'});self.assert_no_native_write()
    def test_genuinely_new_empty_external_id_imports_normally(self):
        self.bridge.rows.append({**self.bridge.rows[0],'list_id':'b','source_id':'account2','reminder_id':'new-native-id','title':'New native item'})
        for _ in range(2):self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(len(self.owner.list_todos()),2);self.assertEqual(len(self.sync._maps()),2)
        self.assertEqual(self.sync.status().get('identity_candidates',[]),[]);self.assert_no_native_write()
    def test_trusted_same_source_empty_external_move_still_imports(self):
        self.bridge.rows[0].update(list_id='c')
        for _ in range(2):self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(len(self.owner.list_todos()),1);self.assertEqual(self.sync._maps()[0]['list_id'],'c')
        self.assertEqual(self.sync.status().get('identity_candidates',[]),[]);self.assert_no_native_write()

if __name__=='__main__':unittest.main(verbosity=2)
