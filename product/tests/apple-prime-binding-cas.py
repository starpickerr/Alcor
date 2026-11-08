"""Specific legacy prime binding races, synthetic stores only."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))

class PrimeBindings(unittest.TestCase):
    def test_verified_binding_installed_after_read_is_not_overwritten(self):
        for kind in ('calendar','reminders'):
            for entity in ('category','item'):
                with self.subTest(kind=kind,entity=entity),M['case'](kind) as (s,o,b):
                    s.start_sync();identity=s._stored_links()[0]['alcor_id'] if entity=='category' else s._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id']
                    saved=copy.deepcopy((o.get_category if entity=='category' else o.get_event if kind=='calendar' else o.get_todo)(identity)['apple'])
                    with o._connect() as db:db.execute('DELETE FROM apple_bindings WHERE entity=? AND local_id=?',(entity,identity))
                    original=o.apple_prime_unbound;calls=[]
                    def raced(expected,document):
                        if expected['entity']==entity and expected['local_id']==identity:
                            calls.append(document);o.apple_bind(entity,identity,saved)
                        return original(expected,document)
                    with patch.object(o,'apple_prime_unbound',side_effect=raced):s.prime_bindings()
                    self.assertEqual(len(calls),1)
                    current=(o.get_category if entity=='category' else o.get_event if kind=='calendar' else o.get_todo)(identity)
                    self.assertEqual(current['apple'],saved)
    def test_recreated_or_edited_row_does_not_inherit_old_placeholder(self):
        for kind in ('calendar','reminders'):
            for entity in ('category','item'):
                for change in ('recreate','edit'):
                    with self.subTest(kind=kind,entity=entity,change=change),M['case'](kind) as (s,o,b):
                        s.start_sync();identity=s._stored_links()[0]['alcor_id'] if entity=='category' else s._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id']
                        if entity=='category' and change=='recreate':
                            for mapping in s._maps():o.apple_remove_item(mapping['alcor_id' if kind=='calendar' else 'todo_id'])
                        with o._connect() as db:db.execute('DELETE FROM apple_bindings WHERE entity=? AND local_id=?',(entity,identity))
                        expected=o.apple_capture_unbound(entity,identity)
                        table=o.APPLE_CATEGORIES if entity=='category' else o.APPLE_ITEMS
                        with o._connect() as db:
                            if change=='recreate':
                                row=dict(db.execute(f'SELECT * FROM {table} WHERE id=?',(identity,)).fetchone())
                                db.execute(f'DELETE FROM {table} WHERE id=?',(identity,))
                                db.execute(f'INSERT INTO {table} ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
                            else:db.execute(f"UPDATE {table} SET {'name' if entity=='category' else 'title'}='new local version' WHERE id=?",(identity,))
                        self.assertFalse(o.apple_prime_unbound(expected,{'apple_id':'old','can_modify':False,'awaiting_permissions':True}))
                        current=(o.get_category if entity=='category' else o.get_event if kind=='calendar' else o.get_todo)(identity)
                        self.assertFalse(current.get('apple'))
    def test_placeholder_cannot_grant_permissions_or_infer_source(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local=o.create_category({'name':'Local'});expected=o.apple_capture_unbound('category',local['id'])
                for bad in ({'can_modify':True},{'can_delete':True},{'can_rename':1},{'source_id':'guess'},{'planned':True}):
                    with self.assertRaisesRegex(ValueError,'cannot grant'):
                        o.apple_prime_unbound(expected,{'apple_id':'old','can_modify':False,'awaiting_permissions':True,**bad})
                self.assertFalse(o.get_category(local['id']).get('apple'))

    def test_normal_legacy_missing_binding_is_read_only_until_verified(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                s.start_sync();category=s._stored_links()[0]['alcor_id'];item=s._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id']
                with o._connect() as db:db.execute('DELETE FROM apple_bindings')
                b.calls.clear();s.prime_bindings()
                self.assertFalse(o.get_category(category)['apple']['can_modify'])
                self.assertFalse((o.get_event if kind=='calendar' else o.get_todo)(item)['apple']['can_modify'])
                self.assertFalse(b.calls)

if __name__=='__main__':unittest.main(verbosity=2)
