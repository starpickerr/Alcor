import copy
from datetime import datetime, timezone
import json
import sys
import tempfile
import unittest
import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/alcor-secretary'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/alcor-apple-sync/python'))

from business.worker import Business
from apple_sync.calendar import AppleCalendarSync
from apple_sync.reminders import AppleReminderSync
from worker import AppleWorker
from apple_sync.category_links import bidirectional_links
from business.bills.domain.export import build_ledger_xlsx


class Bridge:
    def __init__(self, kind):
        self.kind = kind
        self.sources = [{"id": "a", "title": "Synthetic", "source": "test", "source_id": "synthetic-source", "writable": True}]
        self.rows = [dict(source_id='synthetic-source', calendar_id='a', event_id='event', external_id='external', occurrence_date='2026-11-05T01:00:00Z',
                          alcor_id='', title='Remote event', description='', location='',
                          start_at='2026-11-05T01:00:00Z', end_at='2026-11-05T02:00:00Z',
                          all_day=False, timezone='Asia/Shanghai', recurrence={'frequency': 'none'},
                          is_detached=False, last_modified='2026-11-04T00:00:00Z', writable=True)] if kind == 'calendar' else [
            dict(source_id='synthetic-source', list_id='a', reminder_id='reminder', external_id='external', alcor_id='', title='Remote task',
                 notes='', due_on='2026-11-05', due_time=None, timezone='Asia/Shanghai', planned_on=None,
                 remind_at=None, alarm_count=0, priority=0, completed=False, completed_at=None,
                 recurrence={'frequency': 'none'}, last_modified='2026-11-04T00:00:00Z', writable=True)]

    def call(self, op, **payload):
        if op in ('status', 'reminder_status'):
            return {'supported': True, 'authorization': 'full_access', 'category_write_version': 1, 'category_create_version': 1}
        if op in ('calendars', 'reminder_lists'):
            return {'calendars' if op == 'calendars' else 'lists': copy.deepcopy(self.sources), 'source_ids': sorted({row['source_id'] for row in self.sources}), 'default_id': self.sources[0]['id'] if self.sources else None, 'default_source_id': self.sources[0]['source_id'] if self.sources else None}
        if op in ('create_calendar', 'reminder_list_create'):
            row = {'id': 'created-' + payload['title'], 'title': payload['title'], 'color': payload['color'], 'writable': True, 'source_id': 'synthetic-source'}
            self.sources.append(row)
            if payload.get('receipt_binding'):
                Path(payload['receipt_file']).write_text(json.dumps({'binding':payload['receipt_binding'],'saved':row}))
            return {'calendar' if op == 'create_calendar' else 'list': copy.deepcopy(row)}
        if op in ('rename', 'reminder_rename'):
            from apple_sync.bridge import BridgeError
            identity = payload['calendar_id' if op == 'rename' else 'list_id']
            row = next(row for row in self.sources if row['id'] == identity)
            if (payload.get('category_write_version') != 1 or payload.get('expected_source_id') != row['source_id']
                    or payload.get('expected_snapshot') != row):
                raise BridgeError('Synthetic category changed before save',native_no_side_effects=True)
            row.update({key: payload[key] for key in ('title', 'color') if key in payload})
            proof={'version':1,'binding':payload['receipt_binding'],'op':op,'target_id':identity,
                   'source_id':row['source_id'],'expected_snapshot':copy.deepcopy(payload['expected_snapshot']),
                   'title':payload['title'],'color':payload.get('color'),'result':copy.deepcopy(row)}
            Path(payload['receipt_file']).write_text(json.dumps({'state':'saved','category_receipt':proof}))
            return {'calendar' if op == 'rename' else 'list': copy.deepcopy(row),'category_receipt':proof}
        if op in ('delete_calendar', 'reminder_list_delete'):
            identity = payload['calendar_id' if op == 'delete_calendar' else 'list_id']
            self.sources = [row for row in self.sources if row['id'] != identity]
            return {'deleted': True}
        if op == 'events':
            return {'events': copy.deepcopy([row for row in self.rows if row['calendar_id'] in payload['calendar_ids']]), 'successful_calendar_ids': [row['id'] for row in self.sources], 'failed_calendar_ids': []}
        if op == 'reminders':
            return {'reminders': copy.deepcopy([row for row in self.rows if row['list_id'] in payload['list_ids']]), 'successful_list_ids': [row['id'] for row in self.sources], 'failed_list_ids': []}
        if op in ('lookup', 'reminder_lookup'):
            return {'event' if op == 'lookup' else 'reminder': copy.deepcopy(self.rows[0])}
        raise AssertionError(f'Unexpected system write: {op}')


class Recovery(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='stella-business-recovery-')
        self.root = Path(self.temporary.name)
        self.business = Business(self.root)

    def tearDown(self):
        self.business.meta.close()
        self.temporary.cleanup()

    def test_unavailable_apple_account_does_not_delete_categories(self):
        class AccountBridge(Bridge):
            def __init__(self, kind):
                super().__init__(kind)
                for row in self.sources + self.rows:
                    row['source_id'] = 'account'
            account_online = True
            def call(self, op, **payload):
                if op in ('calendars', 'reminder_lists'):
                    return {'calendars' if op == 'calendars' else 'lists':
                        [{**row, 'source_id': 'account'} for row in self.sources] if self.account_online else [],
                        'source_ids': ['account'] if self.account_online else []}
                return super().call(op, **payload)
        for kind, cls, service, port in (
            ('calendar', AppleCalendarSync, self.business.calendar, 'calendar_port'),
            ('reminders', AppleReminderSync, self.business.tasks, 'todo_port'),
        ):
            with self.subTest(kind=kind):
                bridge = AccountBridge(kind)
                sync = cls(bridge=bridge, db_path=self.root / f'account-{kind}.sqlite3', **{port: service})
                sync.start_auto = lambda: None
                sync.start_sync()
                before = service.categories()
                bridge.account_online = False
                self.assertIn(sync.sync()['status'], ('error','partial'))
                self.assertEqual(service.categories(), before)
                self.assertIn(sync.sync()['status'], ('error','partial'))
                self.assertEqual(service.categories(), before)
                bridge.account_online = True
                self.assertEqual(sync.sync()['status'], 'success')

    def test_reassigning_category_moves_existing_apple_record_without_duplication(self):
        class MovingBridge(Bridge):
            def call(self, op, **payload):
                if op in ('upsert', 'reminder_upsert'):
                    self.rows[0].update({key: value for key, value in payload.items() if not key.startswith('lookup_')})
                    return {'event' if op == 'upsert' else 'reminder': copy.deepcopy(self.rows[0])}
                return super().call(op, **payload)
        for kind, cls, service, port in (
            ('calendar', AppleCalendarSync, self.business.calendar, 'calendar_port'),
            ('reminders', AppleReminderSync, self.business.tasks, 'todo_port'),
        ):
            with self.subTest(kind=kind):
                bridge = MovingBridge(kind)
                sync = cls(bridge=bridge, db_path=self.root / f'move-{kind}.sqlite3', **{port: service})
                sync.start_auto = lambda: None
                sync.start_sync()
                target = service.create_category({'name': 'Target'})
                sync.sync()
                original = sync._maps()[0]
                local_id = original['todo_id' if kind == 'reminders' else 'alcor_id']
                if kind == 'calendar':
                    service.set_event_category(local_id, target['id'])
                else:
                    service.set_todo_category(local_id, target['id'])
                result = sync.sync()
                self.assertEqual(result['status'], 'success')
                self.assertEqual(result['exported'], 1)
                self.assertEqual(len(sync._maps()), 1)
                self.assertEqual(len(bridge.rows), 1)
                group_key = 'list_id' if kind == 'reminders' else 'calendar_id'
                expected = next(row['apple_id'] for row in sync._stored_links() if row['alcor_id'] == target['id'])
                self.assertEqual(bridge.rows[0][group_key], expected)
                read = service.get_todo if kind == 'reminders' else service.get_event
                self.assertEqual(read(local_id)['category_id'], target['id'])

    def test_categories_converge_in_both_directions_and_survive_restart(self):
        for kind, cls, service, port in (
            ('calendar', AppleCalendarSync, self.business.calendar, 'calendar_port'),
            ('reminders', AppleReminderSync, self.business.tasks, 'todo_port'),
        ):
            with self.subTest(kind=kind):
                bridge = Bridge(kind)
                now = ['2026-11-07T00:00:00Z']
                path = self.root / f'category-parity-{kind}.sqlite3'
                sync = cls(bridge=bridge, db_path=path, clock=lambda: now[0], **{port: service})
                sync.start_auto = lambda: None
                sync.start_sync()
                local = service.create_category({'name': 'planA', 'color': '#112233'})
                self.assertEqual(sync.sync()['status'], 'success')
                linked = next(row for row in sync._stored_links() if row['alcor_id'] == local['id'])
                apple = next(row for row in bridge.sources if row['id'] == linked['apple_id'])
                self.assertEqual(apple['title'], 'planA')
                self.assertEqual(apple['color'], '#112233')
                sync = cls(bridge=bridge, db_path=path, clock=lambda: now[0], **{port: service})
                sync.start_auto = lambda: None
                service.update_category(local['id'], {'name': 'Local rename', 'color': '#334455'})
                self.assertEqual(sync.sync()['status'], 'success')
                self.assertEqual(apple['title'], 'Local rename')
                self.assertEqual(apple['color'], '#334455')
                apple.update(title='Apple rename', color='#556677')
                self.assertEqual(sync.sync()['status'], 'success')
                self.assertEqual(service.get_category(local['id'])['name'], 'Apple rename')
                self.assertEqual(service.get_category(local['id'])['color'], '#556677')
                bridge.sources = [row for row in bridge.sources if row['id'] != apple['id']]
                sync.sync()
                self.assertEqual(service.get_category(local['id'])['name'], 'Apple rename')
                now[0] = '2026-11-07T00:02:00Z'
                result = sync.sync()
                self.assertEqual(result['status'], 'success')
                self.assertEqual(result['deleted_categories'], 1)
                with self.assertRaises(KeyError):
                    service.get_category(local['id'])
                archives = [row for row in service.list_apple_inbound_recoveries()
                            if row['entity'] == 'category' and row['target_id'] == local['id']]
                self.assertEqual(len(archives), 1)
                service.create_category({'name': 'Delete locally'})
                sync.sync()
                local = next(row for row in service.categories() if row['name'] == 'Delete locally')
                with self.assertRaisesRegex(PermissionError, '人工确认'):
                    service.delete_category(local['id'])
                self.assertEqual(sync.sync()['deleted_remote'], 0)
                self.assertTrue(any(row['title'] == 'Delete locally' for row in bridge.sources))
                self.assertEqual({row['alcor_id'] for row in sync._stored_links()},
                                 {row['id'] for row in service.categories()})
                self.assertEqual(len(bridge.sources), len(sync._stored_links()))

    def test_legacy_category_backfill_preserves_identity_and_does_not_restore_deleted_lists(self):
        from business.tasks.domain.service import TodoService
        tasks = self.business.tasks
        row = tasks.create_todo({'title': 'Old task', 'list_name': 'Legacy plan', 'due_on': '2026-11-07', 'due_time': '04:00', 'timezone': 'Asia/Shanghai'})
        with tasks._connect() as db:
            db.execute('DROP TRIGGER todos_required_category_update')
            db.execute('UPDATE todos SET category_id=NULL WHERE id=?', (row['id'],))
            db.execute("DELETE FROM todo_migrations WHERE name='stable-task-categories'")
        reopened = TodoService(tasks.db_path)
        migrated = reopened.get_todo(row['id'])
        self.assertEqual(migrated['category_id'], row['category_id'])
        self.assertEqual(migrated['due_time'], '04:00')
        self.assertEqual(migrated['timezone'], 'Asia/Shanghai')
        op = 'a' * 64
        plan = reopened.prepare_local_category_delete(row['category_id'], operation_id=op)
        reopened.commit_local_category_delete(row['category_id'], snapshot=plan['snapshot'], operation_id=op)
        again = TodoService(tasks.db_path)
        with self.assertRaises(KeyError):
            again.get_todo(row['id'])
        self.assertFalse(any(item['name'] == 'Legacy plan' for item in again.categories()))

    def test_full_sync_exports_unclassified_and_briefing_records(self):
        class ExportBridge(Bridge):
            def call(self, op, **payload):
                if op in ('upsert', 'reminder_upsert'):
                    row = copy.deepcopy(self.rows[0])
                    row.update(payload)
                    row['event_id' if op == 'upsert' else 'reminder_id'] = payload['alcor_id']
                    row['external_id'] = payload['alcor_id']
                    row['recurrence'] = {'frequency': payload['repeat_frequency']}
                    self.rows.append(row)
                    if payload.get('receipt_binding'):
                        row['source_id'] = payload['expected_source_id']
                        Path(payload['receipt_file']).write_text(json.dumps({'binding':payload['receipt_binding'], 'saved':row}))
                    return {'event' if op == 'upsert' else 'reminder': row}
                return super().call(op, **payload)
        for kind, cls, service, port in (
            ('calendar', AppleCalendarSync, self.business.calendar, 'calendar_port'),
            ('reminders', AppleReminderSync, self.business.tasks, 'todo_port'),
        ):
            with self.subTest(kind=kind):
                payload = {'title': 'Briefing item'}
                if kind == 'calendar':
                    payload.update(start_at='2026-11-07T01:00:00Z', end_at='2026-11-07T02:00:00Z')
                    first = service.create_event(payload, _briefing_key='briefing-synthetic')
                    second = service.create_event({**payload, 'title': 'Unclassified item'})
                else:
                    first = service.create_todo(payload, _briefing_key='briefing-synthetic')
                    second = service.create_todo({**payload, 'title': 'Unclassified item'})
                bridge = ExportBridge(kind)
                sync = cls(bridge=bridge, db_path=self.root / f'export-all-{kind}.sqlite3', **{port: service})
                sync.start_auto = lambda: None
                result = sync.start_sync()['result']
                self.assertEqual(result['status'], 'success')
                self.assertEqual(result['exported'], 2)
                self.assertEqual({row['alcor_id'] for row in bridge.rows if row['alcor_id']}, {first['id'], second['id']})

    def test_task_categories_are_stable_across_edits_legacy_names_and_deletion(self):
        tasks = self.business.tasks
        group = tasks.create_category({'name': 'planA'})
        row = tasks.create_todo({'title': 'Categorized task', 'category_id': group['id'], 'tags': ['keep']})
        self.assertEqual(row['category_id'], group['id'])
        self.assertEqual(row['list_name'], 'planA')
        row = tasks.update_todo(row['id'], {'notes': 'Edited elsewhere'})
        self.assertEqual(row['category_id'], group['id'])
        self.assertEqual(json.loads(row['tags']), ['keep'])
        legacy = tasks.create_todo({'title': 'Legacy name', 'list_name': 'planB'})
        self.assertEqual(tasks.get_category(legacy['category_id'])['name'], 'planB')
        tasks.update_category(group['id'], {'name': 'Renamed'})
        self.assertEqual(tasks.get_todo(row['id'])['list_name'], 'Renamed')
        with self.assertRaises(KeyError):
            tasks.update_todo(row['id'], {'category_id': 'missing'})
        op = 'a' * 64
        plan = tasks.prepare_local_category_delete(group['id'], operation_id=op)
        tasks.commit_local_category_delete(group['id'], snapshot=plan['snapshot'], operation_id=op)
        with self.assertRaises(KeyError):
            tasks.get_todo(row['id'])

    def test_one_click_bidirectional_sync_preserves_categories_lists_and_exports_existing_items(self):
        class WritableBridge(Bridge):
            title = 'Synthetic'
            writable = True

            def call(self, op, **payload):
                if op in ('calendars', 'reminder_lists'):
                    return {'calendars' if op == 'calendars' else 'lists': [{'id': 'a', 'title': self.title, 'writable': self.writable, 'source_id': 'synthetic-source'}], 'source_ids': ['synthetic-source']}
                if op in ('upsert', 'reminder_upsert'):
                    row = {**copy.deepcopy(self.rows[0]), **payload}
                    identity = 'event_id' if op == 'upsert' else 'reminder_id'
                    row[identity] = payload.get(identity) or payload['alcor_id']
                    row['external_id'] = payload.get('external_id') or row[identity]
                    row['recurrence'] = {'frequency': payload['repeat_frequency']}
                    previous = next((item for item in self.rows if item[identity] == row[identity]), None)
                    if previous:
                        previous.update(row)
                    else:
                        self.rows.append(row)
                    if payload.get('receipt_binding'):
                        row['source_id'] = payload['expected_source_id']
                        Path(payload['receipt_file']).write_text(json.dumps({'binding':payload['receipt_binding'], 'saved':row}))
                    return {'event' if op == 'upsert' else 'reminder': row}
                return super().call(op, **payload)

        for kind, cls, service, port in (
            ('calendar', AppleCalendarSync, self.business.calendar, 'calendar_port'),
            ('reminders', AppleReminderSync, self.business.tasks, 'todo_port'),
        ):
            with self.subTest(kind=kind):
                category = service.create_category({'name': 'Synthetic'})
                if kind == 'calendar':
                    local = service.create_event({'title': 'Existing local event', 'category_id': category['id'],
                        'start_at': '2026-11-06T01:00:00Z', 'end_at': '2026-11-06T02:00:00Z'})
                    other = service.create_event({'title': 'Unselected event', 'start_at': '2026-11-07T01:00:00Z', 'end_at': '2026-11-07T02:00:00Z'})
                else:
                    local = service.create_todo({'title': 'Existing local task', 'list_name': 'Synthetic'})
                    other = service.create_todo({'title': 'Unselected task'})
                bridge = WritableBridge(kind)
                path = self.root / f'bidirectional-{kind}.sqlite3'
                sync = cls(bridge=bridge, db_path=path, **{port: service})
                starts = []
                sync.start_auto = lambda: starts.append(True)
                sync.configure(**{'calendars' if kind == 'calendar' else 'list_ids': ['a'], 'direction': 'import'})
                bridge.writable = False
                with self.assertRaisesRegex(ValueError, '可写'):
                    sync.start_sync([{'apple_id': 'a'}])
                bridge.writable = True
                with self.assertRaisesRegex(ValueError, '重复'):
                    sync.start_sync([{'apple_id': 'a'}, {'apple_id': 'a'}])
                result = sync.start_sync([{'apple_id': 'a', 'alcor_id': category['id']}])
                self.assertEqual(result['result']['status'], 'success')
                self.assertEqual(result['result']['imported'], 1)
                self.assertEqual(result['result']['exported'], 1)
                self.assertEqual(result['state']['direction'], 'bidirectional')
                self.assertTrue(result['state']['auto_enabled'])
                self.assertEqual(len(starts), 2)
                self.assertEqual(len(bridge.rows), 2)
                self.assertFalse(any(row['title'] == other['title'] for row in bridge.rows))
                reopened = cls(bridge=bridge, db_path=path, **{port: service})
                reopened.start_auto = lambda: None
                self.assertEqual(reopened.status()['links'][0]['alcor_id'], category['id'])
                self.assertEqual(reopened.start_sync([{'apple_id': 'a', 'alcor_id': category['id']}])['result']['exported'], 0)
                bridge.title = 'Renamed Apple source'
                self.assertEqual(reopened.start_sync([{'apple_id': 'a', 'alcor_id': category['id']}])['result']['status'], 'success')
                self.assertEqual(service.get_category(category['id'])['name'], bridge.title)
                if kind == 'reminders':
                    self.assertEqual(service.get_todo(local['id'])['list_name'], bridge.title)
                    service.create_todo({'title': 'New task from page', 'list_name': bridge.title})
                    self.assertEqual(reopened.sync()['exported'], 1)
                    self.assertEqual(len(bridge.rows), 3)
                reopened.disconnect()
                self.assertEqual(len(reopened.status()['links']), 1)

    def test_unconfirmed_category_delete_preserves_apple_calendar(self):
        bridge = Bridge('calendar')
        sync = AppleCalendarSync(bridge=bridge, db_path=self.root/'delete-category.sqlite3',
                                 calendar_port=self.business.calendar)
        sync.start_auto = lambda: None
        started = sync.start_sync([{'apple_id': 'a'}])
        category = started['state']['links'][0]['alcor_id']
        with self.assertRaisesRegex(PermissionError, '人工确认'):
            self.business.calendar.delete_category(category)
        for result in (sync.start_sync()['result'], sync.sync()):
            self.assertEqual(result['deleted_remote'], 0)
        self.assertTrue(self.business.calendar.get_category(category))
        self.assertEqual(len(bridge.rows), 1)

    def test_sync_all_sources_updates_deletes_and_pause(self):
        class AllBridge(Bridge):
            def __init__(self, kind):
                super().__init__(kind)
                self.sources = [{'id': 'a', 'title': '课程' if kind == 'calendar' else 'planA', 'writable': True, 'source_id': 'synthetic-source'},
                                {'id': 'b', 'title': 'Read only', 'writable': False, 'source_id': 'synthetic-source'}]
                extra = copy.deepcopy(self.rows[0])
                extra.update({'calendar_id' if kind == 'calendar' else 'list_id': 'b',
                              'event_id' if kind == 'calendar' else 'reminder_id': 'second',
                              'external_id': 'second', 'writable': False})
                self.rows.append(extra)
                self.template = copy.deepcopy(self.rows[0])
                self.failed = []

            def call(self, op, **payload):
                identity = 'event_id' if self.kind == 'calendar' else 'reminder_id'
                source = 'calendar_id' if self.kind == 'calendar' else 'list_id'
                singular = 'event' if self.kind == 'calendar' else 'reminder'
                if op in ('calendars', 'reminder_lists'):
                    return {'calendars' if op == 'calendars' else 'lists': copy.deepcopy(self.sources), 'source_ids': sorted({row['source_id'] for row in self.sources}), 'default_id': self.sources[0]['id'] if self.sources else None, 'default_source_id': self.sources[0]['source_id'] if self.sources else None}
                if op in ('events', 'reminders'):
                    requested = payload['calendar_ids' if op == 'events' else 'list_ids']
                    return {singular + 's': copy.deepcopy([row for row in self.rows if row[source] in requested and row[source] not in self.failed]),
                            'successful_calendar_ids' if op == 'events' else 'successful_list_ids':
                                [s['id'] for s in self.sources if s['id'] in requested and s['id'] not in self.failed],
                            'failed_calendar_ids' if op == 'events' else 'failed_list_ids': [key for key in self.failed if key in requested]}
                if op in ('lookup', 'reminder_lookup'):
                    return {singular: next((copy.deepcopy(r) for r in self.rows if r[identity] == payload[identity]), None)}
                if op in ('upsert', 'reminder_upsert'):
                    row = next((r for r in self.rows if r[identity] == payload.get(identity)), None)
                    if row is None:
                        row = {**copy.deepcopy(self.template), identity: payload['alcor_id'], 'external_id': payload['alcor_id']}
                        self.rows.append(row)
                    row.update(payload)
                    if payload.get('receipt_binding'):
                        row['source_id'] = payload['expected_source_id']
                        Path(payload['receipt_file']).write_text(json.dumps({'binding':payload['receipt_binding'], 'saved':row}))
                    return {singular: copy.deepcopy(row)}
                if op in ('delete', 'reminder_delete'):
                    self.rows = [r for r in self.rows if r[identity] != payload[identity]]
                    return {'deleted': True}
                return super().call(op, **payload)

        for kind, cls, service, port in (
            ('calendar', AppleCalendarSync, self.business.calendar, 'calendar_port'),
            ('reminders', AppleReminderSync, self.business.tasks, 'todo_port'),
        ):
            with self.subTest(kind=kind):
                bridge = AllBridge(kind)
                now = ['2026-11-06T00:00:00Z']
                sync = cls(bridge=bridge, db_path=self.root / f'all-{kind}.sqlite3', clock=lambda: now[0], **{port: service})
                sync.start_auto = lambda: None
                if kind == 'calendar':
                    original_call = bridge.call
                    def check_window(op, **payload):
                        if op == 'events':
                            self.assertTrue(payload['start'].startswith('2025-11-06T00:'))
                            self.assertTrue(payload['end'].startswith('2028-11-06T00:'))
                        return original_call(op, **payload)
                    bridge.call = check_window
                result = sync.start_sync()
                self.assertEqual(result['result']['imported'], 2)
                self.assertTrue(result['state']['sync_all'])
                self.assertEqual({r['direction'] for r in result['state']['links']}, {'import', 'bidirectional'})
                link = next(r for r in result['state']['links'] if r['apple_id'] == 'a')
                self.assertEqual(link['alcor_name'], bridge.sources[0]['title'])
                read = service.get_event if kind == 'calendar' else service.get_todo
                update = service.update_event if kind == 'calendar' else service.update_todo
                delete = service.delete_event if kind == 'calendar' else service.delete_todo
                key = 'alcor_id' if kind == 'calendar' else 'todo_id'
                mapping = next(r for r in sync._maps() if r['calendar_id' if kind == 'calendar' else 'list_id'] == 'a')
                bridge.rows[0]['title'] = 'Apple edit'
                self.assertEqual(sync.sync()['updated'], 1)
                self.assertEqual(read(mapping[key])['title'], 'Apple edit')
                update(mapping[key], {'title': 'Stella edit'})
                self.assertEqual(sync.sync()['exported'], 1)
                self.assertEqual(bridge.rows[0]['title'], 'Stella edit')
                with self.assertRaisesRegex(PermissionError, '人工确认'): delete(mapping[key])
                self.assertEqual(sync.sync()['deleted_remote'], 0)
                self.assertEqual(len(bridge.rows), 2)
                # A new source joins without another click.
                bridge.sources.append({'id': 'c', 'title': 'New source', 'writable': True, 'source_id': 'synthetic-source'})
                self.assertEqual(sync.sync()['status'], 'success')
                self.assertEqual(len(sync.status()['links']), len(service.categories()))
                if kind == 'calendar':
                    local = service.create_event({'title': 'Local new', 'category_id': link['alcor_id'],
                        'start_at': '2026-11-06T01:00:00Z', 'end_at': '2026-11-06T02:00:00Z'})
                else:
                    local = service.create_todo({'title': 'Local new', 'list_name': link['alcor_name']})
                self.assertEqual(sync.sync()['exported'], 1)
                bridge.rows = [r for r in bridge.rows if r['external_id'] != local['id']]
                bridge.failed = ['a']
                self.assertEqual(sync.sync()['deleted_local'], 0)
                bridge.failed = []
                sync.sync()
                now[0] = '2026-11-06T00:02:00Z'
                result = sync.sync()
                if kind == 'calendar':
                    self.assertEqual(result['deleted_local'], 0)
                    self.assertEqual(result['status'], 'partial')
                    self.assertTrue(read(local['id']))
                    self.assertEqual(next(row for row in sync._maps() if row[key] == local['id'])['state'], 'conflict')
                else:
                    self.assertEqual(result['deleted_local'], 1)
                    with self.assertRaises(KeyError):
                        read(local['id'])
                # Calendar window null remains unknown; reminder full inventory keeps its existing behavior.
                remote = copy.deepcopy(bridge.template)
                remote.update({'event_id' if kind == 'calendar' else 'reminder_id': 'third', 'external_id': 'third'})
                bridge.rows.append(remote)
                self.assertEqual(sync.sync()['imported'], 1)
                imported = next(r for r in sync._maps() if r['external_id'] == 'third')
                bridge.rows = [r for r in bridge.rows if r['external_id'] != 'third']
                sync.sync()
                now[0] = '2026-11-06T00:04:00Z'
                result = sync.sync()
                if kind == 'calendar':
                    self.assertEqual(result['deleted_local'], 0)
                    self.assertTrue(read(imported[key]))
                    self.assertEqual(next(row for row in sync._maps() if row[key] == imported[key])['state'], 'conflict')
                else:
                    self.assertEqual(result['deleted_local'], 1)
                    with self.assertRaises(KeyError):
                        read(imported[key])
                # A remote edit racing with a Stella deletion must survive as a conflict.
                remote = copy.deepcopy(bridge.template)
                remote.update({'event_id' if kind == 'calendar' else 'reminder_id': 'fourth', 'external_id': 'fourth'})
                bridge.rows.append(remote)
                self.assertEqual(sync.sync()['imported'], 1)
                imported = next(r for r in sync._maps() if r['external_id'] == 'fourth')
                with self.assertRaisesRegex(PermissionError, '人工确认'): delete(imported[key])
                bridge.rows[-1]['title'] = 'Changed after local deletion'
                result = sync.sync()
                self.assertEqual(result['deleted_remote'], 0)
                self.assertEqual(read(imported[key])['title'], 'Changed after local deletion')
                self.assertTrue(any(r['external_id'] == 'fourth' for r in bridge.rows))
                sync.disconnect()
                self.assertEqual(sync.sync()['status'], 'disconnected')

    def test_apple_point_event_preserves_time_and_remains_visible_at_midnight(self):
        class PointBridge(Bridge):
            def call(self, op, **payload):
                if op == 'upsert':
                    self.rows[0].update(payload)
                    return {'event': copy.deepcopy(self.rows[0])}
                return super().call(op, **payload)
        bridge = PointBridge('calendar')
        bridge.rows[0].update(start_at='2026-11-05T00:00:00Z', end_at='2026-11-05T00:00:00Z')
        service = self.business.calendar
        sync = AppleCalendarSync(bridge=bridge, db_path=self.root / 'point.sqlite3', calendar_port=service)
        sync.start_auto = lambda: None
        self.assertEqual(sync.start_sync()['result']['imported'], 1)
        rows = service.list_events('2026-11-05T00:00:00Z', '2026-11-06T00:00:00Z')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['start_at'], rows[0]['end_at'])
        self.assertEqual(service.list_events('2026-11-04T00:00:00Z', '2026-11-05T00:00:00Z'), [])
        service.update_event(rows[0]['id'], {'title': 'Updated point'})
        self.assertEqual(sync.sync()['exported'], 1)
        self.assertEqual(bridge.rows[0]['start_at'], bridge.rows[0]['end_at'])
        with self.assertRaises(ValueError):
            service.create_event({'title': 'Reversed', 'start_at': '2026-11-05T01:00:00Z', 'end_at': '2026-11-05T00:00:00Z'})
        with self.assertRaises(ValueError):
            service.create_event({'title': 'Invalid all day', 'all_day': True, 'start_at': '2026-11-05T00:00:00Z', 'end_at': '2026-11-05T00:00:00Z'})

    def test_repeating_task_focus_and_apple_completion_have_one_owner(self):
        service = self.business.tasks
        local = service.create_todo({'title': 'Local daily', 'due_on': '2026-08-19', 'repeat_frequency': 'daily'})
        focused = [r for r in service.list_todos_focus(today='2026-11-06') if r.get('series_id') == local['id']]
        self.assertEqual([r['due_on'] for r in focused], ['2026-11-06'])
        service.complete_todo(focused[0]['id'], True)
        focused = [r for r in service.list_todos_focus(today='2026-11-06') if r.get('series_id') == local['id']]
        self.assertEqual([(r['due_on'], r['status']) for r in focused], [('2026-11-07', 'todo'), ('2026-11-06', 'completed')])

        service.change_todo_status(local['id'], 'cancelled')
        cancelled = next(r for r in service.list_todos_focus(today='2026-11-06') if r['id'] == local['id'])
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertNotIn('series_id', cancelled)

        class RepeatingBridge(Bridge):
            def call(self, op, **payload):
                if op == 'reminder_complete':
                    self.completed = payload['completed']
                    completed = {**copy.deepcopy(self.rows[0]), 'completed':payload['completed'], 'completed_at':'2026-08-19T12:00:00Z'}
                    self.rows[0].update(due_on='2026-08-20', completed=False, completed_at=None)
                    return {'reminder': copy.deepcopy(self.rows[0]), 'completed_snapshot':completed}
                return super().call(op, **payload)
        bridge = RepeatingBridge('reminders')
        bridge.rows[0].update(due_on='2026-08-19', recurrence={'frequency': 'daily'})
        sync = AppleReminderSync(bridge=bridge, db_path=self.root / 'repeat-owner.sqlite3', todo_port=service)
        sync.start_auto = lambda: None
        sync.start_sync()
        apple = next(r for r in service.list_todos_focus(today='2026-11-06') if r['source_type'] == 'apple_reminder')
        self.assertEqual(apple['due_on'], '2026-08-19')
        service.complete_todo(apple['id'], True)
        self.assertEqual(sync.sync()['exported'], 1)
        self.assertTrue(bridge.completed)
        refreshed = service.get_todo(apple['id'])
        self.assertEqual((refreshed['due_on'], refreshed['status']), ('2026-08-20', 'todo'))
        self.assertEqual(sync.sync()['exported'], 0)
        self.assertEqual(len([r for r in service.list_todos() if r['source_type'] == 'apple_reminder']), 1)

    def test_custom_repeat_intervals_survive_edits_and_restart(self):
        service = self.business.tasks
        cases = [('daily', 3, [], ['2026-11-02','2026-11-05','2026-11-08']),
                 ('weekly', 2, [0,2], ['2026-11-02','2026-11-04','2026-11-16','2026-11-18']),
                 ('monthly', 2, [], ['2026-11-02','2027-01-02']),
                 ('yearly', 2, [], ['2026-11-02'])]
        for frequency, interval, days, expected in cases:
            item = service.create_todo({'title': frequency, 'due_on': '2026-11-02', 'repeat_frequency': frequency,
                'repeat_interval': interval, 'repeat_days': days, 'repeat_until': '2027-01-03'})
            self.assertEqual(service.get_todo(item['id'])['repeat_interval'], interval)
            rows = [r['due_on'] for r in service.list_todos('2026-11-02', '2027-01-04') if r.get('series_id') == item['id']]
            if frequency == 'daily':
                self.assertEqual(rows[:3], expected)
            elif frequency == 'weekly':
                self.assertEqual(rows[:4], expected)
            else:
                self.assertEqual(rows, expected)
            service.update_todo(item['id'], {'title': 'Renamed'})
            self.assertEqual(service.get_todo(item['id'])['repeat_interval'], interval)
        for interval in [0, -1, 1.5, True, 100]:
            with self.assertRaises(ValueError):
                service.create_todo({'title': 'Bad interval', 'due_on': '2026-11-02', 'repeat_frequency': 'daily', 'repeat_interval': interval})

    def test_apple_custom_interval_is_imported_and_written_back(self):
        class CustomBridge(Bridge):
            def call(self, op, **payload):
                if op == 'reminder_upsert':
                    self.rows[0].update(payload)
                    self.rows[0]['recurrence'] = {'frequency': payload['repeat_frequency'], 'days': payload['repeat_days'], 'interval': payload['repeat_interval']}
                    return {'reminder': copy.deepcopy(self.rows[0])}
                return super().call(op, **payload)
        bridge = CustomBridge('reminders')
        bridge.rows[0].update(due_on='2026-11-02', recurrence={'frequency': 'weekly', 'days': [0,2], 'interval': 2})
        sync = AppleReminderSync(bridge=bridge, db_path=self.root / 'custom-apple.sqlite3', todo_port=self.business.tasks)
        sync.start_auto = lambda: None
        result = sync.start_sync()
        self.assertEqual(result['result']['imported'], 1)
        item = self.business.tasks.list_todos()[0]
        self.assertEqual(item['repeat_interval'], 2)
        self.business.tasks.update_todo(item['id'], {'repeat_interval': 3})
        self.assertEqual(sync.sync()['exported'], 1)
        self.assertEqual(bridge.rows[0]['recurrence']['interval'], 3)
        self.assertEqual(sync.sync()['exported'], 0)

    def test_sync_category_selection_uses_distinct_names_and_validates_before_creation(self):
        sources = [{'id': 'a', 'title': 'Work', 'writable': True}, {'id': 'b', 'title': 'Work', 'writable': True}]
        categories = [{'id': 'existing', 'name': 'Work'}]
        links = bidirectional_links([{'apple_id': 'a'}, {'apple_id': 'b'}], sources, categories)
        self.assertEqual(links[0]['alcor_id'], 'existing')
        self.assertEqual(links[1]['create_name'], 'Work (2)')
        with self.assertRaisesRegex(ValueError, '有效'):
            bidirectional_links([{'apple_id': 'a', 'alcor_id': 'missing'}], sources, categories)
        with self.assertRaisesRegex(ValueError, '格式'):
            bidirectional_links([{'apple_id': 'a', 'direction': 'import'}], sources, categories)
        with self.assertRaisesRegex(ValueError, '至少'):
            bidirectional_links([], sources, categories)

    def test_apple_import_conflict_disconnect_and_auto_resume(self):
        for kind, cls, service, port, selection in (
            ('calendar', AppleCalendarSync, self.business.calendar, 'calendar_port', 'calendars'),
            ('reminders', AppleReminderSync, self.business.tasks, 'todo_port', 'list_ids'),
        ):
            with self.subTest(kind=kind):
                bridge = Bridge(kind)
                path = self.root / f'fake-{kind}.sqlite3'
                sync = cls(bridge=bridge, db_path=path, clock=lambda: '2026-11-05T00:00:00Z', **{port: service})
                starts = []
                sync.start_auto = lambda: starts.append(True)
                sync.configure(**{selection: ['a'], 'direction': 'import'})
                self.assertEqual(len(starts), 1)
                self.assertEqual(sync.sync()['imported'], 1)
                self.assertEqual(sync.sync()['imported'], 0)
                rows = service.list_events() if kind == 'calendar' else service.list_todos()
                item = rows[0]
                update = service.update_event if kind == 'calendar' else service.update_todo
                update(item['id'], {'title': 'Local correction'})
                bridge.rows[0]['title'] = 'Remote correction'
                self.assertEqual(sync.sync()['conflicts'], 1)
                read = service.get_event if kind == 'calendar' else service.get_todo
                self.assertEqual(read(item['id'])['title'], 'Local correction')
                reopened = cls(bridge=bridge, db_path=path, **{port: service})
                reopened.start_auto = lambda: starts.append(True)
                reopened.resume_auto()
                self.assertEqual(len(starts), 2)
                reopened.disconnect()
                reopened.resume_auto()
                self.assertEqual(len(starts), 2)
                self.assertEqual(read(item['id'])['title'], 'Local correction')

    def test_pending_receipt_is_never_replayed(self):
        arguments = {'payload': {'title': 'Unknown operation'}}
        fingerprint = json.dumps(['tasks', 'create_todo', arguments], sort_keys=True, ensure_ascii=False)
        self.business.meta.execute('INSERT INTO operations VALUES (?,?,NULL)', ('pending-call', fingerprint))
        self.business.meta.commit()
        with self.assertRaisesRegex(ValueError, '结果未知'):
            self.business.invoke('tasks', 'create_todo', arguments, 'pending-call')
        self.assertEqual(self.business.tasks.list_todos(), [])

    def test_independent_apple_mapping_migration_preserves_original_and_shared_records(self):
        source = self.root / 'apple-calendar.sqlite3'
        legacy = AppleCalendarSync(bridge=Bridge('calendar'), db_path=source, calendar_port=self.business.calendar)
        legacy.set_auto(False)
        legacy.configure(['a'], 'import')
        self.assertEqual(legacy.sync()['imported'], 1)
        plugin = AppleWorker(self.root / 'apple-sync', lambda request: self.business.apple_peer(**request), Bridge('calendar'))
        try:
            plugin.initialize(str(self.root))
            self.assertEqual(plugin.invoke('status')['connected'], True)
            self.assertEqual(plugin.invoke('sync')['imported'], 0)
            plugin.invoke('disconnect')
            self.assertEqual(legacy.status()['connected'], True)
            self.assertEqual(len(self.business.calendar.list_events()), 1)
            self.assertTrue(source.exists())
        finally:
            plugin.close()

    def test_xlsx_keeps_exact_currency_totals_and_excludes_unposted_entries(self):
        base = dict(occurred_at='2026-11-05T12:34:00+08:00', entry_type='expense', name='=1+1', status='posted', currency='CNY')
        rows = [{**base, 'amount_minor': 9007199254740993}, {**base, 'amount_minor': 500, 'status': 'pending'},
                {**base, 'amount_minor': 600, 'status': 'reversed'}, {**base, 'amount_minor': 7, 'currency': 'JPY'}]
        with zipfile.ZipFile(io.BytesIO(build_ledger_xlsx(rows, '2026-11-01', '2026-12-01'))) as archive:
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        amounts = [row.find("s:c[@r='F" + row.attrib['r'] + "']/s:v", ns).text for row in sheet.findall('s:sheetData/s:row', ns)[2:] if row.find("s:c[@r='F" + row.attrib['r'] + "']/s:v", ns) is not None]
        self.assertEqual(amounts, ['-90071992547409.93', '-5.00', '-6.00', '-7', '0.00', '-90071992547409.93', '-90071992547409.93', '0', '-7', '-7'])
        self.assertEqual(sheet.findall('.//s:f', ns), [])

    def test_briefing_actions_project_retry_and_preserve_local_changes(self):
        service = self.business.briefing
        source = {'source': 'elearning', 'account_id': 'test', 'items': [
            {'id': 'source-one', 'source_id': 'assignment', 'title': 'Homework', 'occurred_at': '2026-11-05T01:00:00Z',
             'payload': {'resource_type': 'assignment', 'course_id': '12', 'due_at': '2026-11-06T15:59:00Z'}}], 'cursors': {'until': '2026-11-05T02:00:00Z'}}
        service.create_action_rule({'source': 'elearning', 'account_id': 'test', 'information_type': 'assignment', 'kind': 'todo', 'mode': 'auto', 'course_id': '12'})
        draft = {'title': 'Homework report', 'body': 'One assignment', 'items': [{'source_item_id': 'source-one', 'section': 'action', 'score': 80}]}
        result = service.run_pipeline(briefing_date='2026-11-05', sources=[source], draft=draft, run_at='2026-11-05T02:00:00Z')
        item = result['items'][0]
        self.assertEqual(self.business.tasks.list_todos(), [])
        first_receipt = service.update_action(item.id, {'operation': 'create', 'kind': 'todo'})
        todos = self.business.tasks.list_todos()
        self.assertEqual(len(todos), 1)
        target = todos[0]
        self.assertEqual(first_receipt['target_id'], target['id'])
        receipt = service.update_action(item.id, {'operation': 'create', 'kind': 'todo'})
        self.assertEqual(receipt['target_id'], target['id'])
        self.business.tasks.update_todo(target['id'], {'title': 'My plan', 'due_on': '2026-11-09'})
        source['items'][0]['payload']['due_at'] = '2026-11-07T15:59:00Z'
        service.run_pipeline(briefing_date='2026-11-05', sources=[source], draft=draft, run_at='2026-11-05T03:00:00Z')
        self.business.due_reminders()
        self.assertEqual(len(self.business.tasks.list_todos()), 1)
        self.assertEqual(self.business.tasks.get_todo(target['id'])['due_on'], '2026-11-09')
        windows = self.business.source_windows('2026-11-07', '2026-11-07T12:00:00Z', [{'source': 'elearning', 'accountId': 'test'}])
        self.assertEqual(datetime.fromisoformat(windows[0]).astimezone(timezone.utc).isoformat(), '2026-11-06T12:00:00+00:00')
        self.assertEqual(service.store.pending_projection_events(), [])


if __name__ == '__main__':
    unittest.main()
