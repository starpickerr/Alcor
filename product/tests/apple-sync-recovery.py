"""Owner and manager recovery using synthetic EventKit and temporary SQLite."""
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

CASE = runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))['case']

def merge_fixture(kind, sync, owner, bridge, stop=None):
    sync.start_sync()
    sync.sync()  # Establish the existing incarnation guard after the first import.
    reminder = kind == 'reminders'
    identity = sync._maps()[0]['todo_id' if reminder else 'alcor_id']
    field = 'notes' if reminder else 'location'
    (owner.update_todo if reminder else owner.update_event)(identity, {'title': 'Local title'})
    bridge.rows[0][field] = 'Apple edit'
    original = bridge.call
    def call(method, **kwargs):
        if kwargs.get('direct_patch'):
            assert kwargs['expected_snapshot'] == bridge.rows[0]
            if stop == 'before_native':
                raise RuntimeError('synthetic interruption without receipt')
        result = original(method, **kwargs)
        if method in ('status', 'reminder_status'):
            result['approved_export_version'] = 1
        if kwargs.get('direct_patch'):
            if not reminder:
                Path(kwargs['receipt_file']).write_text(json.dumps({'binding':kwargs['receipt_binding'], 'saved':result['event']}))
            if stop == 'after_native':
                raise RuntimeError('synthetic response loss after save')
        return result
    bridge.call = call
    return identity, field


class Recovery(unittest.TestCase):
    def test_background_read_failures_back_off_per_source_and_manual_check_can_retry(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), CASE(kind) as (sync, owner, bridge):
                bridge.sources.append({**bridge.sources[0], 'id':'b', 'title':'Second source'})
                sync.start_sync()
                op, key = ('events', 'calendar_ids') if kind == 'calendar' else ('reminders', 'list_ids')
                original = bridge.call
                def delayed(method, **kwargs):
                    if method == op and kwargs[key] == ['a']:
                        raise TimeoutError('synthetic read timeout')
                    return original(method, **kwargs)
                with patch.object(bridge, 'call', side_effect=delayed) as calls:
                    first = sync.sync(background=True)
                    self.assertEqual(first['retrying_group_ids'], ['a'])
                    self.assertIn('b', first['checked_group_ids'])
                    self.assertFalse(sync.status()['scan_claims'])
                    calls.reset_mock()
                    again = sync.sync(background=True)
                    self.assertIn('b', again['checked_group_ids'])
                    self.assertFalse(any(call.args[0] == op and 'a' in call.kwargs[key] for call in calls.call_args_list))
                bridge.calls.clear()
                manual = sync.sync(manual=True)
                self.assertNotIn('retrying_group_ids', manual)
                self.assertIn('a', manual['checked_group_ids'])

    def test_reading_claim_can_be_replaced_but_stale_reader_cannot_start_writing(self):
        from apple_sync import group_scan_claims as claims
        with CASE('calendar') as (sync, owner, bridge):
            window = ['2026-01-01T00:00:00Z', '2027-01-01T00:00:00Z']
            old = claims.acquire(sync, 'account1', 'a', window, reading=True)
            new = claims.acquire(sync, 'account1', 'a', window, reading=True)
            with self.assertRaises(claims.ScanHeld):
                claims.begin_item_phase(sync, old)
            claims.begin_item_phase(sync, new)
            with self.assertRaises(claims.ScanHeld):
                claims.acquire(sync, 'account1', 'a', window, reading=True)

    def test_old_scan_read_is_scoped_and_preserves_unknown_operation(self):
        from apple_sync import group_scan_claims as claims
        from apple_sync.sync_recovery import check_scan
        with CASE('calendar') as (sync, owner, bridge):
            sync.start_sync()
            window = ['2026-01-01T00:00:00Z', '2027-01-01T00:00:00Z']
            token = claims.acquire(sync, 'account1', 'a', window)
            claims.claim_items(sync, token, [sync._maps()[0]['alcor_id']])
            claims.uncertain(sync, token)
            original = sync._maps()
            bridge.calls.clear()
            result = check_scan(sync, 'calendar', token)
            self.assertEqual(result['status'], 'needs_evidence')
            self.assertEqual(result['checked'], 1)
            self.assertEqual(sync._maps(), original)
            self.assertEqual([row['attempt_id'] for row in claims.status(sync)], [token])
            self.assertEqual([method for method, _ in bridge.calls], ['status', 'calendars', 'events'])
            bridge.sources[0]['source_id'] = 'different'
            self.assertEqual(check_scan(sync, 'calendar', token)['status'], 'source_unavailable')

    def test_keep_legacy_does_not_delete_or_requeue_after_restart(self):
        from apple_sync.sync_recovery import keep_legacy, preview_legacy
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), CASE(kind) as (sync, owner, bridge):
                sync.start_sync()
                identity = sync._maps()[0]['alcor_id' if kind == 'calendar' else 'todo_id']
                owner.apple_remove_item(identity)
                sync.sync()
                self.assertEqual(sync._maps()[0]['state'], 'pending_delete')
                preview = preview_legacy(sync, kind, identity)
                bridge.rows[0]['title'] = 'Changed after preview'
                with self.assertRaisesRegex(ValueError, '变化'):
                    keep_legacy(sync, kind, identity, preview['revision'])
                keep_legacy(sync, kind, identity, preview_legacy(sync, kind, identity)['revision'])
                bridge.calls.clear()
                sync.sync()
                self.assertEqual(sync._maps()[0]['state'], 'legacy_kept')
                self.assertEqual(sync.status()['legacy_deletions'], [])
                self.assertIsNone(sync._local(identity))
                self.assertFalse(any(method in ('upsert','delete','reminder_upsert','reminder_delete') for method, _ in bridge.calls))

    def test_choices_from_stale_comparison_cannot_be_prepared(self):
        from worker import AppleWorker
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), CASE(kind) as (sync, owner, bridge):
                bridge.rows[0]['last_modified'] = '2026-11-01T00:00:00Z'
                identity, _ = merge_fixture(kind, sync, owner, bridge)
                bridge.rows[0]['title'] = 'Apple title'
                sync.sync()
                worker = AppleWorker.__new__(AppleWorker)
                worker.verification_no_apple = False
                worker.managers = {kind:sync}
                preview = worker.review_sync(kind, 'compare_record', identity)
                fields = {difference['field']: difference for difference in preview['differences']}
                self.assertEqual(fields['title']['apple'], 'Apple title')
                request = {'kind':kind, 'identity':identity, 'choice':'keep_apple'}
                worker.prepare_approved_export('resolve_conflict', request, 'a'*64, preview['revision'])
                bridge.rows[0]['title'] = 'Apple edited again'
                with self.assertRaisesRegex(ValueError, '重新比较'):
                    worker.prepare_approved_export('resolve_conflict', request, 'a'*64, preview['revision'])

    def test_restore_legacy_resumes_after_owner_commit_without_rewriting_apple(self):
        from apple_sync.item_restore import prepare, run
        from apple_sync.sync_recovery import preview_legacy
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), CASE(kind) as (sync, owner, bridge):
                sync.start_sync()
                identity = sync._maps()[0]['alcor_id' if kind == 'calendar' else 'todo_id']
                owner.apple_remove_item(identity)
                sync.sync()
                preview = preview_legacy(sync, kind, identity)
                self.assertTrue(preview['can_restore'])
                plan = prepare(sync, owner, kind, identity, revision=preview['revision'])
                with patch('apple_sync.item_restore._finish', side_effect=RuntimeError('synthetic interruption after restore')):
                    with self.assertRaisesRegex(RuntimeError, 'after restore'):
                        run(sync, owner, kind, plan, lambda: True)
                self.assertTrue(sync.status()['scan_claims'])
                bridge.calls.clear()
                sync.sync()
                self.assertEqual(sync._local(identity)['title'], preview['title'])
                self.assertEqual(sync._maps()[0]['state'], 'active')
                self.assertFalse(sync.status()['scan_claims'])
                self.assertFalse(any(method in ('upsert','delete','reminder_upsert','reminder_delete') for method, _ in bridge.calls))

    def test_candidate_comparison_reads_only_original_candidate_source(self):
        from apple_sync.sync_recovery import compare_identity
        with CASE('reminders') as (sync, owner, bridge):
            bridge.sources.append({**bridge.sources[0], 'id':'b', 'title':'Second list', 'source_id':'account2', 'source':'Work'})
            sync.start_sync()
            bridge.rows.append({**bridge.rows[0], 'list_id':'b', 'source_id':'account2', 'title':'Other reminder'})
            sync.sync()
            candidate = sync.status()['identity_candidates'][0]
            bridge.calls.clear()
            result = compare_identity(sync, 'reminders', candidate['key'])
            self.assertEqual(result['apple']['title'], 'Other reminder')
            self.assertEqual(result['apple']['source'], 'Work')
            self.assertEqual([row['title'] for row in result['alcor']], [bridge.rows[0]['title']])
            self.assertEqual([method for method, _ in bridge.calls], ['reminder_status', 'reminder_lists', 'reminder_lookup'])

    def test_missing_apple_item_is_a_source_check_and_preserves_local_edits(self):
        from apple_sync.sync_recovery import check_missing
        with CASE('calendar') as (sync, owner, bridge):
            sync.start_sync()
            mapping = sync._maps()[0]
            identity = mapping['alcor_id']
            owner.update_event(identity, {'title':'Local edit survives'})
            native = bridge.rows.pop()
            reason = 'Apple item was absent from a bounded lookup'
            sync._conflict(mapping, reason)
            sync._set('calendar-inbound-conflict:'+identity,
                      {'reason':reason, 'remote':{'absence_evidence':'bounded_lookup_null'}})
            self.assertTrue(sync.status()['conflicts'][0]['source_check'])
            self.assertEqual(check_missing(sync, 'calendar', identity)['status'], 'unconfirmed')
            bridge.rows.append(native)
            self.assertEqual(check_missing(sync, 'calendar', identity)['status'], 'present')
            self.assertEqual(sync._local(identity)['title'], 'Local edit survives')

    def test_disjoint_text_merge_uses_a_conditional_native_patch(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind), CASE(kind) as (sync, owner, bridge):
                identity, field = merge_fixture(kind, sync, owner, bridge)
                result = sync.sync()
                self.assertEqual(result['conflicts'], 0, result)
                self.assertEqual(bridge.rows[0]['title'], 'Local title')
                self.assertEqual(sync._local(identity)[field], 'Apple edit')
                self.assertEqual(sync._local(identity)['title'], 'Local title')
                self.assertFalse(sync.status()['scan_claims'])
                (owner.update_todo if kind == 'reminders' else owner.update_event)(identity, {'title': 'Second local title'})
                bridge.rows[0]['title'] = 'Second Apple title'
                result = sync.sync()
                self.assertGreater(result['conflicts'], 0)
                self.assertEqual(bridge.rows[0]['title'], 'Second Apple title')

    def test_merge_recheck_finishes_existing_receipt_without_resending(self):
        from apple_sync.sync_recovery import check_scan
        for kind in ('calendar', 'reminders'):
            for point in ('before_native', 'after_native', 'after_owner', 'after_mapping'):
                with self.subTest(kind=kind, point=point), CASE(kind) as (sync, owner, bridge):
                    identity, field = merge_fixture(kind, sync, owner, bridge, point)
                    module = 'apple_sync.sync_merge.'
                    if point == 'after_owner':
                        path = '_commit_reminder_snapshot' if kind == 'reminders' else '_commit_inbound_snapshot'
                        with patch.object(sync, path, side_effect=RuntimeError('synthetic owner acknowledgement lost')):
                            sync.sync()
                    elif point == 'after_mapping':
                        from apple_sync.sync_merge import _record_phase
                        def checkpoint(*args, **kwargs):
                            if args[-1] == {}:
                                raise RuntimeError('synthetic crash after mapping commit')
                            return _record_phase(*args, **kwargs)
                        with patch(module+'_record_phase', side_effect=checkpoint):
                            sync.sync()
                    else:
                        sync.sync()
                    attempts = sync.status()['scan_claims']
                    self.assertEqual(len(attempts), 1)
                    bridge.calls.clear()
                    outcome = check_scan(sync, kind, attempts[0]['attempt_id'])
                    if point == 'before_native':
                        self.assertEqual(outcome['status'], 'needs_evidence')
                        self.assertTrue(sync.status()['scan_claims'])
                    else:
                        self.assertEqual(outcome['status'], 'receipt_finished')
                        self.assertFalse(sync.status()['scan_claims'])
                        self.assertEqual(sync._local(identity)[field], 'Apple edit')
                    self.assertFalse(any(op in ('upsert','reminder_upsert') for op, _ in bridge.calls))

if __name__ == '__main__':
    unittest.main()
