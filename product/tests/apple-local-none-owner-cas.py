"""Missing-local restoration: synthetic owner SQLite only, no EventKit or Profile."""
import copy
import importlib
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'packages/alcor-secretary'))
sys.path.insert(0, str(ROOT / 'packages/alcor-apple-sync/python'))

from business.calendar.domain.service import CalendarService
from business.tasks.domain.service import TodoService
from business import apple_item_restore
from business.worker import Business
from apple_sync.calendar import _payload as calendar_payload
from apple_sync.reminders import _payload as reminder_payload


@contextmanager
def case(kind):
    with tempfile.TemporaryDirectory() as path:
        owner = (CalendarService if kind == 'calendar' else TodoService)(Path(path) / 'owner.sqlite')
        category = owner.create_category({'name': 'Synthetic restore'})['id']
        owner.apple_bind('category', category, {'apple_id': 'group', 'source_id': 'source', 'can_modify': True})
        native = {'event_id': 'native', 'calendar_id': 'group', 'reminder_id': 'native', 'list_id': 'group',
                  'source_id': 'source', 'external_id': 'external', 'title': 'Native retained',
                  'start_at': '2026-11-09T09:00:00Z', 'end_at': '2026-11-09T10:00:00Z',
                  'timezone': 'UTC', 'all_day': False, 'recurrence': {'frequency': 'none'}, 'writable': True,
                  'due_on': '2026-11-09', 'due_time': '09:00', 'completed': False,
                  'remind_at': '2099-11-09T08:30:00Z', 'alarm_count': 1}
        payload = (calendar_payload if kind == 'calendar' else reminder_payload)(native, category)
        with owner._connect() as db:
            db.execute('INSERT INTO source_tombstones VALUES (?,?)', ('eventkit:old', '2026-11-08T00:00:00Z'))
        owner.apple_bind('item', 'old-id', {'apple_id': 'native', 'group_id': 'group', 'source_id': 'source', 'native': native, 'can_modify': True})
        expected = owner.apple_capture_item_restore('old-id', payload, category, native, 'eventkit:old')
        yield owner, expected


def database(owner):
    with owner._connect() as db:
        return list(db.iterdump())


class MissingLocalOwnerCAS(unittest.TestCase):
    def test_atomic_restore_and_exact_read_only_replay(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), case(kind) as (owner, expected):
                before = database(owner)
                self.assertIsNone(owner.apple_verify_item_restore('1' * 64, expected))
                self.assertEqual(database(owner), before)
                guard = patch.object(owner, '_sync_scheduler_links', side_effect=AssertionError('must not deliver')) if kind == 'reminders' else patch.object(owner, 'create_event', side_effect=AssertionError('must not open a second transaction'))
                with guard:
                    result = owner.apple_restore_item('1' * 64, expected)
                    committed = database(owner)
                    replay = owner.apple_verify_item_restore('1' * 64, expected)
                    repeated = owner.apple_restore_item('1' * 64, expected)
                self.assertFalse(result['replayed'])
                self.assertTrue(replay['replayed'])
                self.assertEqual(replay, repeated)
                self.assertEqual(result['local'], replay['local'])
                self.assertEqual(database(owner), committed)
                self.assertEqual(result['receipt']['operationId'], '1' * 64)
                self.assertEqual(len(result['generation']), 32)
                self.assertEqual(result['local']['id'], 'old-id')
                self.assertEqual(result['local']['source_type'], 'apple_calendar' if kind == 'calendar' else 'apple_reminder')
                self.assertEqual(result['local']['apple']['native'], expected['native'])
                with owner._connect() as db:
                    self.assertFalse(db.execute('SELECT 1 FROM source_tombstones').fetchone())
                    self.assertEqual(db.execute(f'SELECT count(*) FROM {owner.APPLE_ITEMS}').fetchone()[0], 1)
                    if kind == 'reminders':
                        self.assertTrue(result['local']['native_managed'])
                        self.assertTrue(owner._apple_native_reminder(result['local']))
                        self.assertFalse(db.execute('SELECT 1 FROM todo_scheduler_links').fetchone())
                        self.assertFalse(db.execute('SELECT 1 FROM reminder_deliveries').fetchone())

    def test_concurrent_same_id_creation_and_edit_survives(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), case(kind) as (owner, expected):
                payload = {**expected['payload'], 'id': 'old-id', 'title': 'Created concurrently', 'remind_at': None}
                (owner.create_event if kind == 'calendar' else owner.create_todo)(payload)
                (owner.update_event if kind == 'calendar' else owner.update_todo)('old-id', {'title': 'New edit'})
                before = database(owner)
                with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
                    owner.apple_restore_item('2' * 64, expected)
                self.assertEqual(database(owner), before)

    def test_evidence_changes_abort_without_removing_tombstone(self):
        for kind in ('calendar', 'reminders'):
            for change in ('binding', 'other_binding', 'category', 'category_generation', 'generation_residue', 'history', 'tombstone', 'operation'):
                with self.subTest(kind=kind, change=change), case(kind) as (owner, expected):
                    with owner._connect() as db:
                        if change == 'binding':
                            db.execute("UPDATE apple_bindings SET document=? WHERE entity='item'", (json.dumps({'apple_id': 'someone-else'}),))
                        elif change == 'other_binding':
                            db.execute('INSERT INTO apple_bindings VALUES (?,?,?)', ('item', 'another-owner', json.dumps({'apple_id': 'native', 'group_id': 'group', 'source_id': 'source'})))
                        elif change == 'category':
                            db.execute(f'UPDATE {owner.APPLE_CATEGORIES} SET name=? WHERE id=?', ('Changed category', expected['category_id']))
                        elif change == 'category_generation':
                            db.execute("UPDATE alcor_object_generations SET generation=? WHERE entity='category' AND object_id=?", ('a' * 32, expected['category_id']))
                        elif change == 'generation_residue':
                            db.execute('INSERT INTO alcor_object_generations VALUES (?,?,?)', ('item', 'old-id', 'a' * 32))
                        elif change == 'history':
                            db.execute('INSERT INTO apple_completion_history VALUES (?,?,?,?)', ('old-id', 'previous', '', '{}'))
                        elif change == 'tombstone':
                            db.execute('UPDATE source_tombstones SET deleted_at=?', ('new-deletion',))
                        elif change == 'operation':
                            db.execute('INSERT INTO apple_operations VALUES (?,?,?,?,?,?,?,?)', ('operation', 'item', 'old-id', 'complete', '{}', 'pending', '', '2026-11-08'))
                    before = database(owner)
                    with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
                        owner.apple_restore_item('3' * 64, expected)
                    self.assertEqual(database(owner), before)

    def test_failure_rolls_back_item_binding_tombstone_and_receipt(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), case(kind) as (owner, expected):
                before = database(owner)
                with patch.object(apple_item_restore, 'save_receipt', side_effect=RuntimeError('synthetic receipt failure')):
                    with self.assertRaisesRegex(RuntimeError, 'synthetic receipt failure'):
                        owner.apple_restore_item('4' * 64, expected)
                self.assertEqual(database(owner), before)
                self.assertIsNone(owner.apple_verify_item_restore('4' * 64, expected))

    def test_lost_reply_replay_rejects_edited_or_recreated_result(self):
        for kind in ('calendar', 'reminders'):
            for change in ('edit', 'recreate', 'binding', 'category', 'same_operation_new_payload'):
                with self.subTest(kind=kind, change=change), case(kind) as (owner, expected):
                    owner.apple_restore_item('5' * 64, expected)
                    with owner._connect() as db:
                        if change == 'edit':
                            db.execute(f'UPDATE {owner.APPLE_ITEMS} SET title=? WHERE id=?', ('Post-commit edit', 'old-id'))
                        elif change == 'recreate':
                            row = dict(db.execute(f'SELECT * FROM {owner.APPLE_ITEMS} WHERE id=?', ('old-id',)).fetchone())
                            db.execute(f'DELETE FROM {owner.APPLE_ITEMS} WHERE id=?', ('old-id',))
                            db.execute(f"INSERT INTO {owner.APPLE_ITEMS} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
                        elif change == 'binding':
                            db.execute("UPDATE apple_bindings SET document='{}' WHERE entity='item'")
                        elif change == 'category':
                            db.execute(f'UPDATE {owner.APPLE_CATEGORIES} SET name=? WHERE id=?', ('Post-commit category', expected['category_id']))
                        else:
                            expected = copy.deepcopy(expected)
                            expected['native']['title'] = 'Changed request'
                    before = database(owner)
                    for operation in (owner.apple_verify_item_restore, owner.apple_restore_item):
                        with self.assertRaises(ValueError):
                            operation('5' * 64, expected)
                    self.assertEqual(database(owner), before)

    def test_missing_binding_and_local_origin_remain_supported(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), case(kind) as (owner, expected):
                with owner._connect() as db:
                    db.execute("DELETE FROM apple_bindings WHERE entity='item'")
                expected = owner.apple_capture_item_restore('old-id', expected['payload'], expected['category_id'], expected['native'])
                result = owner.apple_restore_item('6' * 64, expected)
                self.assertIsNone(result['local']['source_type'])
                self.assertIsNone(result['local']['source_key'])
                if kind == 'reminders':
                    self.assertTrue(owner._apple_native_reminder(result['local']))

    def test_completed_reminder_preserved_without_delivery(self):
        with case('reminders') as (owner, expected):
            native = {**expected['native'], 'completed': True, 'completed_at': '2026-11-08T12:00:00Z'}
            expected = owner.apple_capture_item_restore('old-id', reminder_payload(native), expected['category_id'], native, expected['source_key'])
            result = owner.apple_restore_item('7' * 64, expected)
            self.assertEqual(result['local']['status'], 'completed')
            self.assertEqual(result['local']['completed_at'], '2026-11-08T12:00:00+00:00')
            self.assertEqual(result['local']['native_snapshot'], native)

    def test_competing_source_row_and_pending_deletes_are_rejected(self):
        for kind in ('calendar', 'reminders'):
            for change in ('source', 'pending', 'sending', 'uncertain'):
                with self.subTest(kind=kind, change=change), case(kind) as (owner, expected):
                    with owner._connect() as db:
                        if change != 'source':
                            db.execute('INSERT INTO apple_operations VALUES (?,?,?,?,?,?,?,?)', ('delete', 'item', 'old-id', 'delete', '{}', change, '', '2026-11-08'))
                    if change == 'source':
                        if kind == 'calendar':
                            owner.upsert_external_event('apple_calendar', 'eventkit:old', expected['payload'])
                        else:
                            other = owner.create_todo({**expected['payload'], 'remind_at': None})
                            with owner._connect() as db:
                                db.execute("UPDATE todos SET source_type='apple_reminder',source_key=? WHERE id=?", ('eventkit:old', other['id']))
                    before = database(owner)
                    with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
                        owner.apple_restore_item('9' * 64, expected)
                    with self.assertRaisesRegex(ValueError, 'INBOUND_CHANGED'):
                        owner.apple_capture_item_restore('old-id', expected['payload'], expected['category_id'], expected['native'], expected['source_key'])
                    self.assertEqual(database(owner), before)

    def test_complex_reminder_retains_native_rules_read_only(self):
        with case('reminders') as (owner, expected):
            native = {**expected['native'], 'recurrence': {'frequency': 'monthly', 'count': 5, 'month_days': [7]}}
            expected = owner.apple_capture_item_restore('old-id', reminder_payload(native), expected['category_id'], native, expected['source_key'])
            result = owner.apple_restore_item('a' * 64, expected)
            self.assertTrue(result['local']['apple']['issues'])
            self.assertFalse(result['local']['apple']['can_modify'])
            self.assertEqual(result['local']['native_recurrence'], native['recurrence'])

    def test_worker_methods_remain_private_in_both_domains(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), case(kind) as (owner, expected):
                domain = 'calendar' if kind == 'calendar' else 'tasks'
                worker = Business.__new__(Business)
                worker.entries = {domain: importlib.import_module('business.' + domain + '.entry')}
                setattr(worker, domain, owner)
                methods = ('apple_capture_item_restore', 'apple_restore_item', 'apple_verify_item_restore')
                for method in methods:
                    with self.assertRaisesRegex(ValueError, 'Unknown business operation'):
                        worker.invoke(domain, method, {'confirmed': True})
                    with self.assertRaisesRegex(ValueError, 'Unknown Apple peer operation'):
                        worker.apple_peer(domain, method, args=[])
                captured = worker.item_restore_peer(domain, methods[0], args=['old-id', expected['payload'], expected['category_id'], expected['native'], expected['source_key']])
                self.assertEqual(captured, expected)
                result = worker.item_restore_peer(domain, methods[1], args=['b' * 64, expected])
                replay = worker.item_restore_peer(domain, methods[2], args=['b' * 64, expected])
                self.assertEqual(result['local'], replay['local'])

    def test_old_signature_or_boolean_does_not_restore(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), case(kind) as (owner, expected):
                before = database(owner)
                with self.assertRaises(TypeError):
                    owner.apple_restore_item('old-id', expected['payload'], expected['category_id'], expected['source_key'])
                with self.assertRaises(ValueError):
                    owner.apple_restore_item(True, expected)
                with self.assertRaises(ValueError):
                    owner.apple_restore_item('8' * 64, True)
                self.assertEqual(database(owner), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
