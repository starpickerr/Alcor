"""Real isolated owners/managers; fake EventKit with occurrence-qualified IDs."""
import copy
import json
import runpy
import unittest
from pathlib import Path

M = runpy.run_path(str(Path(__file__).with_name('apple-approved-export.py')))
from apple_sync.approved_export import prepare, run as export
from apple_sync.bound_conflict import run as resolve
from apple_sync.calendar import AppleCalendarSync
from apple_sync.calendar_rebind import _instant


class OccurrenceBridge(M['ConditionalBridge']):
    def call(self, op, **payload):
        if op != 'upsert':
            return super().call(op, **payload)
        self.assertion_payload = copy.deepcopy(payload)
        matching = [row for row in self.rows if row['calendar_id'] == payload['calendar_id']
                    and row['event_id'] == payload['event_id']
                    and abs((_instant(row['occurrence_date']) - _instant(payload['occurrence_date'])).total_seconds()) < 1]
        assert len(matching) == 1
        assert matching[0] == payload['expected_snapshot']
        assert payload['expected_destination_snapshot'] == self.sources[0]
        assert payload['expected_source_id'] == self.sources[0]['source_id']
        assert payload.get('series') is False
        path = Path(payload['receipt_file'])
        assert not path.exists()
        result = M['M']['NativeBridge'].call(self, op, **payload)
        path.write_text(json.dumps({'binding': payload['receipt_binding'], 'saved': result['event']}))
        self.writes += 1
        return result


class Occurrences(unittest.TestCase):
    def setUp(self):
        self.case = M['M']['Contracts']('test_delete_keeps_local_data_until_apple_acknowledges')
        self.case.setUp()
        self.owner = self.case.business.calendar
        self.bridge = OccurrenceBridge('calendar')
        first = M['M']['NativeBridge']('calendar').rows[0]
        first['recurrence'] = {'frequency': 'weekly', 'interval': 1, 'days': [3]}
        second = {**copy.deepcopy(first), 'occurrence_date': '2026-11-12T01:00:00Z',
                  'start_at': '2026-11-12T01:00:00Z', 'end_at': '2026-11-12T02:00:00Z', 'title': 'Second occurrence'}
        self.bridge.rows = [first, second]
        self.sync = AppleCalendarSync(bridge=self.bridge, db_path=self.case.root/'sync.sqlite3', calendar_port=self.owner)
        self.sync.start_auto = lambda: None
        self.sync.start_sync()
        mappings = sorted(self.sync._maps(), key=lambda row: row['occurrence_date'])
        self.assertEqual(len(mappings), 2)
        self.ids = [row['alcor_id'] for row in mappings]

    def tearDown(self):
        self.case.tearDown()

    def plan(self, choice='keep_apple', operation='a'):
        return prepare(self.sync, 'calendar', 'resolve_conflict',
                       {'kind': 'calendar', 'identity': self.ids[0], 'choice': choice}, operation * 64)

    def conflict(self):
        self.owner.update_event(self.ids[0], {'title': 'Local first'})
        self.bridge.rows[0]['title'] = 'Apple first'
        self.sync.sync()
        self.assertEqual(next(row for row in self.sync._maps() if row['alcor_id'] == self.ids[0])['state'], 'conflict')

    def test_one_time_prepare_through_commit_keeps_distinct_occurrence(self):
        self.owner.update_event(self.ids[0], {'title': 'Exported first'})
        second = self.owner.get_event(self.ids[1])
        second_native = copy.deepcopy(self.bridge.rows[1])
        plan = prepare(self.sync, 'calendar', 'one_time',
                       {'kind': 'calendar', 'direction': 'export', 'calendar_id': 'a', 'item_ids': [self.ids[0]]}, 'a' * 64)
        result = export(self.sync, 'calendar', plan, lambda *args: True)
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(self.bridge.writes, 1)
        self.assertEqual(self.bridge.rows[0]['title'], 'Exported first')
        self.assertEqual(self.owner.get_event(self.ids[1]), second)
        self.assertEqual(self.bridge.rows[1], second_native)
        self.assertEqual(len(self.sync._maps()), 2)

    def test_bound_keep_apple_and_keep_alcor_allow_distinct_occurrences(self):
        self.conflict()
        second = self.owner.get_event(self.ids[1])
        second_native = copy.deepcopy(self.bridge.rows[1])
        result = resolve(self.sync, self.owner, 'calendar', self.plan(), lambda *args: True)
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(self.owner.get_event(self.ids[0])['title'], 'Apple first')
        self.assertEqual(self.bridge.writes, 0)
        self.owner.update_event(self.ids[0], {'title': 'Local again'})
        self.bridge.rows[0]['title'] = 'Apple again'
        self.sync.sync()
        result = resolve(self.sync, self.owner, 'calendar', self.plan('keep_alcor', 'b'), lambda *args: True)
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(self.bridge.rows[0]['title'], 'Local again')
        self.assertEqual(self.bridge.writes, 1)
        self.assertEqual(self.owner.get_event(self.ids[1]), second)
        self.assertEqual(self.bridge.rows[1], second_native)
        for _ in range(2): self.sync.sync()
        self.assertEqual(self.bridge.writes, 1)

    def test_same_occurrence_different_timezone_is_duplicate_owner_binding(self):
        self.conflict()
        binding = copy.deepcopy(self.owner.get_event(self.ids[1])['apple'])
        binding['native']['occurrence_date'] = '2026-11-05T09:00:00+08:00'
        self.owner.apple_bind('item', self.ids[1], binding)
        with self.assertRaisesRegex(ValueError, 'another owner binding'):
            self.owner.apple_capture_approved_export(self.ids[0])
        with self.assertRaises(ValueError): self.plan()
        with self.assertRaises(ValueError):
            prepare(self.sync, 'calendar', 'one_time',
                    {'kind': 'calendar', 'direction': 'export', 'calendar_id': 'a', 'item_ids': [self.ids[0]]}, 'b' * 64)
        self.assertEqual(self.bridge.writes, 0)

    def test_same_occurrence_different_timezone_is_duplicate_manager_mapping(self):
        self.conflict()
        with self.sync._connect() as db:
            db.execute('UPDATE mappings SET occurrence_date=? WHERE alcor_id=?', ('2026-11-05T09:00:00+08:00', self.ids[1]))
        self.owner.apple_capture_approved_export(self.ids[0])  # Owner bindings are still distinct.
        with self.assertRaises(ValueError): self.plan()
        self.assertEqual(self.bridge.writes, 0)

    def test_requested_occurrence_must_match_binding_and_native(self):
        self.conflict()
        original = copy.deepcopy(self.owner.get_event(self.ids[0])['apple'])
        for value in (None, '', '2026-11-05T01:00:00', '2026-11-06T01:00:00Z'):
            with self.subTest(value=value):
                binding = copy.deepcopy(original)
                binding['native']['occurrence_date'] = value
                self.owner.apple_bind('item', self.ids[0], binding)
                with self.assertRaises(ValueError): self.plan()
        self.assertEqual(self.bridge.writes, 0)

    def test_subsecond_overlap_is_ambiguous_like_native_lookup(self):
        self.conflict()
        binding = copy.deepcopy(self.owner.get_event(self.ids[1])['apple'])
        binding['native']['occurrence_date'] = '2026-11-05T01:00:00.500Z'
        self.owner.apple_bind('item', self.ids[1], binding)
        with self.assertRaises(ValueError): self.plan()
        self.assertEqual(self.bridge.writes, 0)

    def test_missing_or_timezone_free_occurrence_is_not_assumed_distinct(self):
        self.conflict()
        original = copy.deepcopy(self.owner.get_event(self.ids[1])['apple'])
        for value in (None, '', '2026-11-12T01:00:00'):
            with self.subTest(value=value):
                binding = copy.deepcopy(original)
                binding['native']['occurrence_date'] = value
                self.owner.apple_bind('item', self.ids[1], binding)
                with self.assertRaises(ValueError): self.plan()
        self.owner.apple_bind('item', self.ids[1], original)
        for value in ('', '2026-11-12T01:00:00'):
            with self.subTest(manager=value):
                with self.sync._connect() as db:
                    db.execute('UPDATE mappings SET occurrence_date=? WHERE alcor_id=?', (value, self.ids[1]))
                with self.assertRaises(ValueError): self.plan()
        self.assertEqual(self.bridge.writes, 0)


if __name__ == '__main__': unittest.main(verbosity=2)
