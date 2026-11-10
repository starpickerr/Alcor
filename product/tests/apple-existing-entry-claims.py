"""Bounded public entry regressions; all databases/native contents are synthetic."""
import copy,runpy,unittest
from pathlib import Path
M=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))

class ExistingEntries(unittest.TestCase):
    def setUp(self):self.base=M['CommonClaims']();self.base.setUp()
    def tearDown(self):self.base.tearDown()
    def test_first_native_import_waits_for_group_claim(self):
        sync,bridge,owner=self.base.setup_domain('reminders')
        bridge.rows.append({**bridge.rows[0],'reminder_id':'new-unmapped','external_id':'','alcor_id':''})
        before=copy.deepcopy(owner.list_todos())
        with M['other_process_holds'](sync):sync.sync()
        self.assertEqual(owner.list_todos(),before);self.assertEqual(len(sync._maps()),1)
    def test_marker_binding_waits_for_other_group_item_claim(self):
        sync,bridge,owner=self.base.setup_domain('reminders');category=sync._stored_links()[0]['alcor_id']
        local=owner.create_todo({'title':'Marker local','category_id':category});before=copy.deepcopy(owner.get_todo(local['id']))
        bridge.rows.append({**bridge.rows[0],'title':'Marker local','reminder_id':'marker-id','external_id':'','alcor_id':local['id']})
        with M['other_process_holds'](sync,'unrelated',local['id']):sync.sync()
        self.assertEqual(owner.get_todo(local['id']),before)
        self.assertFalse(any(row['todo_id']==local['id'] for row in sync._maps()))
    def test_manual_item_resolution_waits_for_group_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);mapping=sync._maps()[0];identity=mapping['alcor_id' if kind=='calendar' else 'todo_id']
                sync._conflict(mapping,'synthetic both sides changed');bridge.rows[0]['title']='Native chosen value'
                before=copy.deepcopy(sync._local(identity));beforemap=copy.deepcopy(sync._maps())
                with M['other_process_holds'](sync):
                    try:sync.resolve_conflict(identity,'keep_apple')
                    except ValueError:pass
                self.assertEqual(sync._local(identity),before);self.assertEqual(sync._maps(),beforemap)
    def test_manual_category_resolution_waits_for_group_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);link=sync._stored_links()[0]
                bridge.sources[0]['title']='Native category rename';before=copy.deepcopy(owner.get_category(link['alcor_id']))
                with M['other_process_holds'](sync):
                    try:sync.resolve_category_conflict('a','keep_apple',field='name')
                    except ValueError:pass
                self.assertEqual(owner.get_category(link['alcor_id']),before)
    def test_automatic_category_rename_waits_for_group_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);link=sync._stored_links()[0]
                bridge.sources[0]['title']='Native rename while claimed';before=copy.deepcopy(owner.get_category(link['alcor_id']))
                with M['other_process_holds'](sync):sync.sync()
                self.assertEqual(owner.get_category(link['alcor_id']),before)

if __name__=='__main__':unittest.main(verbosity=2)
