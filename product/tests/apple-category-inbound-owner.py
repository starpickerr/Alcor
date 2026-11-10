"""Real owners with temporary synthetic databases; no Apple, Profile or scheduler calls."""
import json
import runpy
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

M = runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))


class CategoryAbsence(unittest.TestCase):
    def setUp(self):
        self.case = M['Contracts']('test_identity_reset_rebinds_without_changing_local_ids_or_losing_notes')
        self.case.setUp()
        self.business = self.case.business
        self.business.tasks._sync_scheduler_links = lambda: None

    def tearDown(self):
        self.case.tearDown()

    def owners(self):
        for name in ('calendar', 'tasks'):
            svc = getattr(self.business, name)
            category = svc.create_category({'name': 'synthetic absence', 'color': '#112233'})
            svc.apple_bind('category', category['id'], {'apple_id': 'synthetic-group', 'source_id': 'synthetic-account',
                                                      'title': category['name'], 'can_modify': True, 'can_delete': True, 'can_rename': True})
            yield svc, category['id']

    def item(self, svc, category, **extra):
        payload = {'title': 'synthetic member', 'category_id': category, **extra}
        if svc.APPLE_ITEMS == 'calendar_events':
            return svc.create_event({**payload, 'start_at': '2026-11-07T01:00:00Z', 'end_at': '2026-11-07T02:00:00Z'})
        return svc.create_todo(payload)

    def assert_kept(self, svc, category, expected):
        with self.assertRaises((ValueError, KeyError)):
            svc.apple_remove_category_if_unchanged(expected)
        self.assertEqual(svc.get_category(category)['id'], category)
        self.assertEqual(svc.list_apple_inbound_recoveries(), [])

    def test_unchanged_empty_and_populated_categories_delete_and_restore(self):
        for svc, category in self.owners():
            item = self.item(svc, category)
            svc.apple_record_completion(item['id'], {'reminder_id': 'old', 'due_on': '2026-11-06'}, {'completed': False}) if svc.APPLE_ITEMS == 'todos' else None
            expected = svc.apple_capture_category_absence(category)
            result = svc.apple_remove_category_if_unchanged(json.loads(json.dumps(expected)))
            with self.assertRaises(KeyError):
                svc.get_category(category)
            with svc._connect() as db:
                document = json.loads(db.execute('SELECT document FROM apple_inbound_recovery WHERE id=?', (result['recovery_id'],)).fetchone()[0])
            self.assertEqual(document['tables'][svc.APPLE_ITEMS][0]['id'], item['id'])
            restored = svc.restore_apple_inbound_recovery(result['recovery_id'])
            self.assertEqual(len(restored['ids']), 1)
            self.assertEqual(restored['sync'], 'local_only')
            empty = svc.create_category({'name': 'synthetic empty'})['id']
            result = svc.apple_remove_category_if_unchanged(svc.apple_capture_category_absence(empty))
            self.assertEqual(len(svc.restore_apple_inbound_recovery(result['recovery_id'])['categoryIds']), 1)

    def test_rename_and_recolor_after_first_absence(self):
        for field, value in (('name', 'synthetic renamed'), ('color', '#445566')):
            for svc, category in self.owners():
                expected = svc.apple_capture_category_absence(category)
                svc.update_category(category, {field: value})
                self.assert_kept(svc, category, expected)
                # Remove fixtures locally so this test can repeat both owners.
                with svc._connect() as db:
                    db.execute(f'DELETE FROM {svc.APPLE_CATEGORIES} WHERE id=?', (category,))

    def test_new_member_before_owner_call(self):
        for svc, category in self.owners():
            expected = svc.apple_capture_category_absence(category)
            item = self.item(svc, category)
            self.assert_kept(svc, category, expected)
            with svc._connect() as db:
                self.assertIsNotNone(db.execute(f'SELECT 1 FROM {svc.APPLE_ITEMS} WHERE id=?', (item['id'],)).fetchone())

    def test_member_moved_in_before_owner_call(self):
        for svc, category in self.owners():
            other = svc.create_category({'name': 'synthetic other'})['id']
            item = self.item(svc, other)
            expected = svc.apple_capture_category_absence(category)
            svc.apple_place_item(item['id'], category)
            self.assert_kept(svc, category, expected)

    def test_member_moved_out_before_owner_call(self):
        for svc, category in self.owners():
            other = svc.create_category({'name': 'synthetic other'})['id']
            item = self.item(svc, category)
            expected = svc.apple_capture_category_absence(category)
            svc.apple_place_item(item['id'], other)
            self.assert_kept(svc, category, expected)

    def test_same_id_identical_row_recreation_is_not_same_generation(self):
        for entity in ('category', 'item'):
            for svc, category in self.owners():
                item = self.item(svc, category) if entity == 'item' else None
                expected = svc.apple_capture_category_absence(category)
                table, identity = (svc.APPLE_CATEGORIES, category) if entity == 'category' else (svc.APPLE_ITEMS, item['id'])
                with svc._connect() as db:
                    row = dict(db.execute(f'SELECT * FROM {table} WHERE id=?', (identity,)).fetchone())
                    db.execute(f'DELETE FROM {table} WHERE id=?', (identity,))
                    db.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
                self.assert_kept(svc, category, expected)
                with svc._connect() as db:
                    db.execute(f'DELETE FROM {svc.APPLE_ITEMS}')
                    db.execute(f'DELETE FROM {svc.APPLE_CATEGORIES} WHERE id=?', (category,))

    def test_member_metadata_and_completion_history_changes(self):
        for svc, category in self.owners():
            item = self.item(svc, category)
            expected = svc.apple_capture_category_absence(category)
            with svc._connect() as db:
                db.execute('INSERT INTO apple_completion_history VALUES (?,?,?,?)', (item['id'], 'synthetic', '2026-11-06', '{}'))
            self.assert_kept(svc, category, expected)
            expected = svc.apple_capture_category_absence(category)
            svc.apple_apply_metadata(item['id'], {'url': 'https://example.com/synthetic'})
            self.assert_kept(svc, category, expected)

    def test_source_tombstone_change(self):
        for svc, category in self.owners():
            item = self.item(svc, category)
            with svc._connect() as db:
                db.execute(f'UPDATE {svc.APPLE_ITEMS} SET source_key=? WHERE id=?', ('synthetic-source', item['id']))
            expected = svc.apple_capture_category_absence(category)
            with svc._connect() as db:
                db.execute('INSERT INTO source_tombstones VALUES (?,?)', ('synthetic-source', '2026-11-07'))
            self.assert_kept(svc, category, expected)

    def test_recursive_child_and_dependency_changes(self):
        svc = self.business.tasks
        category = svc.create_category({'name': 'synthetic parent category'})['id']
        other = svc.create_category({'name': 'synthetic child category'})['id']
        parent = self.item(svc, category)
        child = self.item(svc, other, parent_id=parent['id'])
        expected = svc.apple_capture_category_absence(category)
        svc.update_todo(child['id'], {'title': 'synthetic child edited'})
        self.assert_kept(svc, category, expected)
        outside = self.item(svc, other)
        expected = svc.apple_capture_category_absence(category)
        with svc._connect() as db:
            db.execute('INSERT INTO todo_dependencies(task_id,prerequisite_id) VALUES (?,?)', (outside['id'], child['id']))
        self.assert_kept(svc, category, expected)
        expected = svc.apple_capture_category_absence(category)
        result = svc.apple_remove_category_if_unchanged(expected)
        with svc._connect() as db:
            document = json.loads(db.execute('SELECT document FROM apple_inbound_recovery WHERE id=?', (result['recovery_id'],)).fetchone()[0])
        self.assertEqual({row['id'] for row in document['tables']['todos']}, {parent['id'], child['id']})
        self.assertEqual(document['tables']['todo_dependencies'][0]['task_id'], outside['id'])
        self.assertEqual(len(svc.restore_apple_inbound_recovery(result['recovery_id'])['ids']), 2)

    def test_archive_failure_rolls_back_all_owner_changes(self):
        import business.apple_contract as contract
        original = contract.capture_inbound_delete
        for svc, category in self.owners():
            self.item(svc, category)
            expected = svc.apple_capture_category_absence(category)
            def fail(*args):
                original(*args)
                raise sqlite3.OperationalError('synthetic archive failure')
            with patch.object(contract, 'capture_inbound_delete', fail):
                with self.assertRaises(sqlite3.OperationalError):
                    svc.apple_remove_category_if_unchanged(expected)
            self.assertEqual(svc.apple_capture_category_absence(category), expected)
            self.assertEqual(svc.list_apple_inbound_recoveries(), [])
            with patch.object(contract, 'capture_inbound_delete', return_value=None):
                self.assert_kept(svc, category, expected)

    def test_delete_failure_rolls_back_archive_and_rows(self):
        for svc, category in self.owners():
            self.item(svc, category)
            expected = svc.apple_capture_category_absence(category)
            method = '_delete_event' if svc.APPLE_ITEMS == 'calendar_events' else '_delete_todo_tree'
            with patch.object(svc, method, side_effect=sqlite3.OperationalError('synthetic delete failure')):
                with self.assertRaises(sqlite3.OperationalError):
                    svc.apple_remove_category_if_unchanged(expected)
            self.assertEqual(svc.apple_capture_category_absence(category), expected)
            self.assertEqual(svc.list_apple_inbound_recoveries(), [])

    def test_snapshot_recheck_holds_sqlite_write_lock(self):
        for svc, category in self.owners():
            expected = svc.apple_capture_category_absence(category)
            original = svc._apple_category_absence_snapshot
            ready, release = threading.Event(), threading.Event()
            def pause(db, identity):
                result = original(db, identity)
                ready.set()
                self.assertTrue(release.wait(5))
                return result
            with patch.object(svc, '_apple_category_absence_snapshot', pause):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(svc.apple_remove_category_if_unchanged, expected)
                    self.assertTrue(ready.wait(5))
                    try:
                        with svc._connect() as db:
                            db.execute('PRAGMA busy_timeout=1')
                            with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                                db.execute(f'UPDATE {svc.APPLE_CATEGORIES} SET name=? WHERE id=?', ('synthetic concurrent rename', category))
                    finally:
                        release.set()
                    self.assertTrue(pending.result(timeout=5)['deleted'])

    def test_missing_or_legacy_snapshot_rejected_and_api_not_public(self):
        for svc, category in self.owners():
            for expected in (None, {}, {'local_id': category}, {'version': 1, 'local_id': category, 'local': {}, 'snapshot': '0' * 64}):
                self.assert_kept(svc, category, expected)
            domain = 'calendar' if svc.APPLE_ITEMS == 'calendar_events' else 'tasks'
            for method in ('apple_capture_category_absence', 'apple_remove_category_if_unchanged'):
                with self.assertRaises(ValueError):
                    self.business.invoke(domain, method, {'identity': category})


if __name__ == '__main__':
    unittest.main(verbosity=2)
