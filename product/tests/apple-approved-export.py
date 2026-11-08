"""Synthetic native transport; real owners, managers, claims, and worker routing.
No Profile, EventKit, network, or model is used. Host approval is tested separately.
"""
import copy
import importlib.util
import json
import runpy
import unittest
from pathlib import Path
M = runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))
from apple_sync.calendar import AppleCalendarSync
from apple_sync.reminders import AppleReminderSync
from apple_sync.approved_export import prepare, run, guard_unresolved
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('export_worker', ROOT/'packages/alcor-apple-sync/python/worker.py')
worker_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker_module)


class ConditionalBridge(M['NativeBridge']):
    def __init__(self, kind):
        super().__init__(kind)
        self.rows = []
        self.writes = 0
        self.after_save = None
        self.before_save = None
        self.lose_reply = False

    def call(self, op, **payload):
        if op in ('status', 'reminder_status'):
            return {**super().call(op, **payload), 'approved_export_version': 1}
        if op not in ('upsert', 'reminder_upsert'):
            return super().call(op, **payload)
        if self.before_save:
            self.before_save()
        reminder = self.kind == 'reminders'
        key, group = ('reminder_id', 'list_id') if reminder else ('event_id', 'calendar_id')
        target = next(x for x in self.sources if x['id'] == payload[group])
        rows = [x for x in self.rows if x[key] == payload.get(key) and x[group] == payload[group]]
        existing = rows[0] if len(rows) == 1 else None
        assert payload['expected_source_id'] == target['source_id']
        assert payload['expected_destination_snapshot'] == target
        if payload.get('expected_absent'):
            assert existing is None
        else:
            assert existing == payload['expected_snapshot']
        receipt = Path(payload['receipt_file'])
        assert not receipt.exists()
        if not reminder:
            receipt.write_text(json.dumps({'binding': payload['receipt_binding'], 'intent': True}))
        result = super().call(op, **payload)
        saved = result['reminder' if reminder else 'event']
        if not reminder:
            receipt.write_text(json.dumps({'binding': payload['receipt_binding'], 'saved': saved}))
        self.writes += 1
        if self.after_save:
            self.after_save()
        if self.lose_reply:
            raise TimeoutError('synthetic lost native reply')
        return result


class ApprovedExport(unittest.TestCase):
    def setUp(self):
        self.case = M['Contracts']('test_delete_keeps_local_data_until_apple_acknowledges')
        self.case.setUp()
        self.count = 0
        self.fixtures = 0

    def tearDown(self):
        self.case.tearDown()

    def fixture(self, kind):
        self.fixtures += 1
        owner = self.case.business.tasks if kind == 'reminders' else self.case.business.calendar
        bridge = ConditionalBridge(kind)
        sync = (AppleReminderSync(bridge=bridge, db_path=self.case.root/f'reminders-{self.fixtures}.sqlite3', todo_port=owner)
                if kind == 'reminders' else AppleCalendarSync(bridge=bridge, db_path=self.case.root/f'calendar-{self.fixtures}.sqlite3', calendar_port=owner))
        category = owner.create_category({'name': f'Synthetic export {self.fixtures}'})
        def create(title='Synthetic item'):
            return owner.create_todo({'title': title, 'category_id': category['id'], 'due_on': '2026-11-07'}) if kind == 'reminders' else owner.create_event({'title': title, 'category_id': category['id'], 'start_at': '2026-11-07T01:00:00Z', 'end_at': '2026-11-07T02:00:00Z'})
        return owner, bridge, sync, category, create

    def plan(self, sync, kind, ids):
        self.count += 1
        return prepare(sync, kind, 'one_time', {'kind': kind, 'direction': 'export', 'list_id' if kind == 'reminders' else 'calendar_id': 'a', 'item_ids': ids}, f'{self.count:064x}')

    def test_create_update_batch_skip_both_domains(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind):
                owner, bridge, sync, category, create = self.fixture(kind)
                local = create()
                plan = self.plan(sync, kind, [local['id']])
                self.assertEqual(plan['items'][0]['action'], 'create')
                result = run(sync, kind, plan, lambda *a: True)
                self.assertEqual(result['status'], 'success', result)
                self.assertEqual(bridge.writes, 1)
                getter = owner.get_todo if kind == 'reminders' else owner.get_event
                updater = owner.update_todo if kind == 'reminders' else owner.update_event
                self.assertEqual(getter(local['id'])['apple']['source_id'], 'account1')
                updater(local['id'], {'title': 'Updated explicit export'})
                plan = self.plan(sync, kind, [local['id']])
                self.assertEqual(plan['items'][0]['action'], 'update')
                result = run(sync, kind, plan, lambda *a: True)
                self.assertEqual(result['status'], 'success', result)
                self.assertEqual(bridge.writes, 2)
                self.assertEqual(bridge.rows[0]['title'], 'Updated explicit export')
                plan = prepare(sync, kind, 'transfer_batch', {'kind': kind, 'direction': 'export', 'selections': [{'alcor_id': category['id'], 'apple_id': 'a'}], 'start': '2026-11-01', 'end': '2026-11-30'}, 'f'*64)
                self.assertEqual(plan['items'], [])
                self.assertEqual(plan['skipped'], 1)
                self.assertEqual(run(sync, kind, plan, lambda *a: True)['status'], 'success')
                self.assertEqual(bridge.writes, 2)

    def test_reject_changed_owner_generation_destination_and_native(self):
        for change in ('content', 'generation', 'destination'):
            with self.subTest(change=change):
                owner, bridge, sync, category, create = self.fixture('calendar')
                local = create(); plan = self.plan(sync, 'calendar', [local['id']])
                if change == 'content': owner.update_event(local['id'], {'title': 'Later'})
                elif change == 'destination': bridge.sources[0]['source_id'] = 'account2'
                else:
                    with owner._connect() as db:
                        row = dict(db.execute('SELECT * FROM calendar_events WHERE id=?', (local['id'],)).fetchone())
                        db.execute('DELETE FROM calendar_events WHERE id=?', (local['id'],))
                        db.execute('INSERT INTO calendar_events ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', list(row.values()))
                result = run(sync, 'calendar', plan, lambda *a: True)
                self.assertEqual(result['status'], 'needs_review')
                self.assertEqual(bridge.writes, 0)

    def test_no_grant_no_dispatch_and_revoke_between_items(self):
        owner, bridge, sync, category, create = self.fixture('reminders')
        first, second = create('First'), create('Second')
        plan = self.plan(sync, 'reminders', [first['id'], second['id']])
        with self.assertRaises(PermissionError): run(sync, 'reminders', plan, lambda *a: False)
        self.assertEqual(bridge.writes, 0)
        result = run(sync, 'reminders', plan, lambda op, fp, item: item != second['id'])
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(bridge.writes, 1)
        guard_unresolved(sync)  # Denied before dispatch is proven unstarted.
        self.assertEqual(run(sync, 'reminders', plan, lambda *a: True)['status'], 'needs_review')
        self.assertEqual(bridge.writes, 1)

    def test_unknown_never_retries_and_holds_competing_claim(self):
        owner, bridge, sync, category, create = self.fixture('calendar')
        local = create(); plan = self.plan(sync, 'calendar', [local['id']]); bridge.lose_reply = True
        self.assertEqual(run(sync, 'calendar', plan, lambda *a: True)['status'], 'needs_review')
        self.assertEqual(bridge.writes, 1)
        self.assertEqual(run(sync, 'calendar', plan, lambda *a: True)['status'], 'needs_review')
        from apple_sync.group_scan_claims import WorkClaim
        with self.assertRaises(ValueError):
            with WorkClaim(sync, {'a': 'account1'}, [local['id']], {'kind': 'synthetic competing writer'}): pass
        with self.assertRaises(ValueError): guard_unresolved(sync)
        self.assertEqual(bridge.writes, 1)
        # A fresh manager instance sees the durable hold, not just the in-memory lock.
        restarted = AppleCalendarSync(bridge=bridge, db_path=sync.db_path, calendar_port=owner)
        from apple_sync.export_reservations import held_groups
        self.assertEqual(held_groups(restarted), {'a'})
        with self.assertRaises(ValueError): guard_unresolved(restarted)
        newer = self.plan(restarted, 'calendar', [local['id']])
        self.assertEqual(run(restarted, 'calendar', newer, lambda *a: True)['status'], 'needs_review')
        self.assertEqual(bridge.writes, 1)

    def test_owner_edit_after_native_save_preserved_with_hold(self):
        owner, bridge, sync, category, create = self.fixture('reminders')
        local = create(); plan = self.plan(sync, 'reminders', [local['id']])
        bridge.after_save = lambda: owner.update_todo(local['id'], {'title': 'Later user edit'})
        result = run(sync, 'reminders', plan, lambda *a: True)
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(owner.get_todo(local['id'])['title'], 'Later user edit')
        self.assertEqual(sync._maps(), [])
        with self.assertRaises(ValueError): guard_unresolved(sync)

    def test_native_change_after_preview_and_old_helper_fail_closed(self):
        owner, bridge, sync, category, create = self.fixture('reminders')
        local = create(); first = self.plan(sync, 'reminders', [local['id']])
        self.assertEqual(run(sync, 'reminders', first, lambda *a: True)['status'], 'success')
        owner.update_todo(local['id'], {'title': 'Explicit update'})
        plan = self.plan(sync, 'reminders', [local['id']])
        bridge.rows[0]['title'] = 'New Apple edit'
        self.assertEqual(run(sync, 'reminders', plan, lambda *a: True)['status'], 'needs_review')
        self.assertEqual(bridge.writes, 1)
        original = bridge.call
        bridge.call = lambda op, **args: {'authorization': 'full_access'} if op == 'reminder_status' else original(op, **args)
        with self.assertRaises(PermissionError): self.plan(sync, 'reminders', [local['id']])

    def test_public_worker_and_manager_do_not_accept_model_confirmation(self):
        owner, bridge, sync, category, create = self.fixture('calendar')
        worker = worker_module.AppleWorker(self.case.root/'worker', lambda *a: False, bridge=bridge)
        worker.managers = {'calendar': sync}
        for method, args in [('one_time', {'direction': 'export', 'confirm': True}), ('transfer_batch', {'direction': 'export', 'confirm': True, 'token': 'fake'}), ('resolve_conflict', {'choice': 'keep_alcor'}), ('sync', {'confirm_deletions': True})]:
            with self.assertRaises(PermissionError): worker.invoke(method, args)
        with self.assertRaises(PermissionError): sync.one_time('export', 'a', ['fake'], confirm=True)
        self.assertEqual(bridge.writes, 0)

    def test_export_preserves_marker_and_retained_holds_before_and_after_preview(self):
        for kind in ('calendar', 'reminders'):
            for hold in ('marker', 'retained'):
                for timing in ('before_preview', 'after_preview'):
                    with self.subTest(kind=kind, hold=hold, timing=timing):
                        owner, bridge, sync, category, create = self.fixture(kind)
                        local = create()
                        plan = self.plan(sync, kind, [local['id']]) if timing == 'after_preview' else None
                        key = ('marker-conflict:' if hold == 'marker' else 'category-retained:') + local['id']
                        evidence = {'local_id': local['id']} if hold == 'marker' else {'member_ids': [local['id']]}
                        sync._set(key, evidence)
                        with owner._connect() as db: before = list(db.iterdump())
                        if plan is None:
                            with self.assertRaisesRegex(ValueError, '保留'):
                                self.plan(sync, kind, [local['id']])
                        else:
                            result = run(sync, kind, plan, lambda *a: True)
                            self.assertEqual(result['status'], 'needs_review')
                            self.assertEqual(result['exported'], 0)
                        self.assertEqual(bridge.writes, 0)
                        self.assertEqual(sync._maps(), [])
                        self.assertEqual(sync._get(key), evidence)
                        with owner._connect() as db: self.assertEqual(list(db.iterdump()), before)


if __name__ == '__main__': unittest.main(verbosity=2)
