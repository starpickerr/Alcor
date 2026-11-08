"""Conditional owner category updates retain concurrent fields and member changes."""
import copy,runpy,unittest
from pathlib import Path
M=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))

class CategoryOwner(unittest.TestCase):
    def setUp(self):self.case=M['CommonClaims']();self.case.setUp()
    def tearDown(self):self.case.tearDown()
    def test_normal_name_color_binding_commit_together(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.case.setup_domain(kind);identity=sync._stored_links()[0]['alcor_id'];expected=owner.apple_capture_category_absence(identity)
                binding={**expected['local']['apple'],'title':'Accepted native'}
                result=owner.apple_apply_category_if_unchanged(expected,{'name':'Accepted native','color':'#123456'},binding)
                self.assertEqual(result['name'],'Accepted native');self.assertEqual(result['color'],'#123456');self.assertEqual(result['apple']['title'],'Accepted native')
                if kind=='reminders':self.assertEqual(owner.list_todos()[0]['list_name'],'Accepted native')
    def test_local_rename_after_capture_rejects_without_binding_write(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.case.setup_domain(kind);identity=sync._stored_links()[0]['alcor_id'];expected=owner.apple_capture_category_absence(identity)
                owner.update_category(identity,{'name':'New local choice'});before=copy.deepcopy(owner.get_category(identity))
                with self.assertRaisesRegex(ValueError,'CATEGORY_CHANGED:'):owner.apple_apply_category_if_unchanged(expected,{'name':'Stale native'},expected['local']['apple'])
                self.assertEqual(owner.get_category(identity),before)
    def test_new_member_after_capture_rejects_without_rename(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.case.setup_domain(kind);identity=sync._stored_links()[0]['alcor_id'];expected=owner.apple_capture_category_absence(identity)
                if kind=='reminders':owner.create_todo({'title':'Later member','category_id':identity})
                else:owner.create_event({'title':'Later member','start_at':'2026-11-08T01:00:00Z','end_at':'2026-11-08T02:00:00Z','category_id':identity})
                with self.assertRaisesRegex(ValueError,'CATEGORY_CHANGED:'):owner.apple_apply_category_if_unchanged(expected,{'name':'Stale native'})
                self.assertEqual(owner.get_category(identity)['name'],expected['local']['name'])
    def test_source_change_is_not_a_rebind_permission(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.case.setup_domain(kind);identity=sync._stored_links()[0]['alcor_id'];expected=owner.apple_capture_category_absence(identity)
                with self.assertRaisesRegex(ValueError,'CATEGORY_CHANGED:'):owner.apple_apply_category_if_unchanged(expected,{},dict(expected['local']['apple'],source_id='different'))
                self.assertEqual(owner.get_category(identity),expected['local'])

if __name__=='__main__':unittest.main(verbosity=2)
