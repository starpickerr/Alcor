"""First trusted category absence and owner CAS, in real synthetic manager flows."""
import copy
from contextlib import contextmanager
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = runpy.run_path(str(ROOT/'product/tests/apple-contracts.py'))


class CategoryAbsence(unittest.TestCase):
    @contextmanager
    def case(self, kind):
        fixture = FIXTURE['Contracts']()
        fixture.setUp()
        try:
            bridge = FIXTURE['NativeBridge'](kind)
            sync = fixture.calendar(bridge) if kind == 'calendar' else fixture.reminder(bridge)
            service = fixture.business.calendar if kind == 'calendar' else fixture.business.tasks
            clock = ['2026-11-07T00:00:00Z']
            sync.clock = lambda: clock[0]
            sync.start_sync()
            self.assertEqual(sync.sync()['status'], 'success')
            link = next(row for row in sync._stored_links() if row['apple_id'] == 'a')
            yield sync, service, bridge, clock, link
        finally:
            fixture.tearDown()

    def missing(self, sync, bridge, clock):
        bridge.sources = []
        bridge.rows = []
        self.assertNotEqual(sync.sync()['status'], 'success')
        self.assertEqual(sync._get('category-absence:a')['phase'], 'observed')
        clock[0] = '2026-11-07T00:02:00Z'

    def create(self, service, kind, category):
        if kind == 'calendar':
            return service.create_event({'title': 'new local member', 'category_id': category,
                                         'start_at': '2026-11-07T01:00:00Z', 'end_at': '2026-11-07T02:00:00Z'})
        return service.create_todo({'title': 'new local member', 'category_id': category})

    def test_unchanged_trustworthy_absence_still_archives_automatically(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), self.case(kind) as (sync, service, bridge, clock, link):
                self.missing(sync, bridge, clock)
                result = sync.sync()
                self.assertEqual(result.get('deleted_categories'), 1, result)
                with self.assertRaises(KeyError):
                    service.get_category(link['alcor_id'])
                self.assertEqual(sync._get('category-absence:a')['phase'], 'done')
                self.assertEqual(len(service.list_apple_inbound_recoveries()), 1)
                self.assertFalse(any(row['apple_id'] == 'a' for row in sync._stored_links()))

    def test_rename_or_recolor_while_waiting_preserves_first_snapshot(self):
        for kind in ('calendar', 'reminders'):
            for edit in ({'name': 'later rename'}, {'color': '#112233'}):
                with self.subTest(kind=kind, edit=edit), self.case(kind) as (sync, service, bridge, clock, link):
                    self.missing(sync, bridge, clock)
                    expected = copy.deepcopy(sync._get('category-absence:a')['expected'])
                    service.update_category(link['alcor_id'], edit)
                    result = sync.sync()
                    self.assertEqual(result.get('deleted_categories'), 0, result)
                    self.assertTrue(service.get_category(link['alcor_id']))
                    self.assertEqual(service.list_apple_inbound_recoveries(), [])
                    self.assertEqual(sync._get('category-absence:a')['expected'], expected)

    def test_new_member_during_wait_is_not_captured_as_safe_new_baseline(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), self.case(kind) as (sync, service, bridge, clock, link):
                self.missing(sync, bridge, clock)
                expected = copy.deepcopy(sync._get('category-absence:a')['expected'])
                created = self.create(service, kind, link['alcor_id'])
                result = sync.sync()
                self.assertEqual(result.get('deleted_categories'), 0)
                read = service.get_event if kind == 'calendar' else service.get_todo
                self.assertEqual(read(created['id'])['title'], 'new local member')
                self.assertEqual(sync._get('category-absence:a')['expected'], expected)
                self.assertEqual(service.list_apple_inbound_recoveries(), [])

    def test_member_created_at_owner_call_is_not_deleted_and_not_retried(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), self.case(kind) as (sync, service, bridge, clock, link):
                self.missing(sync, bridge, clock)
                original = service.apple_remove_category_if_unchanged
                created = []
                def raced(expected):
                    created.append(self.create(service, kind, link['alcor_id']))
                    return original(expected)
                with patch.object(service, 'apple_remove_category_if_unchanged', side_effect=raced):
                    result = sync.sync()
                    sync.sync()
                self.assertEqual(result.get('deleted_categories'), 0)
                self.assertEqual(len(created), 1)
                self.assertEqual(service.list_apple_inbound_recoveries(), [])
                self.assertEqual(sync._get('category-absence:a')['phase'], 'uncertain')
                read = service.get_event if kind == 'calendar' else service.get_todo
                self.assertTrue(read(created[0]['id']))

    def test_changes_after_missing_inventory_return_are_not_a_new_safe_baseline(self):
        for kind in ('calendar', 'reminders'):
            for edit in ('rename', 'create'):
                with self.subTest(kind=kind, edit=edit), self.case(kind) as (sync, service, bridge, clock, link):
                    bridge.sources = []
                    bridge.rows = []
                    original = bridge.call
                    changed = []
                    def interleaved(operation, **payload):
                        result = original(operation, **payload)
                        if operation in ('calendars', 'reminder_lists') and not changed:
                            changed.append(True)
                            if edit == 'rename':
                                service.update_category(link['alcor_id'], {'name': 'changed at first missing read'})
                            else:
                                self.create(service, kind, link['alcor_id'])
                        return result
                    with patch.object(bridge, 'call', side_effect=interleaved):
                        sync.sync()
                    first = copy.deepcopy(sync._get('category-absence:a'))
                    if first:
                        self.assertNotEqual(first['expected']['local']['name'], 'changed at first missing read')
                    clock[0] = '2026-11-07T00:02:00Z'
                    result = sync.sync()
                    self.assertEqual(result.get('deleted_categories'), 0, result)
                    self.assertTrue(service.get_category(link['alcor_id']))
                    self.assertEqual(service.list_apple_inbound_recoveries(), [])
                    if first:
                        self.assertEqual(sync._get('category-absence:a')['expected'], first['expected'])

    def test_old_missing_marker_without_snapshot_is_never_resampled(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), self.case(kind) as (sync, service, bridge, clock, link):
                bridge.sources = []
                bridge.rows = []
                sync._set('category-missing:a', '2026-11-06T00:00:00Z')
                self.assertEqual(sync.sync().get('deleted_categories'), 0)
                self.assertIsNone(sync._get('category-absence:a'))
                self.assertTrue(service.get_category(link['alcor_id']))
                with self.assertRaisesRegex(ValueError, '首次可信快照'):
                    sync.resolve_category_conflict('a', 'keep_apple')

    def test_unavailable_account_never_starts_a_trusted_absence_snapshot(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), self.case(kind) as (sync, service, bridge, clock, link):
                bridge.sources = []
                bridge.rows = []
                original = bridge.call
                def unavailable(operation, **payload):
                    result = original(operation, **payload)
                    if operation in ('calendars', 'reminder_lists'):
                        result['source_ids'] = []
                    return result
                with patch.object(bridge, 'call', side_effect=unavailable):
                    sync.sync()
                    clock[0] = '2026-11-08T00:02:00Z'
                    self.assertEqual(sync.sync().get('deleted_categories'), 0)
                self.assertIsNone(sync._get('category-absence:a'))
                self.assertTrue(service.get_category(link['alcor_id']))

    def test_restart_reuses_original_snapshot_and_detects_later_local_edit(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), self.case(kind) as (sync, service, bridge, clock, link):
                self.missing(sync, bridge, clock)
                expected = copy.deepcopy(sync._get('category-absence:a')['expected'])
                reopened = type(sync)(bridge=bridge, db_path=sync.db_path, clock=lambda: clock[0],
                                      **({'calendar_port': service} if kind == 'calendar' else {'todo_port': service}))
                reopened.start_auto = lambda: None
                service.update_category(link['alcor_id'], {'name': 'changed after restart'})
                self.assertEqual(reopened.sync().get('deleted_categories'), 0)
                self.assertEqual(reopened._get('category-absence:a')['expected'], expected)
                self.assertTrue(service.get_category(link['alcor_id']))

    def test_explicit_verified_category_deletion_can_restore_as_local_only(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), self.case(kind) as (sync, service, bridge, clock, link):
                sync._set('sync_deletions', False)
                self.missing(sync, bridge, clock)
                self.assertEqual(sync.sync().get('deleted_categories'), 0)
                sync.resolve_category_conflict('a', 'keep_apple')
                recovery = service.list_apple_inbound_recoveries()[0]
                restored = service.restore_apple_inbound_recovery(recovery['id'])
                self.assertEqual(restored['sync'], 'local_only')
                self.assertTrue(restored['ids'])
                bridge.calls.clear()
                sync.sync()
                self.assertFalse(any(op in ('upsert', 'reminder_upsert', 'create_calendar', 'reminder_list_create') for op, _ in bridge.calls))
                for category_id in restored['categoryIds']:
                    self.assertTrue(service.get_category(category_id))
                read = service.get_event if kind == 'calendar' else service.get_todo
                for item_id in restored['ids']:
                    row = read(item_id)
                    self.assertEqual(row['source_type'], 'apple_recovered')
                    if kind == 'reminders':
                        self.assertIsNone(row['remind_at'])
                        with service._connect() as db:
                            self.assertEqual(db.execute("SELECT COUNT(*) FROM reminder_occurrences WHERE todo_id=? AND state!='cancelled'", (item_id,)).fetchone()[0], 0)
                self.assertEqual(len(service.list_apple_inbound_recoveries()), 1)

    def test_source_returning_before_final_check_prevents_deletion(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), self.case(kind) as (sync, service, bridge, clock, link):
                source = copy.deepcopy(bridge.sources)
                rows = copy.deepcopy(bridge.rows)
                self.missing(sync, bridge, clock)
                original = bridge.call
                reads = []
                def returned(operation, **payload):
                    if operation in ('calendars', 'reminder_lists'):
                        reads.append(True)
                        if len(reads) == 2:
                            bridge.sources = source
                            bridge.rows = rows
                    return original(operation, **payload)
                with patch.object(bridge, 'call', side_effect=returned):
                    result = sync.sync()
                self.assertEqual(result.get('deleted_categories'), 0)
                self.assertTrue(service.get_category(link['alcor_id']))
                self.assertEqual(service.list_apple_inbound_recoveries(), [])

    def test_manager_change_after_owner_commit_preserves_recovery_and_stops_retry(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), self.case(kind) as (sync, service, bridge, clock, link):
                self.missing(sync, bridge, clock)
                original = service.apple_remove_category_if_unchanged
                calls = []
                def interrupted(expected):
                    result = original(expected)
                    calls.append(True)
                    with sync._connect() as db:
                        db.execute("UPDATE mappings SET conflict='new manager evidence'")
                    return result
                with patch.object(service, 'apple_remove_category_if_unchanged', side_effect=interrupted):
                    result = sync.sync()
                    sync.sync()
                self.assertNotEqual(result['status'], 'success')
                self.assertEqual(len(calls), 1)
                self.assertEqual(sync._get('category-absence:a')['phase'], 'uncertain')
                self.assertTrue(sync._get('category-absence:a')['owner_result']['recovery_id'])
                self.assertEqual(len(service.list_apple_inbound_recoveries()), 1)
                self.assertEqual(sync._maps()[0]['conflict'], 'new manager evidence')


if __name__ == '__main__':
    unittest.main(verbosity=2)
