"""Synthetic owner contracts only; does not prove DSH approval or invoke native Apple."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import hashlib
import json
import sys
import tempfile
import unittest

sys.path.insert(0, 'packages/alcor-secretary')
from business.worker import Business


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class OwnerDeletion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='alcor-owner-approved-')
        self.addCleanup(self.tmp.cleanup)
        self.b = Business(self.tmp.name)
        self.addCleanup(self.b.meta.close)
        self.counter = 0

    def op(self):
        self.counter += 1
        return digest(str(self.counter))

    def item(self, domain, category=None, recurring=False):
        payload = {'title': 'Synthetic'}
        if category is not None: payload['category_id'] = category
        if domain == 'calendar':
            payload.update(start_at='2026-11-01T09:00:00+00:00', end_at='2026-11-01T10:00:00+00:00')
            if recurring: payload.update(repeat_frequency='daily', repeat_timezone='UTC')
            return self.b.calendar.create_event(payload)
        return self.b.tasks.create_todo(payload)

    def linked(self, domain, category=False, recurring=False):
        svc = getattr(self.b, domain)
        group = svc.create_category({'name': 'Synthetic ' + self.op()[:8]})
        row = group if category else self.item(domain, group['id'], recurring)
        entity = 'category' if category else 'item'
        binding = {'apple_id': 'group' if category else 'native', 'group_id': 'group', 'source_id': 'source', 'can_delete': True, 'can_modify': True}
        svc.apple_bind(entity, row['id'], binding)
        return svc, row, binding

    def queue(self, domain, row, category=False, scope=None, occurrence=None, confirmed=False):
        method = 'delete_category' if category else 'delete_todo' if domain == 'tasks' else 'delete_event'
        key = ('category_id' if category else 'todo_id') if domain == 'tasks' else 'id'
        args = {key: row['id']}
        if scope: args.update(scope=scope, occurrence_on=occurrence)
        operation = self.op()
        plan = self.b.prepare_apple_delete(domain, method, args, operation)
        apple = plan['apple']; binding = apple['binding']; entity = apple['entity']
        native = {'id': 'group', 'source_id': 'source'} if category else {('reminder_id' if domain == 'tasks' else 'event_id'): 'native', ('list_id' if domain == 'tasks' else 'calendar_id'): 'group', 'source_id': 'source'}
        approved = dict(version=1, source_id='source', group_id='group', series_apple_id=binding['apple_id'], instance_apple_id=binding['apple_id'], occurrence_on=apple['occurrence_on'], native_fingerprint=digest(native), entity=entity, scope=apple['scope'], native_snapshot=native)
        full = {'version': 1, 'domain': domain, 'kind': 'reminders' if domain == 'tasks' else 'calendar', 'operation_id': operation, 'local_plan': plan, 'native': approved}
        self.expected_confirmed_plan = {**full, 'plan_fingerprint': digest(full)}
        intent = {'version': 1, 'plan_fingerprint': digest(full) if confirmed is True else '0'*64} if confirmed else None
        commit = lambda: self.b.commit_apple_delete(domain, method, args, operation, plan['snapshot'], approved, confirmed_intent=intent)
        commit()
        return getattr(self.b, domain).apple_operations()[-1], commit

    def test_local_items_categories_cas_and_replay(self):
        for domain in ('tasks', 'calendar'):
            for category in (False, True):
                with self.subTest(domain=domain, category=category):
                    svc = getattr(self.b, domain)
                    row = svc.create_category({'name': self.op()}) if category else self.item(domain)
                    operation = self.op()
                    plan = self.b.local_delete_prepare(domain, row['id'], operation, category=category)
                    commit = lambda: self.b.local_delete_commit(domain, row['id'], operation, plan['snapshot'], category=category)
                    table = svc.APPLE_CATEGORIES if category else svc.APPLE_ITEMS
                    with svc._connect() as db:
                        original = dict(db.execute(f'SELECT * FROM {table} WHERE id=?', (row['id'],)).fetchone())
                        field = 'name' if category else 'title'
                        db.execute(f'UPDATE {table} SET {field}=? WHERE id=?', ('Changed', row['id']))
                    with self.assertRaises(ValueError): commit()
                    with svc._connect() as db: db.execute(f'UPDATE {table} SET {field}=? WHERE id=?', (original[field], row['id']))
                    if category:
                        member = self.item(domain, row['id'])
                        with self.assertRaises(ValueError): commit()
                        with svc._connect() as db: db.execute(f'DELETE FROM {svc.APPLE_ITEMS} WHERE id=?', (member['id'],))
                    self.assertTrue(commit()['deleted'])
                    self.assertTrue(commit()['replayed'])
                    with svc._connect() as db:
                        db.execute(f'INSERT INTO {table} ({",".join(original)}) VALUES ({",".join("?" for _ in original)})', tuple(original.values()))
                    self.assertTrue(commit()['targetConflict'])
                    stale_op = self.op()
                    stale = self.b.local_delete_prepare(domain, row['id'], stale_op, category=category)
                    with svc._connect() as db:
                        db.execute(f'DELETE FROM {table} WHERE id=?', (row['id'],))
                        db.execute(f'INSERT INTO {table} ({",".join(original)}) VALUES ({",".join("?" for _ in original)})', tuple(original.values()))
                    with self.assertRaises(ValueError): self.b.local_delete_commit(domain, row['id'], stale_op, stale['snapshot'], category=category)

    def test_local_recurrence_scopes(self):
        for scope, day in [('this', '2026-11-02'), ('future', '2026-11-03'), ('future', '2026-11-01')]:
            row = self.item('calendar', recurring=True); operation = self.op()
            plan = self.b.prepare_local_occurrence_delete(row['id'], day, scope, operation)
            commit = lambda: self.b.commit_local_occurrence_delete(row['id'], day, scope, operation, plan['snapshot'])
            self.assertTrue(commit()['deleted']); self.assertTrue(commit()['replayed'])
            with self.b.calendar._connect() as db:
                present = db.execute('SELECT * FROM calendar_events WHERE id=?', (row['id'],)).fetchone()
                if scope == 'this': self.assertTrue(db.execute('SELECT 1 FROM calendar_occurrence_exclusions WHERE series_id=?', (row['id'],)).fetchone())
                elif day.endswith('03'): self.assertEqual(present['repeat_until'], '2026-11-02')
                else: self.assertIsNone(present)

    def test_apple_items_categories_and_private_claim(self):
        for domain in ('tasks', 'calendar'):
            for category in (False, True):
                svc, row, binding = self.linked(domain, category)
                operation, replay = self.queue(domain, row, category)
                self.assertTrue(replay()['replayed'])
                with self.assertRaises(PermissionError): svc.apple_begin_operation(operation['id'], operation['document'], binding)
                with self.assertRaises(PermissionError): svc.apple_finish_operation(operation['id'])
                with self.assertRaises(ValueError): svc.apple_finish_approved_delete(operation['id'])
                with self.assertRaises(ValueError): svc.apple_begin_approved_delete(operation['id'], {}, binding)
                with self.assertRaises(ValueError): self.b.apple_peer(domain, 'apple_begin_approved_delete')
                svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
                self.assertEqual(svc.apple_reset_approved_delete(operation['id'], operation['document'], binding), {'state': 'pending', 'not_sent': True})
                svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
                self.assertEqual(svc.apple_finish_approved_delete(operation['id'])['state'], 'done')
                self.assertEqual(svc.apple_finish_approved_delete(operation['id'])['state'], 'done')
                with svc._connect() as db:
                    self.assertTrue(db.execute("SELECT 1 FROM apple_inbound_recovery WHERE status='operation_completed'").fetchone())

    def test_apple_conflict_retains_data_and_archives(self):
        for domain in ('tasks', 'calendar'):
            svc, row, binding = self.linked(domain)
            operation, _ = self.queue(domain, row)
            svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
            with svc._connect() as db: db.execute(f'UPDATE {svc.APPLE_ITEMS} SET title=? WHERE id=?', ('Changed after send', row['id']))
            with self.assertRaises(ValueError): svc.apple_reset_approved_delete(operation['id'], operation['document'], binding)
            self.assertEqual(svc.apple_finish_approved_delete(operation['id'])['state'], 'uncertain')
            with svc._connect() as db:
                self.assertEqual(db.execute(f'SELECT title FROM {svc.APPLE_ITEMS} WHERE id=?', (row['id'],)).fetchone()[0], 'Changed after send')
                conflict_id = db.execute("SELECT id FROM apple_inbound_recovery WHERE status='operation_conflict'").fetchone()[0]
            with self.assertRaises(ValueError): self.b.restore_inbound_recovery(domain, conflict_id)
            self.assertFalse(any(x['id'] == conflict_id for x in self.b.list_inbound_recoveries(domain)))

    def test_apple_exclude_is_applied_only_after_finish(self):
        svc, row, binding = self.linked('calendar', recurring=True)
        operation, _ = self.queue('calendar', row, scope='this', occurrence='2026-11-02')
        with svc._connect() as db: self.assertFalse(db.execute('SELECT 1 FROM calendar_occurrence_exclusions').fetchone())
        svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
        svc.apple_reset_approved_delete(operation['id'], operation['document'], binding)
        with svc._connect() as db: self.assertFalse(db.execute('SELECT 1 FROM calendar_occurrence_exclusions').fetchone())
        svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
        self.assertEqual(svc.apple_finish_approved_delete(operation['id'])['state'], 'done')
        with svc._connect() as db: self.assertTrue(db.execute('SELECT 1 FROM calendar_occurrence_exclusions').fetchone())

    def test_apple_rechecks_content_membership_binding_and_generation(self):
        for domain in ('tasks', 'calendar'):
            for category in (False, True):
                svc, row, binding = self.linked(domain, category)
                operation, _ = self.queue(domain, row, category)
                table = svc.APPLE_CATEGORIES if category else svc.APPLE_ITEMS
                with svc._connect() as db:
                    original = dict(db.execute(f'SELECT * FROM {table} WHERE id=?', (row['id'],)).fetchone())
                    field = 'name' if category else 'title'
                    db.execute(f'UPDATE {table} SET {field}=? WHERE id=?', ('Changed', row['id']))
                begin = lambda: svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
                with self.assertRaises(ValueError): begin()
                with svc._connect() as db: db.execute(f'UPDATE {table} SET {field}=? WHERE id=?', (original[field], row['id']))
                with self.assertRaises(ValueError): svc.apple_begin_approved_delete(operation['id'], operation['document'], {**binding, 'source_id': 'other'})
                if category:
                    # Insert through the synthetic DB to model an inbound race behind the UI guard.
                    member = self.item(domain)
                    with svc._connect() as db: db.execute(f'UPDATE {svc.APPLE_ITEMS} SET category_id=? WHERE id=?', (row['id'], member['id']))
                    with self.assertRaises(ValueError): begin()
                    with svc._connect() as db: db.execute(f'DELETE FROM {svc.APPLE_ITEMS} WHERE id=?', (member['id'],))
                with svc._connect() as db:
                    db.execute(f'DELETE FROM {table} WHERE id=?', (row['id'],))
                    db.execute(f'INSERT INTO {table} ({",".join(original)}) VALUES ({",".join("?" for _ in original)})', tuple(original.values()))
                with self.assertRaises(ValueError): begin()
                self.assertEqual(svc.apple_operations()[-1]['state'], 'pending')

    def test_cancel_and_claim_of_the_same_approved_occurrence_are_exclusive(self):
        for scope in ('this', 'future'):
            svc, row, binding = self.linked('calendar', recurring=True)
            operation, _ = self.queue('calendar', row, scope=scope, occurrence='2026-11-02')
            start = Barrier(2)
            def attempt(action):
                start.wait(timeout=5)
                try:
                    action()
                except ValueError:
                    return False
                return True
            with ThreadPoolExecutor(max_workers=2) as pool:
                claim = pool.submit(attempt, lambda: svc.apple_begin_approved_delete(
                    operation['id'], operation['document'], binding))
                cancel = pool.submit(attempt, lambda: svc.cancel_apple_operation(operation['id']))
                self.assertNotEqual(claim.result(), cancel.result())
            active = [op for op in svc.apple_operations() if op['id'] == operation['id']]
            self.assertEqual([op['state'] for op in active], ['sending'] if claim.result() else [])
            self.assertTrue(svc.get_event(row['id']))

    def test_apple_future_scope_and_uncertain_keep_original_until_finish(self):
        for day in ('2026-11-01', '2026-11-03'):
            svc, row, binding = self.linked('calendar', recurring=True)
            operation, _ = self.queue('calendar', row, scope='future', occurrence=day)
            svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
            svc.apple_operation_error(operation['id'], 'Synthetic unknown result')
            self.assertFalse(svc.get_event(row['id'])['repeat_until'])
            with self.assertRaises(ValueError): svc.apple_reset_approved_delete(operation['id'], operation['document'], binding)
            self.assertEqual(svc.apple_finish_approved_delete(operation['id'])['state'], 'done')
            with svc._connect() as db:
                current = db.execute('SELECT * FROM calendar_events WHERE id=?', (row['id'],)).fetchone()
                if day.endswith('01'): self.assertIsNone(current)
                else: self.assertEqual(current['repeat_until'], '2026-11-02')

    def assert_local_recovery(self, domain, recovery_id, original_ids):
        svc = getattr(self.b, domain)
        before = svc.apple_operations()
        result = self.b.restore_inbound_recovery(domain, recovery_id)
        self.assertEqual(result['sync'], 'local_only')
        self.assertFalse(set(result['ids']) & set(original_ids))
        replay = self.b.restore_inbound_recovery(domain, recovery_id)
        self.assertTrue(replay['replayed']); self.assertEqual(replay['ids'], result['ids'])
        self.assertEqual(svc.apple_operations(), before)
        with svc._connect() as db:
            for identity in result['ids']:
                row = db.execute(f'SELECT * FROM {svc.APPLE_ITEMS} WHERE id=?', (identity,)).fetchone()
                self.assertEqual(row['source_type'], 'apple_recovered'); self.assertIsNone(row['source_key'])
                self.assertFalse(db.execute("SELECT 1 FROM apple_bindings WHERE entity='item' AND local_id=?", (identity,)).fetchone())
                if domain == 'tasks':
                    self.assertFalse(row['remind_at'])
                    self.assertFalse(db.execute("SELECT 1 FROM reminder_occurrences WHERE todo_id=? AND state!='cancelled'", (identity,)).fetchone())
                    self.assertFalse(db.execute("SELECT 1 FROM reminder_deliveries d JOIN reminder_occurrences o ON d.occurrence_id=o.occurrence_id WHERE o.todo_id=? AND (d.claim_id IS NOT NULL OR d.lease_until IS NOT NULL OR d.status NOT IN ('shown','accepted','unknown','cancelled'))", (identity,)).fetchone())
            for identity in result['categoryIds']:
                self.assertFalse(db.execute("SELECT 1 FROM apple_bindings WHERE entity='category' AND local_id=?", (identity,)).fetchone())
                self.assertTrue(db.execute("SELECT 1 FROM apple_inbound_local_only WHERE entity='category' AND local_id=?", (identity,)).fetchone())
        return result

    def test_completed_recoveries_use_existing_local_only_restore(self):
        for domain in ('tasks', 'calendar'):
            for category in (False, True):
                svc, row, binding = self.linked(domain, category)
                original = row if not category else self.item(domain, row['id'])
                if domain == 'tasks':
                    with svc._connect() as db:
                        occurrence = self.op()
                        db.execute('UPDATE todos SET remind_at=? WHERE id=?', ('2026-11-10T09:00:00Z', original['id']))
                        db.execute('INSERT INTO reminder_occurrences VALUES (?,?,?,?,?,?)', (occurrence, original['id'], '2026-11-10', 'due', '2026-11-10', 'pending'))
                        db.execute('INSERT INTO reminder_deliveries VALUES (?,?,?,?,?,?,?,?)', (self.op(), occurrence, 'pending', None, None, 0, None, None))
                operation, _ = self.queue(domain, row, category)
                self.assertFalse(any(x['target_id'] == row['id'] for x in self.b.list_inbound_recoveries(domain)))
                with svc._connect() as db:
                    recovery_id = db.execute("SELECT id FROM apple_inbound_recovery WHERE status='operation_approved'").fetchone()[0]
                with self.assertRaises(ValueError): self.b.restore_inbound_recovery(domain, recovery_id)
                svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
                svc.apple_finish_approved_delete(operation['id'])
                listed = next(x for x in self.b.list_inbound_recoveries(domain) if x['id'] == recovery_id)
                self.assertEqual(listed['status'], 'available')
                self.assert_local_recovery(domain, recovery_id, [original['id']])

    def test_import_category_delete_is_local_only_conditional_and_recoverable(self):
        for domain in ('tasks', 'calendar'):
            svc, category, binding = self.linked(domain, category=True)
            binding['can_delete'] = False
            svc.apple_bind('category', category['id'], binding)
            item = self.item(domain, category['id'])
            svc.apple_bind('item', item['id'], {**binding, 'apple_id': 'native'})
            if domain == 'tasks':
                with svc._connect() as db: db.execute("UPDATE todos SET source_type='apple_reminder',source_key=? WHERE id=?", ('synthetic-import-key', item['id']))
            operation = self.op()
            plan = self.b.prepare_import_category_delete(domain, category['id'], operation)
            self.assertEqual({x['id'] for x in plan['targets']}, {category['id'], item['id']})
            commit = lambda: self.b.commit_import_category_delete(domain, category['id'], operation, plan['snapshot'])
            with svc._connect() as db: db.execute(f'UPDATE {svc.APPLE_ITEMS} SET title=? WHERE id=?', ('Changed', item['id']))
            with self.assertRaises(ValueError): commit()
            with svc._connect() as db: db.execute(f'UPDATE {svc.APPLE_ITEMS} SET title=? WHERE id=?', ('Synthetic', item['id']))
            result = commit(); self.assertEqual(result['state'], 'done')
            self.assertEqual(commit()['recovery_id'], result['recovery_id']); self.assertTrue(commit()['replayed'])
            self.assertEqual(svc.apple_operations(), [])
            if domain == 'tasks':
                with svc._connect() as db: self.assertFalse(db.execute('SELECT 1 FROM source_tombstones WHERE source_key=?', ('synthetic-import-key',)).fetchone())
            self.assert_local_recovery(domain, result['recovery_id'], [item['id']])
            with self.assertRaises(ValueError): self.b.apple_peer(domain, 'commit_import_category_delete')

    def test_import_category_recreated_id_invalidates_approval(self):
        for domain in ('tasks', 'calendar'):
            svc, category, binding = self.linked(domain, category=True)
            operation = self.op(); plan = self.b.prepare_import_category_delete(domain, category['id'], operation)
            with svc._connect() as db:
                original = dict(db.execute(f'SELECT * FROM {svc.APPLE_CATEGORIES} WHERE id=?', (category['id'],)).fetchone())
                db.execute(f'DELETE FROM {svc.APPLE_CATEGORIES} WHERE id=?', (category['id'],))
                db.execute(f'INSERT INTO {svc.APPLE_CATEGORIES} ({",".join(original)}) VALUES ({",".join("?" for _ in original)})', tuple(original.values()))
            with self.assertRaises(ValueError): self.b.commit_import_category_delete(domain, category['id'], operation, plan['snapshot'])
            self.assertEqual(self.b.list_inbound_recoveries(domain), [])

    def test_import_category_rejects_started_operation_and_changed_members(self):
        for domain in ('tasks', 'calendar'):
            svc, category, _ = self.linked(domain, category=True)
            operation = self.op(); plan = self.b.prepare_import_category_delete(domain, category['id'], operation)
            item = self.item(domain, category['id'])
            with self.assertRaises(ValueError): self.b.commit_import_category_delete(domain, category['id'], operation, plan['snapshot'])
            with svc._connect() as db:
                db.execute('INSERT INTO apple_operations VALUES (?,?,?,?,?,?,?,?)', (self.op(), 'item', item['id'], 'delete', '{}', 'sending', '', '2026-11-01'))
            with self.assertRaises(ValueError): self.b.prepare_import_category_delete(domain, category['id'], self.op())
            with svc._connect() as db: db.execute("UPDATE apple_operations SET state='uncertain'")
            with self.assertRaises(ValueError): self.b.prepare_import_category_delete(domain, category['id'], self.op())

    def test_private_delete_receipts_before_missing_target_resolution(self):
        for domain in ('tasks', 'calendar'):
            for category in (False, True):
                svc = getattr(self.b, domain)
                row = svc.create_category({'name': self.op()}) if category else self.item(domain)
                method = 'delete_category' if category else 'delete_todo' if domain == 'tasks' else 'delete_event'
                key = ('category_id' if category else 'todo_id') if domain == 'tasks' else 'id'
                args = {key: row['id']}; operation = self.op()
                self.assertIsNone(self.b.prepare_delete_receipt(domain, method, args, operation))
                plan = self.b.local_delete_prepare(domain, row['id'], operation, category=category)
                self.b.local_delete_commit(domain, row['id'], operation, plan['snapshot'], category=category)
                replay = self.b.prepare_delete_receipt(domain, method, args, operation)
                self.assertTrue(replay['replayed']); self.assertFalse(replay['targetConflict'])
                with self.assertRaises(ValueError): self.b.prepare_delete_receipt(domain, method, {key: 'other'}, operation)
                with self.assertRaises(ValueError): self.b.prepare_delete_receipt(domain, method, {**args, 'owner': True}, operation)
                self.assertIsNone(self.b.prepare_delete_receipt(domain, method, args, self.op()))
                with self.assertRaises(ValueError): self.b.apple_peer(domain, 'prepare_delete_receipt')
                svc, linked, binding = self.linked(domain, category)
                queued, _ = self.queue(domain, linked, category)
                approved = queued['document']['_approved_request']
                svc.apple_begin_approved_delete(queued['id'], queued['document'], binding)
                svc.apple_finish_approved_delete(queued['id'])
                replay = self.b.prepare_delete_receipt(domain, approved['method'], approved['arguments'], queued['id'])
                self.assertTrue(replay['replayed']); self.assertEqual(replay['state'], 'done')
                with self.assertRaises(ValueError): self.b.prepare_delete_receipt(domain, approved['method'], {key: 'other'}, queued['id'])
            svc, category, _ = self.linked(domain, category=True)
            operation = self.op(); plan = self.b.prepare_import_category_delete(domain, category['id'], operation)
            result = self.b.commit_import_category_delete(domain, category['id'], operation, plan['snapshot'])
            key = 'category_id' if domain == 'tasks' else 'id'
            replay = self.b.prepare_delete_receipt(domain, 'delete_category', {key: category['id']}, operation)
            self.assertTrue(replay['replayed']); self.assertEqual(replay['recovery_id'], result['recovery_id'])

    def test_external_apple_receipt_does_not_resolve_deleted_source_key(self):
        svc, row, binding = self.linked('tasks')
        with svc._connect() as db: db.execute("UPDATE todos SET source_type='apple_reminder',source_key=? WHERE id=?", ('synthetic-source-key', row['id']))
        args = {'source_key': 'synthetic-source-key'}; operation = self.op()
        plan = self.b.prepare_apple_delete('tasks', 'delete_external_todo', args, operation)
        native = {'reminder_id': 'native', 'list_id': 'group', 'source_id': 'source'}
        approved = dict(version=1, source_id='source', group_id='group', series_apple_id='native', instance_apple_id='native', occurrence_on=None, native_fingerprint=digest(native), entity='item', scope='item', native_snapshot=native)
        self.b.commit_apple_delete('tasks', 'delete_external_todo', args, operation, plan['snapshot'], approved)
        queued = svc.apple_operations()[0]
        svc.apple_begin_approved_delete(operation, queued['document'], binding)
        svc.apple_finish_approved_delete(operation)
        self.assertEqual(self.b.prepare_delete_receipt('tasks', 'delete_external_todo', args, operation)['state'], 'done')
        with self.assertRaises(ValueError): self.b.prepare_delete_receipt('tasks', 'delete_external_todo', {'source_key': 'other'}, operation)
        with self.assertRaises(ValueError): self.b.prepare_delete_receipt('tasks', 'delete_external_todo', {**args, 'preserve_children': False}, operation)

    def test_occurrence_receipt_scope_and_recreated_generation(self):
        for scope, method in [('this', 'exclude_occurrence'), ('future', 'delete_event')]:
            row = self.item('calendar', recurring=True); operation = self.op(); day = '2026-11-02'
            plan = self.b.prepare_local_occurrence_delete(row['id'], day, scope, operation)
            self.b.commit_local_occurrence_delete(row['id'], day, scope, operation, plan['snapshot'])
            args = {'series_id': row['id'], 'occurrence_on': day} if method == 'exclude_occurrence' else {'id': row['id'], 'scope': scope, 'occurrence_on': day}
            replay = self.b.prepare_delete_receipt('calendar', method, args, operation)
            self.assertTrue(replay['replayed']); self.assertFalse(replay['targetConflict'])
            with self.assertRaises(ValueError): self.b.prepare_delete_receipt('calendar', method, {**args, 'occurrence_on': '2026-11-03'}, operation)
            with self.assertRaises(ValueError): self.b.prepare_delete_receipt('calendar', 'delete_event', {'id': row['id'], 'scope': 'future' if scope == 'this' else 'this', 'occurrence_on': day}, operation)
            with self.b.calendar._connect() as db:
                original = dict(db.execute('SELECT * FROM calendar_events WHERE id=?', (row['id'],)).fetchone())
                db.execute('DELETE FROM calendar_events WHERE id=?', (row['id'],))
                db.execute(f'INSERT INTO calendar_events ({",".join(original)}) VALUES ({",".join("?" for _ in original)})', tuple(original.values()))
            self.assertTrue(self.b.prepare_delete_receipt('calendar', method, args, operation)['targetConflict'])

    def test_confirmed_pending_intent_survives_owner_restart_but_not_send_or_cancel(self):
        for domain in ('tasks', 'calendar'):
            for category in (False, True):
                svc, row, binding = self.linked(domain, category)
                operation, replay = self.queue(domain, row, category, confirmed=True)
                expected = self.expected_confirmed_plan
                restarted = Business(self.tmp.name)
                try: self.assertEqual(restarted.prepare_confirmed_delete(domain, operation['id']), expected)
                finally: restarted.meta.close()
                self.assertTrue(replay()['replayed'])
                svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
                self.assertIsNone(self.b.prepare_confirmed_delete(domain, operation['id']))
                svc.apple_reset_approved_delete(operation['id'], operation['document'], binding)
                self.assertEqual(self.b.prepare_confirmed_delete(domain, operation['id']), expected)
                svc.cancel_apple_operation(operation['id'])
                self.assertIsNone(self.b.prepare_confirmed_delete(domain, operation['id']))
        svc, row, binding = self.linked('calendar', recurring=True)
        operation, _ = self.queue('calendar', row, scope='this', occurrence='2026-11-02', confirmed=True)
        self.assertEqual(self.b.prepare_confirmed_delete('calendar', operation['id']), self.expected_confirmed_plan)
        svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
        svc.apple_operation_error(operation['id'], 'unknown synthetic outcome')
        self.assertIsNone(self.b.prepare_confirmed_delete('calendar', operation['id']))

    def test_confirmed_intent_rejects_old_pending_changes_and_forged_fingerprint(self):
        for domain in ('tasks', 'calendar'):
            svc, rejected, _ = self.linked(domain)
            with self.assertRaises(ValueError): self.queue(domain, rejected, confirmed='invalid')
            self.assertEqual(svc.apple_operations(), [])
            svc, row, _ = self.linked(domain)
            old, _ = self.queue(domain, row)
            self.assertIsNone(self.b.prepare_confirmed_delete(domain, old['id']))
            request = old['document']['_approved_request']; approval = old['document']['_approval']
            with self.assertRaises(ValueError):
                self.b.commit_apple_delete(domain, request['method'], request['arguments'], old['id'], approval['snapshot'], approval['native'], {'version': 1, 'plan_fingerprint': self.expected_confirmed_plan['plan_fingerprint']})
            self.assertIsNone(self.b.prepare_confirmed_delete(domain, old['id']))
            svc, row, _ = self.linked(domain)
            operation, _ = self.queue(domain, row, confirmed=True)
            with svc._connect() as db: db.execute(f'UPDATE {svc.APPLE_ITEMS} SET title=? WHERE id=?', ('Changed after confirmation', row['id']))
            with self.assertRaises(ValueError): self.b.prepare_confirmed_delete(domain, operation['id'])
            with svc._connect() as db:
                db.execute(f'UPDATE {svc.APPLE_ITEMS} SET title=? WHERE id=?', ('Synthetic', row['id']))
                doc = json.loads(db.execute('SELECT document FROM apple_operations WHERE id=?', (operation['id'],)).fetchone()[0])
                doc['_confirmed_intent']['plan_fingerprint'] = '0'*64
                db.execute('UPDATE apple_operations SET document=? WHERE id=?', (json.dumps(doc), operation['id']))
            with self.assertRaises(ValueError): self.b.prepare_confirmed_delete(domain, operation['id'])
            with self.assertRaises(ValueError): self.b.apple_peer(domain, 'prepare_confirmed_delete')
            self.assertIsNone(self.b.prepare_confirmed_delete(domain, self.op()))

    def test_raw_deletes_remain_closed(self):
        for domain in ('tasks', 'calendar'):
            row = self.item(domain)
            method, key = ('delete_todo', 'todo_id') if domain == 'tasks' else ('delete_event', 'id')
            with self.assertRaises((PermissionError, ValueError)): self.b.invoke(domain, method, {key: row['id']})
            svc = getattr(self.b, domain)
            cat = svc.create_category({'name': self.op()})
            with self.assertRaises(PermissionError): svc.delete_category(cat['id'])
        with self.assertRaises(ValueError): self.b.commit_apple_delete('tasks', 'delete_todo', {'todo_id': 'fake'}, self.op(), 'a'*64, {'allowed-once': True})


if __name__ == '__main__': unittest.main()
