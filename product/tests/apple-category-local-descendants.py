"""A native container's absence does not authorize deleting local-only descendants."""
import runpy,unittest
from pathlib import Path
M=runpy.run_path(str(Path(__file__).with_name('apple-category-absence-sync.py')))

class LocalDescendants(unittest.TestCase):
    def test_local_only_cross_category_descendant_stays_active(self):
        base=M['CategoryAbsence']()
        with base.case('reminders') as (sync,owner,bridge,clock,link):
            parent=sync._maps()[0]['todo_id'];category=owner.create_category({'name':'Local only recovered'})
            # Existing restore marker; no external source or native identity is assigned.
            with owner._connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS apple_inbound_local_only (entity TEXT NOT NULL, local_id TEXT NOT NULL, PRIMARY KEY(entity,local_id))')
                db.execute("INSERT INTO apple_inbound_local_only VALUES ('category',?)",(category['id'],))
            child=owner.create_todo({'title':'Local only child','category_id':category['id'],'parent_id':parent})
            self.assertFalse(child.get('apple'));self.assertEqual(child['category_id'],category['id'])
            bridge.sources=[];bridge.rows=[];sync.sync();clock[0]='2026-11-07T00:02:00Z'
            result=sync.sync()
            self.assertEqual(result.get('deleted_categories'),0,result)
            self.assertTrue(owner.get_category(link['alcor_id']));self.assertTrue(owner.get_category(category['id']))
            self.assertEqual(owner.get_todo(child['id'])['title'],'Local only child')
            self.assertEqual(owner.list_apple_inbound_recoveries(),[])

if __name__=='__main__':unittest.main(verbosity=2)
