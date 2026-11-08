"""Ordinary calendar moves preserve owner edits and durable split-commit evidence."""
import copy
import json
import os
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = runpy.run_path(str(ROOT / 'product/tests/apple-contracts.py'))


class StrictBridge(FIXTURE['NativeBridge']):
    def call(self, operation, **payload):
        if operation == 'upsert' and payload.get('lookup_calendar_id'):
            current = next((row for row in self.rows if row['calendar_id'] == payload['lookup_calendar_id']
                            and row['event_id'] == payload['event_id']), None)
            destination = next((row for row in self.sources if row['id'] == payload['calendar_id']), None)
            if current != payload.get('expected_snapshot') or destination != payload.get('expected_destination_snapshot'):
                raise ValueError('synthetic native conditional move rejected')
            if destination['source_id'] != payload.get('expected_source_id'):
                raise ValueError('synthetic native source changed')
        return super().call(operation, **payload)


class CalendarMove(unittest.TestCase):
    def setUp(self):
        self.fixture = FIXTURE['Contracts']()
        self.fixture.setUp()
        self.owner = self.fixture.business.calendar
        self.bridge = StrictBridge('calendar')
        self.sync = self.fixture.calendar(self.bridge)
        self.sync.start_sync()
        self.target = self.owner.create_category({'name': 'Synthetic target'})
        self.assertEqual(self.sync.sync()['status'], 'success')
        self.mapping = copy.deepcopy(self.sync._maps()[0])
        self.id = self.mapping['alcor_id']
        self.target_id = next(row['apple_id'] for row in self.sync._stored_links() if row['alcor_id'] == self.target['id'])
        self.owner.set_event_category(self.id, self.target['id'])
        self.bridge.calls.clear()

    def tearDown(self):
        self.fixture.tearDown()

    def writes(self):
        return [call for call in self.bridge.calls if call[0] == 'upsert']

    def assert_held(self, result, writes=1):
        self.assertNotEqual(result['status'], 'success', result)
        self.assertEqual(len(self.writes()), writes)
        self.assertEqual(len(self.sync.status()['scan_claims']), 2)
        with self.sync._connect() as db:
            docs = [json.loads(row[0]) for row in db.execute(
                "SELECT evidence FROM calendar_scan_attempts WHERE state='uncertain'")]
        self.assertTrue(all(row['kind'] == 'calendar_move' for row in docs))
        self.sync.sync()
        self.assertEqual(len(self.writes()), writes)

    def test_normal_move_updates_binding_mapping_marker_and_next_scan_once(self):
        result = self.sync.sync()
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(result['exported'], 1)
        self.assertEqual(result['conflicts'], 0)
        self.assertEqual(self.sync._maps()[0]['calendar_id'], self.target_id)
        self.assertEqual(self.owner.get_event(self.id)['apple']['group_id'], self.target_id)
        self.assertEqual(self.sync._get('calendar-binding-generation:'+self.id)['group_id'], self.target_id)
        self.assertEqual(self.sync.sync()['status'], 'success')
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.bridge.rows), 1)
        self.assertEqual(self.sync.status()['scan_claims'], [])

    def test_local_edit_before_send_prevents_native_move(self):
        original = self.owner.apple_verify_calendar_move
        def changed(*args):
            self.owner.update_event(self.id, {'description': 'later local note'})
            return original(*args)
        with patch.object(self.owner, 'apple_verify_calendar_move', side_effect=changed):
            result = self.sync.sync()
        self.assertNotEqual(result['status'], 'success')
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.sync.status()['scan_claims'], [])
        self.assertEqual(self.owner.get_event(self.id)['description'], 'later local note')
        current = self.sync._maps()[0]
        self.assertEqual(current['state'], 'conflict')
        self.assertTrue(current['conflict'])
        self.assertEqual({**current, 'state': self.mapping['state'], 'conflict': self.mapping['conflict']}, self.mapping)
        self.sync.sync()
        self.assertEqual(self.writes(), [])

    def test_local_edit_during_native_move_is_preserved(self):
        original = self.bridge.call
        def changed(operation, **payload):
            result = original(operation, **payload)
            if operation == 'upsert':
                self.owner.update_event(self.id, {'description': 'edited during native call'})
            return result
        with patch.object(self.bridge, 'call', side_effect=changed):
            result = self.sync.sync()
        self.assert_held(result)
        self.assertEqual(self.owner.get_event(self.id)['description'], 'edited during native call')
        self.assertEqual(self.sync._maps()[0], self.mapping)

    def test_recreated_id_during_native_move_does_not_receive_old_binding(self):
        original = self.bridge.call
        def changed(operation, **payload):
            result = original(operation, **payload)
            if operation == 'upsert':
                self.owner.apple_remove_item(self.id)
                self.owner.create_event({'id': self.id, 'title': 'replacement', 'category_id': self.target['id'],
                                        'start_at': '2026-11-07T01:00:00Z', 'end_at': '2026-11-07T02:00:00Z'})
            return result
        with patch.object(self.bridge, 'call', side_effect=changed):
            result = self.sync.sync()
        self.assert_held(result)
        self.assertEqual(self.owner.get_event(self.id)['title'], 'replacement')
        self.assertFalse(self.owner.get_event(self.id).get('apple'))

    def test_changed_destination_before_send_is_rejected_by_native_condition(self):
        original = self.bridge.call
        def changed(operation, **payload):
            if operation == 'upsert':
                next(row for row in self.bridge.sources if row['id'] == self.target_id)['title'] = 'changed destination'
            return original(operation, **payload)
        with patch.object(self.bridge, 'call', side_effect=changed):
            result = self.sync.sync()
        self.assert_held(result, writes=0)
        self.assertEqual(self.bridge.rows[0]['calendar_id'], self.mapping['calendar_id'])

    def test_changed_remote_before_send_is_rejected_by_native_condition(self):
        original = self.bridge.call
        def changed(operation, **payload):
            if operation == 'upsert':
                self.bridge.rows[0]['description'] = 'new remote edit'
            return original(operation, **payload)
        with patch.object(self.bridge, 'call', side_effect=changed):
            result = self.sync.sync()
        self.assert_held(result, writes=0)
        self.assertEqual(self.bridge.rows[0]['description'], 'new remote edit')

    def test_owner_failure_after_native_keeps_evidence_and_never_resends(self):
        with patch.object(self.owner, 'apple_commit_calendar_move', side_effect=RuntimeError('synthetic owner interruption')):
            result = self.sync.sync()
        self.assert_held(result)
        self.assertEqual(self.sync._maps()[0], self.mapping)

    def test_manager_failure_after_owner_keeps_evidence_and_never_resends(self):
        with patch('apple_sync.calendar_moves._finish', side_effect=RuntimeError('synthetic manager interruption')):
            result = self.sync.sync()
        self.assert_held(result)
        self.assertEqual(self.owner.get_event(self.id)['apple']['group_id'], self.target_id)
        self.assertEqual(self.sync._maps()[0], self.mapping)
        self.assertEqual(self.sync._get('calendar-binding-generation:'+self.id)['group_id'], self.mapping['calendar_id'])

    def test_uncertain_move_does_not_stop_an_unrelated_calendar(self):
        self.bridge.sources.append({**self.bridge.sources[0], 'id': 'unrelated-group', 'title': 'Unrelated'})
        self.bridge.rows.append({**self.bridge.rows[0], 'calendar_id': 'unrelated-group', 'event_id': 'unrelated-event',
                                 'external_id': 'unrelated-external', 'title': 'Unrelated event'})
        with patch.object(self.owner, 'apple_commit_calendar_move', side_effect=RuntimeError('synthetic owner interruption')):
            result = self.sync.sync()
        self.assertEqual(result['status'], 'partial', result)
        self.assertEqual(result['imported'], 1)
        self.assertTrue(any(row['title'] == 'Unrelated event' for row in self.owner.list_events()))
        self.assertEqual(len(self.writes()), 1)

    def test_changed_manager_after_owner_is_not_overwritten(self):
        import apple_sync.calendar_moves as moves
        original = moves._finish
        def changed(sync, *args):
            with sync._connect() as db:
                db.execute("UPDATE mappings SET conflict='new manager evidence' WHERE alcor_id=?", (self.id,))
            return original(sync, *args)
        with patch.object(moves, '_finish', side_effect=changed):
            result = self.sync.sync()
        self.assert_held(result)
        self.assertEqual(self.sync._maps()[0]['conflict'], 'new manager evidence')

    def test_owner_receipt_replay_preserves_later_edit_and_rejects_new_incarnation(self):
        self.assertEqual(self.sync.sync()['status'], 'success')
        with self.sync._connect() as db:
            doc = next(json.loads(row[0]) for row in db.execute('SELECT evidence FROM calendar_scan_attempts')
                       if json.loads(row[0]).get('kind') == 'calendar_move')
        args = [doc['operation_id'], doc['owner_expected'], doc['destinations'][0]['id'], doc['destinations'][1], doc['native_result']]
        self.owner.update_event(self.id, {'description': 'later note'})
        self.assertTrue(self.owner.apple_commit_calendar_move(*args)['replayed'])
        self.assertEqual(self.owner.get_event(self.id)['description'], 'later note')
        self.owner.apple_remove_item(self.id)
        self.owner.create_event({'id': self.id, 'title': 'replacement', 'start_at': '2026-11-07T01:00:00Z', 'end_at': '2026-11-07T02:00:00Z'})
        with self.assertRaises(ValueError):
            self.owner.apple_commit_calendar_move(*args)
        self.assertEqual(self.owner.get_event(self.id)['title'], 'replacement')

    def test_both_groups_block_second_process_approved_reservation(self):
        original = self.bridge.call
        def paused(operation, **payload):
            if operation == 'upsert':
                for group in (self.mapping['calendar_id'], self.target_id):
                    approved = {'operation_id': 'a'*64, 'approval_fingerprint': 'b'*64, 'plan': {'items': [{
                        'local_id': 'another-item', 'source_id': 'account1', 'group_id': group,
                        'destination_snapshot': {'id': group, 'source_id': 'account1'}}]}}
                    code = "import json,sys\nfrom apple_sync.calendar import AppleCalendarSync\nfrom apple_sync.export_reservations import reserve\ns=AppleCalendarSync(bridge=object(),db_path=sys.argv[1],calendar_port=object())\ntry:reserve(s,json.load(sys.stdin));print('reserved')\nexcept ValueError:print('blocked')\n"
                    child = subprocess.run([sys.executable, '-B', '-c', code, str(self.sync.db_path)], input=json.dumps(approved),
                                           capture_output=True, text=True, check=True,
                                           env={**os.environ, 'PYTHONPATH': str(ROOT/'packages/alcor-apple-sync/python')})
                    self.assertEqual(child.stdout.strip(), 'blocked')
            return original(operation, **payload)
        with patch.object(self.bridge, 'call', side_effect=paused):
            result = self.sync.sync()
        self.assertEqual(result['status'], 'success', result)

    def test_existing_scan_in_either_group_prevents_move(self):
        from apple_sync import group_scan_claims as scans
        for group in (self.mapping['calendar_id'], self.target_id):
            with self.subTest(group=group):
                token = scans.acquire(self.sync, 'account1', group, ('2026-11-01', '2026-12-01'))
                self.assertNotEqual(self.sync.sync()['status'], 'success')
                self.assertEqual(self.writes(), [])
                self.assertEqual(self.sync._maps()[0], self.mapping)
                scans.complete(self.sync, token)

    def test_move_owner_methods_are_not_public_business_commands(self):
        for method in ('apple_verify_calendar_move', 'apple_commit_calendar_move'):
            with self.assertRaises((ValueError, KeyError, TypeError)):
                self.fixture.business.invoke('calendar', method, {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
