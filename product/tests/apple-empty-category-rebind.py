"""An unidentifiable empty calendar must not stop healthy calendar sync."""
import copy
import runpy
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

M = runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))


class EmptyRebind(unittest.TestCase):
    def setUp(self):
        self.case = M['Contracts'](); self.case.setUp()
        self.owner = self.case.business.calendar
        self.bridge = M['NativeBridge']('calendar')
        self.bridge.sources.append({**self.bridge.sources[0], 'id': 'other', 'title': 'Healthy'})
        self.bridge.rows.append({**self.bridge.rows[0], 'calendar_id': 'other', 'event_id': 'other-event',
                                 'external_id': 'other-external', 'title': 'Healthy event'})
        self.sync = self.case.calendar(self.bridge)
        self.assertEqual(self.sync.start_sync()['result']['status'], 'success')
        mapping = next(row for row in self.sync._maps() if row['calendar_id'] == 'a')
        self.category = next(row for row in self.sync._stored_links() if row['apple_id'] == 'a')['alcor_id']
        self.owner.apple_remove_item(mapping['alcor_id'])
        with self.sync._connect() as db:
            db.execute('DELETE FROM mappings WHERE id=?', (mapping['id'],))
        self.bridge.rows = [row for row in self.bridge.rows if row['calendar_id'] != 'a']
        self.bridge.sources[0]['id'] = 'new-group'
        self.bridge.calls.clear()

    def tearDown(self):
        self.case.tearDown()

    def test_empty_category_stays_unbound_and_other_calendar_updates_twice(self):
        for title in ('Healthy update one', 'Healthy update two'):
            self.bridge.rows[0]['title'] = title
            result = self.sync.sync()
            self.assertEqual(result['status'], 'partial', result)
            self.assertEqual(self.owner.get_category(self.category)['apple']['apple_id'], 'a')
            self.assertEqual({row['apple_id'] for row in self.sync._stored_links()}, {'a', 'other'})
            self.assertTrue(any(row['title'] == title for row in self.owner.list_events()))
            self.assertEqual(len(self.owner.categories()), 2)
            conflicts = self.sync._get('category_conflicts')
            self.assertTrue(any(row['kind'] == 'unverified_empty_category' and row['apple_id'] == 'a' for row in conflicts))
        self.assertFalse(any(op in ('create_calendar', 'upsert', 'delete_calendar') for op, _ in self.bridge.calls))
        self.assertEqual(self.sync.status()['scan_claims'], [])

    def test_database_errors_are_not_misreported_as_empty_identity(self):
        with patch('apple_sync.calendar_rebind.rebind', side_effect=sqlite3.OperationalError('synthetic database unavailable')):
            result = self.sync.sync()
        self.assertEqual(result['status'], 'error')
        self.assertIn('synthetic database unavailable', self.sync._get('error'))
        self.assertEqual(self.owner.get_category(self.category)['apple']['apple_id'], 'a')


if __name__ == '__main__':
    unittest.main(verbosity=2)
