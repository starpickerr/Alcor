"""Synthetic SQLite + fake EventKit only; explicit bound choices and receipt recovery."""
import copy
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

M = runpy.run_path(str(Path(__file__).with_name('apple-approved-export.py')))
from apple_sync.approved_export import prepare, guard_unresolved
from apple_sync.bound_conflict import run
from apple_sync.calendar import AppleCalendarSync
from apple_sync.reminders import AppleReminderSync


class BoundConflict(unittest.TestCase):
    def setUp(self):
        self.case = M['ApprovedExport']('test_create_update_batch_skip_both_domains')
        self.case.setUp()
        self.sequence = 0

    def tearDown(self):
        self.case.tearDown()

    def fixture(self, kind):
        self.case.fixtures += 1
        business = M['M']['Business'](self.case.case.root / f'business-{self.case.fixtures}')
        self.addCleanup(business.meta.close)
        owner = business.tasks if kind == 'reminders' else business.calendar
        bridge = M['ConditionalBridge'](kind)
        bridge.rows = M['M']['NativeBridge'](kind).rows
        path = self.case.case.root / f'{kind}-{self.case.fixtures}.sqlite3'
        sync = (AppleReminderSync(bridge=bridge, db_path=path, todo_port=owner) if kind == 'reminders'
                else AppleCalendarSync(bridge=bridge, db_path=path, calendar_port=owner))
        sync.start_auto = lambda: None
        sync.start_sync()
        identity = sync._maps()[0]['todo_id' if kind == 'reminders' else 'alcor_id']
        update = owner.update_todo if kind == 'reminders' else owner.update_event
        update(identity, {'title': 'Local choice'})
        bridge.rows[0]['title'] = 'Apple choice'
        bridge.rows[0]['last_modified'] = '2026-11-08T00:00:00Z'
        sync.sync()
        self.assertEqual(sync._maps()[0]['state'], 'conflict')
        self.assertEqual(bridge.writes, 0)
        return owner, bridge, sync, identity

    def plan(self, sync, kind, identity, choice):
        self.sequence += 1
        return prepare(sync, kind, 'resolve_conflict', {'kind': kind, 'identity': identity, 'choice': choice}, f'{self.sequence:064x}')

    def run_plan(self, owner, sync, kind, plan, authorize=lambda *args: True):
        envelope = plan.get('mode') == 'receipt_finish'
        return run(sync, owner, kind, plan['plan'] if envelope else plan, authorize, receipt_only=envelope)

    def test_both_choices_and_two_sync_rounds(self):
        for kind in ('calendar', 'reminders'):
            for choice in ('keep_apple', 'keep_alcor'):
                with self.subTest(kind=kind, choice=choice):
                    owner, bridge, sync, identity = self.fixture(kind)
                    plan = self.plan(sync, kind, identity, choice)
                    self.assertEqual(plan['items'][0]['action'], 'local_update' if choice == 'keep_apple' else 'update')
                    result = self.run_plan(owner, sync, kind, plan)
                    self.assertEqual(result['status'], 'success', result)
                    self.assertEqual(sync._local(identity)['title'], 'Apple choice' if choice == 'keep_apple' else 'Local choice')
                    self.assertEqual(bridge.rows[0]['title'], sync._local(identity)['title'])
                    for _ in range(2):
                        state = sync.sync()
                        self.assertEqual(state['status'], 'success', state)
                    self.assertEqual(bridge.writes, int(choice == 'keep_alcor'))
                    self.assertEqual(sync._maps()[0]['state'], 'active')
                    repeated = self.plan(sync, kind, identity, choice)
                    self.assertEqual(repeated['mode'], 'receipt_finish')
                    self.assertEqual(self.run_plan(owner, sync, kind, repeated, lambda *a: self.fail('receipt invoked approval'))['status'], 'success')
                    self.assertEqual(bridge.writes, int(choice == 'keep_alcor'))

    def test_lost_owner_reply_recovers_exact_committed_snapshot_not_later_edit(self):
        for kind in ('calendar', 'reminders'):
            for choice in ('keep_apple', 'keep_alcor'):
                with self.subTest(kind=kind, choice=choice):
                    owner, bridge, sync, identity = self.fixture(kind)
                    plan = self.plan(sync, kind, identity, choice)
                    original = owner.apple_commit_bound_conflict
                    calls = []
                    def lost(*args):
                        result = original(*args); calls.append(result)
                        (owner.update_todo if kind == 'reminders' else owner.update_event)(identity, {'title': 'Later user edit'})
                        raise TimeoutError('synthetic lost owner reply')
                    with patch.object(owner, 'apple_commit_bound_conflict', lost):
                        self.assertEqual(self.run_plan(owner, sync, kind, plan)['status'], 'needs_review')
                    guard_unresolved(sync)
                    self.assertEqual(len(calls), 1)
                    restarted = (AppleReminderSync(bridge=bridge, db_path=sync.db_path, todo_port=owner) if kind == 'reminders'
                                 else AppleCalendarSync(bridge=bridge, db_path=sync.db_path, calendar_port=owner))
                    envelope = self.plan(restarted, kind, identity, choice)
                    with patch.object(owner, 'apple_commit_bound_conflict', side_effect=AssertionError('must not write owner twice')):
                        result = self.run_plan(owner, restarted, kind, envelope, lambda *a: self.fail('receipt invoked approval'))
                    self.assertEqual(result['status'], 'success', result)
                    self.assertEqual(restarted._local(identity)['title'], 'Later user edit')
                    from apple_sync.approved_export import _hashes
                    local_hash, _ = _hashes(kind, restarted._local(identity), bridge.rows[0], restarted._maps()[0])
                    self.assertNotEqual(restarted._maps()[0]['local_hash'], local_hash)
                    self.assertEqual(bridge.writes, int(choice == 'keep_alcor'))

    def test_receipt_save_failure_rolls_back_and_never_rewrites(self):
        for kind in ('calendar', 'reminders'):
            owner, bridge, sync, identity = self.fixture(kind)
            plan = self.plan(sync, kind, identity, 'keep_apple')
            before = sync._local(identity)
            with patch('business.apple_bound_conflict.save_receipt', side_effect=RuntimeError('synthetic receipt failure')):
                result = self.run_plan(owner, sync, kind, plan)
            self.assertEqual(result['status'], 'needs_review')
            self.assertEqual(sync._local(identity), before)
            envelope = self.plan(sync, kind, identity, 'keep_apple')
            with patch.object(owner, 'apple_commit_bound_conflict', side_effect=AssertionError('never retry unknown owner')):
                self.assertEqual(self.run_plan(owner, sync, kind, envelope)['status'], 'needs_review')
            self.assertEqual(bridge.writes, 0)

    def test_lost_native_reply_never_replays_or_finalizes_owner_without_receipt(self):
        for kind in ('calendar', 'reminders'):
            owner, bridge, sync, identity = self.fixture(kind)
            plan = self.plan(sync, kind, identity, 'keep_alcor')
            bridge.lose_reply = True
            self.assertEqual(self.run_plan(owner, sync, kind, plan)['status'], 'needs_review')
            self.assertEqual(bridge.writes, 1)
            pending = next(row for row in sync.status()['conflicts'] if row.get('todo_id', row.get('alcor_id')) == identity)
            self.assertEqual(pending['bound_conflict_pending'], {'choice': 'keep_alcor', 'phase': 'native_saved'})
            envelope = self.plan(sync, kind, identity, 'keep_alcor')
            with patch.object(owner, 'apple_commit_bound_conflict', side_effect=AssertionError('receipt path must not write')):
                self.assertEqual(self.run_plan(owner, sync, kind, envelope)['status'], 'needs_review')
            self.assertEqual(bridge.writes, 1)
            self.assertEqual(sync._maps()[0]['state'], 'conflict')
            guard_unresolved(sync)

    def test_denied_selection_and_pre_dispatch_changes_do_not_write(self):
        for kind in ('calendar', 'reminders'):
            for choice in ('keep_apple', 'keep_alcor'):
                for mutation in ('denied', 'native', 'owner', 'links', 'source', 'generation', 'pending'):
                    with self.subTest(kind=kind, choice=choice, mutation=mutation):
                        owner, bridge, sync, identity = self.fixture(kind)
                        plan = self.plan(sync, kind, identity, choice)
                        if mutation == 'native': bridge.rows[0]['title'] = 'Later Apple'
                        elif mutation == 'owner': (owner.update_todo if kind == 'reminders' else owner.update_event)(identity, {'title': 'Later local'})
                        elif mutation == 'source': bridge.sources[0]['source_id'] = 'account2'
                        elif mutation == 'links':
                            with sync._connect() as db: db.execute('UPDATE category_links SET enabled=0')
                        elif mutation == 'generation':
                            with owner._connect() as db: db.execute("UPDATE alcor_object_generations SET generation=? WHERE entity='item' AND object_id=?", ('a' * 32, identity))
                        elif mutation == 'pending':
                            with owner._connect() as db:
                                db.execute("INSERT INTO apple_operations (id,entity,local_id,action,document,created_at) VALUES (?,?,?,?,?,?)", ('synthetic-pending-delete', 'item', identity, 'delete', '{}', '2026-11-08'))
                        before = sync._local(identity)
                        result = self.run_plan(owner, sync, kind, plan, lambda *a: mutation != 'denied')
                        self.assertEqual(result['status'], 'needs_review', result)
                        self.assertEqual(sync._local(identity), before)
                        self.assertEqual(bridge.writes, 0)

    def test_missing_moved_ambiguous_and_old_generation_are_rejected(self):
        for kind in ('calendar', 'reminders'):
            for change in ('missing_native', 'missing_local', 'move', 'binding_source', 'duplicate_binding', 'old_generation'):
                with self.subTest(kind=kind, change=change):
                    owner, bridge, sync, identity = self.fixture(kind)
                    if change == 'missing_native': bridge.rows = []
                    elif change == 'move': bridge.rows[0]['list_id' if kind == 'reminders' else 'calendar_id'] = 'elsewhere'
                    elif change == 'missing_local':
                        with owner._connect() as db: db.execute('DELETE FROM ' + owner.APPLE_ITEMS + ' WHERE id=?', (identity,))
                    elif change == 'binding_source':
                        binding = sync._local(identity)['apple']; binding['source_id'] = 'account2'
                        owner.apple_bind('item', identity, binding)
                    elif change == 'duplicate_binding': owner.apple_bind('item', 'another-local', sync._local(identity)['apple'])
                    elif change == 'old_generation':
                        sync._set(('reminder' if kind == 'reminders' else 'calendar') + '-binding-generation:' + identity,
                                  {'generation': '0' * 32, 'source_id': 'account1', 'group_id': 'a'})
                    with self.assertRaises((ValueError, KeyError)):
                        self.plan(sync, kind, identity, 'keep_apple')
                    self.assertEqual(bridge.writes, 0)

    def test_read_only_apple_can_be_kept_but_cannot_be_overwritten(self):
        for kind in ('calendar', 'reminders'):
            owner, bridge, sync, identity = self.fixture(kind)
            bridge.sources[0]['writable'] = False
            bridge.rows[0]['writable'] = False
            with self.assertRaises(PermissionError): self.plan(sync, kind, identity, 'keep_alcor')
            plan = self.plan(sync, kind, identity, 'keep_apple')
            result = self.run_plan(owner, sync, kind, plan)
            self.assertEqual(result['status'], 'success', result)
            self.assertEqual(sync._local(identity)['title'], 'Apple choice')
            self.assertEqual(bridge.writes, 0)

    def test_approval_callback_recheck_and_fresh_explicit_selection(self):
        for kind in ('calendar', 'reminders'):
            for choice in ('keep_apple', 'keep_alcor'):
                owner, bridge, sync, identity = self.fixture(kind)
                plan = self.plan(sync, kind, identity, choice)
                def change_at_dispatch(operation, fingerprint, local_id):
                    if local_id: bridge.rows[0]['title'] = 'Later Apple choice'
                    return True
                self.assertEqual(self.run_plan(owner, sync, kind, plan, change_at_dispatch)['status'], 'needs_review')
                self.assertEqual(bridge.writes, 0)
                self.assertEqual(sync._local(identity)['title'], 'Local choice')
                fresh = self.plan(sync, kind, identity, choice)
                self.assertEqual(fresh['mode'], 'conflict')
                self.assertEqual(self.run_plan(owner, sync, kind, fresh)['status'], 'success')

    def test_completed_journal_allows_a_new_real_conflict(self):
        for kind in ('calendar', 'reminders'):
            owner, bridge, sync, identity = self.fixture(kind)
            first = self.plan(sync, kind, identity, 'keep_apple')
            self.assertEqual(self.run_plan(owner, sync, kind, first)['status'], 'success')
            (owner.update_todo if kind == 'reminders' else owner.update_event)(identity, {'title': 'Second local'})
            bridge.rows[0]['title'] = 'Second Apple'
            sync.sync()
            second = self.plan(sync, kind, identity, 'keep_alcor')
            self.assertEqual(second['mode'], 'conflict')
            self.assertNotEqual(first['operation_id'], second['operation_id'])
            result = self.run_plan(owner, sync, kind, second)
            self.assertEqual(result['status'], 'success', result)
            self.assertEqual(bridge.rows[0]['title'], 'Second local')
            self.assertEqual(bridge.writes, 1)

    def test_committed_manager_receipt_releases_only_its_retained_claim(self):
        from apple_sync.group_scan_claims import WorkClaim, status
        for kind in ('calendar', 'reminders'):
            owner, bridge, sync, identity = self.fixture(kind)
            plan = self.plan(sync, kind, identity, 'keep_apple')
            with patch.object(WorkClaim, '__exit__', side_effect=RuntimeError('synthetic exit before claim release')):
                self.assertEqual(self.run_plan(owner, sync, kind, plan)['status'], 'needs_review')
            self.assertTrue(status(sync))
            pending = next(row for row in sync.status()['conflicts'] if row.get('todo_id', row.get('alcor_id')) == identity)
            self.assertEqual(pending['bound_conflict_pending'], {'choice': 'keep_apple', 'phase': 'finalizing'})
            envelope = self.plan(sync, kind, identity, 'keep_apple')
            with patch.object(owner, 'apple_commit_bound_conflict', side_effect=AssertionError('must only verify')):
                self.assertEqual(self.run_plan(owner, sync, kind, envelope)['status'], 'success')
            self.assertEqual(status(sync), [])
            self.assertFalse(any(row.get('bound_conflict_pending') for row in sync.status()['conflicts']))
            self.assertEqual(bridge.writes, 0)

    def test_unknown_conflict_does_not_stop_another_healthy_source(self):
        for kind in ('calendar', 'reminders'):
            owner, bridge, sync, identity = self.fixture(kind)
            group, native_key = ('list_id', 'reminder_id') if kind == 'reminders' else ('calendar_id', 'event_id')
            bridge.sources.append({**bridge.sources[0], 'id': 'b', 'title': 'Healthy source'})
            healthy = {**bridge.rows[0], group: 'b', native_key: 'healthy-native', 'external_id': 'healthy-external', 'title': 'Healthy first'}
            bridge.rows.append(healthy)
            sync.start_sync()
            healthy_id = next(row['todo_id' if kind == 'reminders' else 'alcor_id'] for row in sync._maps() if row[group] == 'b')
            plan = self.plan(sync, kind, identity, 'keep_apple')
            with patch.object(owner, 'apple_commit_bound_conflict', side_effect=TimeoutError('synthetic unknown')):
                self.assertEqual(self.run_plan(owner, sync, kind, plan)['status'], 'needs_review')
            healthy['title'] = 'Healthy later'
            guard_unresolved(sync)
            for _ in range(2): sync.sync()
            self.assertEqual(sync._local(healthy_id)['title'], 'Healthy later')
            self.assertEqual(sync._local(identity)['title'], 'Local choice')
            self.assertEqual(bridge.writes, 0)

    def test_public_worker_and_owner_cannot_reach_private_commit(self):
        for domain in ('tasks', 'calendar'):
            for method in ('apple_commit_bound_conflict', 'apple_verify_bound_conflict'):
                with self.assertRaises(ValueError): self.case.case.business.apple_peer(domain, method, ['a' * 64, {}, 'keep_apple', {}])
        owner, bridge, sync, identity = self.fixture('calendar')
        worker = M['worker_module'].AppleWorker(self.case.case.root / 'worker', lambda request: False, bridge=bridge)
        worker.managers = {'calendar': sync}
        for choice in ('keep_apple', 'keep_alcor'):
            with self.assertRaises(PermissionError): worker.invoke('resolve_conflict', {'kind': 'calendar', 'alcor_id': identity, 'choice': choice})


if __name__ == '__main__': unittest.main(verbosity=2)
