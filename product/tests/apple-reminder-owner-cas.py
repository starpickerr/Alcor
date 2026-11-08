"""Owner-only synthetic reminder transactions. Never calls EventKit."""
import copy
import json
import runpy
import tempfile
import unittest
from pathlib import Path
M = runpy.run_path(str(Path(__file__).with_name('business-recovery.py')))
Business = M['Business']


class ReminderOwnerCAS(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.business = Business(Path(self.tmp.name) / 'business')
        self.owner = self.business.tasks
        self.category = self.owner.create_category({'name': 'Synthetic'})
        self.owner.apple_bind('category', self.category['id'], {'apple_id': 'list', 'source_id': 'source', 'can_modify': True})
        self.local = self.owner.create_todo({'title': 'Before', 'category_id': self.category['id'], 'tags': ['local']}, _external_source_key='eventkit:native')
        self.identity = self.local['id']
        self.native = dict(reminder_id='native', list_id='list', source_id='source', external_id='series', title='After', completed=False, recurrence={'frequency': 'none'})
        self.owner.apple_bind('item', self.identity, {'apple_id': 'native', 'group_id': 'list', 'source_id': 'source', 'native': self.native, 'can_modify': True, 'can_delete': True})

    def tearDown(self):
        self.tmp.cleanup()

    def capture(self):
        return self.owner.apple_capture_reminder_sync(self.identity)

    def apply(self, expected=None, native=None, **kwargs):
        return self.owner.apple_apply_reminder_inbound(expected or self.capture(), {'title': 'After', 'completed': False}, native or self.native, **kwargs)

    def test_normal_preserves_local_fields_and_metadata(self):
        row = self.apply()
        self.assertEqual(row['title'], 'After')
        self.assertEqual(json.loads(row['tags']), ['local'])
        self.assertTrue(row['native_managed'])
        self.assertEqual(row['apple']['native'], self.native)
        self.apply(metadata_only=True)
        self.assertEqual(self.owner.get_todo(self.identity)['title'], 'After')

    def test_metadata_only_does_not_replace_content(self):
        self.apply(metadata_only=True)
        self.assertEqual(self.owner.get_todo(self.identity)['title'], 'Before')

    def test_capture_then_edit_rejects(self):
        expected = self.capture()
        self.owner.update_todo(self.identity, {'title': 'User edit'})
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.apply(expected)
        self.assertEqual(self.owner.get_todo(self.identity)['title'], 'User edit')

    def test_recreated_same_id_rejects(self):
        expected = self.capture()
        with self.owner._connect() as db:
            row = dict(db.execute('SELECT * FROM todos WHERE id=?', (self.identity,)).fetchone())
            db.execute('DELETE FROM todos WHERE id=?', (self.identity,))
            db.execute('INSERT INTO todos ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', list(row.values()))
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.apply(expected)

    def test_category_edit_rejects(self):
        expected = self.capture()
        with self.owner._connect() as db:
            db.execute('UPDATE todo_categories SET name=? WHERE id=?', ('Changed', self.category['id']))
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.apply(expected)

    def test_new_child_rejects(self):
        expected = self.capture()
        child = self.owner.create_todo({'title': 'Child'})
        with self.owner._connect() as db:
            db.execute('UPDATE todos SET parent_id=? WHERE id=?', (self.identity, child['id']))
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.apply(expected)

    def test_new_dependency_rejects(self):
        expected = self.capture()
        other = self.owner.create_todo({'title': 'Other'})
        with self.owner._connect() as db:
            db.execute('INSERT INTO todo_dependencies(task_id,prerequisite_id) VALUES (?,?)', (self.identity, other['id']))
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.apply(expected)

    def test_history_change_rejects(self):
        expected = self.capture()
        with self.owner._connect() as db:
            db.execute('INSERT INTO apple_completion_history VALUES (?,?,?,?)', (self.identity, 'old', '', '{}'))
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.apply(expected)

    def test_legacy_source_requires_exact_item_and_bound_category(self):
        binding = self.owner.get_todo(self.identity)['apple']
        binding.pop('source_id')
        self.owner.apple_bind('item', self.identity, binding)
        expected = self.capture()
        for bad in ({**self.native, 'reminder_id': 'other'}, {**self.native, 'source_id': ''}, {**self.native, 'source_id': 'other'}):
            with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
                self.apply(expected, bad)
        row = self.apply(expected)
        self.assertEqual(row['apple']['source_id'], 'source')

    def test_successor_requires_proven_series_and_history_once(self):
        previous = {**self.native, 'completed': True, 'due_on': '2026-11-08'}
        successor = {**self.native, 'reminder_id': 'successor'}
        expected = self.capture()
        for bad in ({**successor, 'external_id': ''}, {**successor, 'source_id': 'other'}):
            with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
                self.apply(expected, bad, previous_completion=previous)
        self.apply(expected, successor, previous_completion=previous)
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.apply(expected, successor, previous_completion=previous)
        with self.owner._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM apple_completion_history').fetchone()[0], 1)

    def test_pending_operation_rejected(self):
        with self.owner._connect() as db:
            self.owner._apple_queue(db, 'item', self.identity, 'complete', {'completed': True})
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.apply()

    def test_cancelled_preserved(self):
        with self.owner._connect() as db:
            db.execute("UPDATE todos SET status='cancelled' WHERE id=?", (self.identity,))
        row = self.owner.apple_apply_reminder_inbound(self.capture(), {'title': 'After', 'completed': True}, {**self.native, 'completed': True})
        self.assertEqual(row['status'], 'cancelled')

    def test_delete_restore_local_only_no_duplicate_reminders(self):
        expected = self.owner.apple_capture_reminder_missing(self.identity)
        self.assertTrue(self.owner.apple_remove_reminder_inbound(expected['capture'], 'source', 'list', 'native', expected['capture']['generation'], expected['deletion_snapshot']))
        recoveries = self.owner.list_apple_inbound_recoveries()
        self.assertEqual(len(recoveries), 1)
        restored = self.owner.restore_apple_inbound_recovery(recoveries[0]['id'])
        self.assertEqual(restored['sync'], 'local_only')
        row = self.owner.get_todo(restored['ids'][0])
        self.assertFalse(row.get('apple'))
        self.assertEqual(row['source_type'], 'apple_recovered')
        self.assertEqual(row['title'], 'Before')
        with self.owner._connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM reminder_occurrences WHERE state='pending'").fetchone()[0], 0)

    def test_delete_stale_snapshot_rejected(self):
        missing = self.owner.apple_capture_reminder_missing(self.identity)
        self.owner.update_todo(self.identity, {'title': 'Keep'})
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.owner.apple_remove_reminder_inbound(missing['capture'], 'source', 'list', 'native', missing['capture']['generation'], missing['deletion_snapshot'])
        self.assertEqual(self.owner.get_todo(self.identity)['title'], 'Keep')

    def test_native_update_cancels_local_schedule(self):
        local = self.owner.create_todo({'title': 'Local alarm', 'remind_at': '2099-11-08T12:00:00+00:00', 'category_id': self.category['id']})
        binding = self.owner.get_todo(self.identity)['apple']
        self.owner.apple_bind('item', local['id'], binding)
        self.owner.apple_apply_reminder_inbound(self.owner.apple_capture_reminder_sync(local['id']), {'title': 'Remote', 'completed': False}, self.native)
        with self.owner._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM todo_scheduler_links WHERE todo_id=? AND desired_at IS NOT NULL', (local['id'],)).fetchone()[0], 0)
        self.assertFalse(self.owner.queue_scheduled_reminder(local['id'], '2099-11-08T12:00:00+00:00'))

    def test_child_blocks_archive_without_partial_recovery(self):
        child = self.owner.create_todo({'title': 'Child'})
        with self.owner._connect() as db:
            db.execute('UPDATE todos SET parent_id=? WHERE id=?', (self.identity, child['id']))
        missing = self.owner.apple_capture_reminder_missing(self.identity)
        with self.assertRaisesRegex(ValueError, 'subtasks'):
            self.owner.apple_remove_reminder_inbound(missing['capture'], 'source', 'list', 'native', missing['capture']['generation'], missing['deletion_snapshot'])
        self.assertEqual(self.owner.get_todo(child['id'])['parent_id'], self.identity)
        self.assertEqual(self.owner.list_apple_inbound_recoveries(), [])

    def test_unbound_category_is_explicit_conflict(self):
        with self.owner._connect() as db:
            db.execute("DELETE FROM apple_bindings WHERE entity='category'")
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.apply()

    def test_absent_capture_category_is_explicit_conflict(self):
        from business.reminder_sync import _identity
        expected = self.capture()
        expected['categories'] = []
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            _identity(expected, self.native)

    def test_private_peer_not_public_catalog(self):
        for name in ('apple_begin_reminder_completion', 'apple_capture_reminder_completion', 'apple_finalize_reminder_completion', 'apple_capture_reminder_sync', 'apple_verify_reminder_sync', 'apple_apply_reminder_inbound', 'apple_capture_reminder_missing', 'apple_remove_reminder_inbound'):
            self.assertNotIn(name, [item['name'] for descriptor in self.business.entries['tasks'].SERVICE_DESCRIPTORS for item in descriptor['methods']])
        peer = self.business.apple_peer('tasks', 'apple_capture_reminder_sync', [self.identity])
        self.assertEqual(peer['local_id'], self.identity)

    def completion(self):
        with self.owner._connect() as db:
            self.owner._apple_queue(db, 'item', self.identity, 'complete', {'completed': True, 'occurrence_on': '2026-11-08'})
        operation = next(row for row in self.owner.apple_operations() if row['local_id'] == self.identity)
        expected = self.owner.apple_capture_reminder_completion(self.identity, operation)
        return operation, expected

    def finalize(self, operation, expected, **kwargs):
        native = {**self.native, 'completed': True, 'completed_at': '2026-11-08T12:00:00Z', 'due_on': '2026-11-08'}
        return self.owner.apple_finalize_reminder_completion(expected, operation,
            {'title': 'Completed', 'completed': True, 'completed_at': native['completed_at']}, native,
            previous_completion=native, **kwargs)

    def test_completion_requires_begin_then_receipt_replay_after_delete(self):
        operation, expected = self.completion()
        with self.assertRaisesRegex(ValueError, 'has not begun'):
            self.finalize(operation, expected)
        self.owner.apple_begin_reminder_completion(expected, operation)
        first = self.finalize(operation, expected)
        self.assertTrue(first['committed'])
        self.assertFalse(first['replayed'])
        self.assertEqual(first['local']['status'], 'completed')
        self.assertEqual(first['local']['apple_pending'], [])
        self.assertEqual(self.owner.apple_operations(), [])
        with self.owner._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM apple_completion_history').fetchone()[0], 1)
            db.execute('DELETE FROM todos WHERE id=?', (self.identity,))
        replay = self.finalize(operation, expected)
        self.assertEqual(replay, {**first, 'replayed': True})
        with self.owner._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM apple_completion_history').fetchone()[0], 1)

    def test_completion_uncertain_transport_error_only_allowed(self):
        operation, expected = self.completion()
        self.owner.apple_begin_reminder_completion(expected, operation)
        self.owner.apple_operation_error(operation['id'], 'Transport unknown; manager subsequently verified receipt')
        self.assertTrue(self.finalize(operation, expected)['committed'])

    def test_completion_edit_after_capture_rejected_without_receipt(self):
        operation, expected = self.completion()
        self.owner.apple_begin_reminder_completion(expected, operation)
        self.owner.update_todo(self.identity, {'title': 'Keep user edit'})
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.finalize(operation, expected)
        self.assertEqual(self.owner.get_todo(self.identity)['title'], 'Keep user edit')
        self.assertEqual(self.owner.apple_operations()[0]['state'], 'sending')
        with self.owner._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM apple_completion_history').fetchone()[0], 0)

    def test_completion_other_pending_rejected(self):
        operation, expected = self.completion()
        self.owner.apple_begin_reminder_completion(expected, operation)
        with self.owner._connect() as db:
            self.owner._apple_queue(db, 'item', self.identity, 'complete', {'completed': False})
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.finalize(operation, expected)

    def test_completion_changed_document_rejected(self):
        operation, expected = self.completion()
        self.owner.apple_begin_reminder_completion(expected, operation)
        with self.owner._connect() as db:
            db.execute("UPDATE apple_operations SET document=? WHERE id=?", ('{"completed": false}', operation['id']))
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.finalize(operation, expected)

    def test_completion_receipt_rejects_changed_evidence(self):
        operation, expected = self.completion()
        self.owner.apple_begin_reminder_completion(expected, operation)
        self.finalize(operation, expected)
        changed = copy.deepcopy(expected)
        changed['capture']['local']['title'] = 'Other evidence'
        with self.assertRaisesRegex(ValueError, 'idempotency conflict'):
            self.finalize(operation, changed)

    def test_completion_successor_atomic_history_and_binding(self):
        operation, expected = self.completion()
        self.owner.apple_begin_reminder_completion(expected, operation)
        previous = {**self.native, 'completed': True, 'due_on': '2026-11-08'}
        native = {**self.native, 'reminder_id': 'next', 'due_on': '2026-11-09'}
        result = self.owner.apple_finalize_reminder_completion(expected, operation,
            {'title': 'Next', 'completed': False}, native, previous)
        self.assertEqual(result['local']['apple']['apple_id'], 'next')
        self.assertEqual(result['local']['status'], 'todo')
        self.assertEqual(result['local']['completion_history'][0], previous)
        self.assertEqual(self.owner.apple_operations(), [])

    def test_completion_receipt_failure_rolls_back_all_owner_writes(self):
        from unittest.mock import patch
        operation, expected = self.completion()
        self.owner.apple_begin_reminder_completion(expected, operation)
        before = self.owner.apple_capture_reminder_completion(self.identity, operation)
        with patch('business.security_receipts.save_receipt', side_effect=RuntimeError('Synthetic receipt failure')):
            with self.assertRaisesRegex(RuntimeError, 'Synthetic receipt failure'):
                self.finalize(operation, expected)
        self.assertEqual(self.owner.apple_capture_reminder_completion(self.identity, operation), before)
        with self.owner._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM apple_completion_history').fetchone()[0], 0)
        self.assertEqual(self.owner.apple_operations()[0]['state'], 'sending')

    def test_completion_operation_created_at_not_normalized(self):
        operation, expected = self.completion()
        self.owner.apple_begin_reminder_completion(expected, operation)
        with self.owner._connect() as db:
            db.execute('UPDATE apple_operations SET created_at=? WHERE id=?', ('changed', operation['id']))
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.finalize(operation, expected)

    def test_completion_begin_rejects_edit_without_entering_sending(self):
        operation, expected = self.completion()
        self.owner.update_todo(self.identity, {'title': 'Edited before sending'})
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.owner.apple_begin_reminder_completion(expected, operation)
        self.assertEqual(self.owner.apple_operations()[0]['state'], 'pending')
        self.assertEqual(self.owner.get_todo(self.identity)['title'], 'Edited before sending')

    def test_completion_begin_rejects_recreated_same_id(self):
        operation, expected = self.completion()
        with self.owner._connect() as db:
            row = dict(db.execute('SELECT * FROM todos WHERE id=?', (self.identity,)).fetchone())
            db.execute('DELETE FROM todos WHERE id=?', (self.identity,))
            db.execute('INSERT INTO todos ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', list(row.values()))
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.owner.apple_begin_reminder_completion(expected, operation)
        self.assertEqual(self.owner.apple_operations()[0]['state'], 'pending')

    def test_completion_begin_is_single_use_and_rejects_other_pending(self):
        operation, expected = self.completion()
        self.assertTrue(self.owner.apple_begin_reminder_completion(expected, operation))
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.owner.apple_begin_reminder_completion(expected, operation)
        self.assertEqual(self.owner.apple_operations()[0]['state'], 'sending')

    def test_completion_begin_other_pending_rejected(self):
        operation, expected = self.completion()
        with self.owner._connect() as db:
            self.owner._apple_queue(db, 'item', self.identity, 'complete', {'completed': False})
        with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
            self.owner.apple_begin_reminder_completion(expected, operation)
        self.assertTrue(all(item['state'] == 'pending' for item in self.owner.apple_operations()))

if __name__ == '__main__':
    unittest.main()
