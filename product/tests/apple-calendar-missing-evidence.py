"""Bounded native lookup is not deletion proof; synthetic stores only."""
import copy
import runpy
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = runpy.run_path(str(ROOT / 'product/tests/apple-calendar-rebind-safe.py'))


class MissingEvidence(unittest.TestCase):
    def setUp(self):
        self.fixture = FIXTURE['Rebind']('test_unique_external_id_and_occurrence_rebinds_without_changing_local')
        self.fixture.setUp()
        self.sync, self.owner, self.bridge, self.id = (
            self.fixture.sync, self.fixture.owner, self.fixture.bridge, self.fixture.id)
        self.sync.clock = lambda: '2026-11-07T00:00:00+00:00'
        self.assertEqual(self.sync.sync()['status'], 'success')
        self.sync._set('sync_deletions', True)
        self.local = self.owner.get_event(self.id)
        self.mapping = self.sync._maps()[0]
        self.baseline = self.sync._get('item-metadata:' + self.id)
        self.archives = self.owner.list_apple_inbound_recoveries()
        with self.sync._connect() as db:
            db.execute("UPDATE mappings SET missing_since='2026-11-06T00:00:00+00:00' WHERE alcor_id=?", (self.id,))

    def tearDown(self):
        self.fixture.tearDown()

    def null_lookup(self, before=None):
        original = self.bridge.call
        def call(operation, **arguments):
            if operation == 'lookup':
                if before:
                    before()
                return {'event': None}
            return original(operation, **arguments)
        self.bridge.call = call

    def assert_preserved(self, result, *, title=None):
        self.assertEqual(result['deleted_local'], 0, result)
        self.assertEqual(result['status'], 'partial', result)
        self.assertEqual(self.owner.get_event(self.id)['title'], title or self.local['title'])
        self.assertEqual(next(row for row in self.sync._maps() if row['alcor_id'] == self.id)['state'], 'conflict')
        self.assertEqual(self.sync._get('item-metadata:' + self.id), self.baseline)
        self.assertEqual(self.owner.list_apple_inbound_recoveries(), self.archives)
        self.assertTrue(self.sync._get('auto_enabled', True))
        self.assertTrue(self.sync._get('sync_deletions'))

    def test_changed_native_id_moved_outside_window_keeps_local_record(self):
        self.bridge.rows[0].update(event_id='new-id-outside-window', start_at='2032-11-05T01:00:00Z',
                                   end_at='2032-11-05T02:00:00Z', occurrence_date='2032-11-05T01:00:00Z')
        self.null_lookup()
        self.assert_preserved(self.sync.sync())
        self.assertEqual(len(self.bridge.rows), 1)
        evidence = self.sync._get('calendar-inbound-conflict:' + self.id)
        self.assertEqual(evidence['remote']['absence_evidence'], 'bounded_lookup_null')
        self.assertEqual(evidence['remote']['event_id'], self.mapping['event_id'])
        self.assertIn('scan_start', evidence['remote'])
        self.assertEqual(evidence['observed']['local']['id'], self.id)

    def test_positive_lookup_still_updates_the_verified_local_item(self):
        self.bridge.rows[0]['title'] = 'verified remote update'
        original = self.bridge.call
        def omitted_from_window(operation, **arguments):
            result = original(operation, **arguments)
            if operation == 'events':
                result['events'] = []
            return result
        self.bridge.call = omitted_from_window
        result = self.sync.sync()
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['deleted_local'], 0)
        self.assertEqual(self.owner.get_event(self.id)['title'], 'verified remote update')
        self.assertEqual(self.sync._maps()[0]['state'], 'active')
        self.assertEqual(self.owner.list_apple_inbound_recoveries(), self.archives)

    def test_elapsed_time_and_manual_confirmation_do_not_turn_null_into_proof(self):
        self.bridge.rows = []
        self.null_lookup()
        self.assert_preserved(self.sync.sync(manual=True, confirm_local_deletions=True))
        self.sync.clock = lambda: '2026-11-14T00:00:00+00:00'
        repeated = self.sync.sync()
        self.assertEqual(repeated['deleted_local'], 0)
        self.assertEqual(repeated['status'], 'partial')
        self.assertEqual(self.owner.get_event(self.id)['title'], self.local['title'])

    def test_local_edit_during_null_lookup_is_not_archived(self):
        self.bridge.rows = []
        self.null_lookup(lambda: self.owner.update_event(self.id, {'description': 'later local note'}))
        self.assert_preserved(self.sync.sync())
        self.assertEqual(self.owner.get_event(self.id)['description'], 'later local note')

    def test_absent_local_id_recreated_during_lookup_is_not_marked_deleted(self):
        self.bridge.rows = []
        self.owner.apple_remove_item(self.id)
        self.archives = self.owner.list_apple_inbound_recoveries()
        self.null_lookup(lambda: self.owner.create_event({'id': self.id, 'title': 'new incarnation',
                         'start_at': '2026-11-05T01:00:00Z', 'end_at': '2026-11-05T02:00:00Z'}))
        self.assert_preserved(self.sync.sync(), title='new incarnation')

    def test_generic_conflict_choices_cannot_delete_or_duplicate_an_unlocated_item(self):
        self.bridge.rows = []
        self.null_lookup()
        self.assert_preserved(self.sync.sync())
        before_calls = len(self.bridge.calls)
        for choice in ('keep_apple', 'keep_alcor'):
            with self.subTest(choice=choice):
                with self.assertRaisesRegex(ValueError, '无法确认'):
                    self.sync.resolve_conflict(self.id, choice)
                self.assertEqual(self.owner.get_event(self.id)['title'], self.local['title'])
                self.assertEqual(self.owner.list_apple_inbound_recoveries(), self.archives)
        self.assertFalse(any(op in ('upsert', 'delete') for op, _ in self.bridge.calls[before_calls:]))
        self.assertEqual(self.sync._maps()[0]['state'], 'conflict')

    def test_changed_manager_mapping_during_lookup_is_not_overwritten(self):
        self.bridge.rows = []
        def change():
            with self.sync._connect() as db:
                db.execute("UPDATE mappings SET conflict='newer manager evidence' WHERE alcor_id=?", (self.id,))
        self.null_lookup(change)
        result = self.sync.sync()
        self.assertEqual(result['status'], 'error', result)
        self.assertEqual(result['deleted_local'], 0)
        self.assertEqual(self.sync._maps()[0]['conflict'], 'newer manager evidence')
        self.assertEqual(self.owner.get_event(self.id)['title'], self.local['title'])
        self.assertTrue(self.sync.status()['scan_claims'])

    def test_other_selected_calendar_continues_while_one_record_needs_review(self):
        self.bridge.rows = []
        self.bridge.sources.append({**self.bridge.sources[0], 'id': 'other-group', 'title': 'Other'})
        other = copy.deepcopy(self.local['apple']['native'])
        other.update(calendar_id='other-group', event_id='other-event', external_id='other-external',
                     alcor_id='', title='Other calendar event')
        self.bridge.rows.append(other)
        self.null_lookup()
        result = self.sync.sync()
        self.assert_preserved(result)
        self.assertEqual(result['imported'], 1)
        self.assertTrue(any(row['title'] == 'Other calendar event' for row in self.owner.list_events()))
        self.assertEqual(self.sync.status()['scan_claims'], [])

    def test_other_group_scan_keeps_mapping_until_its_claim_finishes(self):
        from apple_sync import group_scan_claims as scans
        self.bridge.rows = []
        self.null_lookup()
        before = copy.deepcopy(self.sync._maps())
        token = scans.acquire(self.sync, 'account1', 'other-group',
                              ('2026-11-01T00:00:00Z', '2026-12-01T00:00:00Z'))
        result = self.sync.sync()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['deleted_local'], 0)
        self.assertEqual(self.sync._maps(), before)
        self.assertIn(self.id, result['blocked_local_ids'])
        self.assertIsNone(self.sync._get('calendar-inbound-conflict:' + self.id))
        scans.complete(self.sync, token)
        self.assert_preserved(self.sync.sync())

    def test_lookup_exception_never_archives_local_data(self):
        self.bridge.rows = []
        original = self.bridge.call
        def unavailable(operation, **arguments):
            if operation == 'lookup':
                raise RuntimeError('synthetic source unavailable')
            return original(operation, **arguments)
        self.bridge.call = unavailable
        result = self.sync.sync()
        self.assertEqual(result['status'], 'error', result)
        self.assertEqual(result['deleted_local'], 0)
        self.assertEqual(self.owner.get_event(self.id)['title'], self.local['title'])
        self.assertEqual(self.owner.list_apple_inbound_recoveries(), [])
        self.assertTrue(self.sync.status()['scan_claims'])

    def test_denied_access_never_archives_local_data(self):
        self.bridge.rows = []
        original = self.bridge.call
        self.bridge.call = lambda op, **kw: {'authorization': 'denied'} if op == 'status' else original(op, **kw)
        result = self.sync.sync()
        self.assertEqual(result['status'], 'denied')
        self.assertEqual(result['deleted_local'], 0)
        self.assertEqual(self.owner.get_event(self.id)['title'], self.local['title'])
        self.assertEqual(self.owner.list_apple_inbound_recoveries(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
