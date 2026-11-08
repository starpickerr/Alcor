"""Synthetic owner intent verification only; no native receipt or DSH proof."""
import copy
import json
import runpy
import unittest

fixtures = runpy.run_path('product/tests/apple-owner-approved-delete.py')
Fixture = fixtures['OwnerDeletion']
digest = fixtures['digest']


class FinishVerification(unittest.TestCase):
    setUp = Fixture.setUp
    op = Fixture.op
    item = Fixture.item
    linked = Fixture.linked
    queue = Fixture.queue

    def verify(self, domain, operation, plan):
        svc = getattr(self.b, domain)
        with svc._connect() as db: before = list(db.iterdump())
        result = self.b.verify_confirmed_delete_finish(domain, operation['id'], plan)
        with svc._connect() as db: self.assertEqual(list(db.iterdump()), before)
        return result

    def test_claimed_uncertain_and_done_verify_without_current_target(self):
        for domain in ('tasks', 'calendar'):
            for category in (False, True):
                svc, row, binding = self.linked(domain, category)
                operation, _ = self.queue(domain, row, category, confirmed=True)
                plan = self.expected_confirmed_plan
                with self.assertRaises(ValueError): self.verify(domain, operation, plan)
                svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
                self.assertTrue(self.verify(domain, operation, plan))
                svc.apple_operation_error(operation['id'], 'Synthetic receipt recovery pending')
                self.assertTrue(self.verify(domain, operation, plan))
                self.assertEqual(svc.apple_finish_approved_delete(operation['id'])['state'], 'done')
                self.assertTrue(self.verify(domain, operation, plan))
                self.assertIsNone(self.b.prepare_confirmed_delete(domain, operation['id']))
                with self.assertRaises(ValueError): self.b.apple_peer(domain, 'verify_confirmed_delete_finish')

    def test_legacy_cancelled_and_plan_tampering_are_rejected(self):
        svc, row, binding = self.linked('tasks')
        legacy, _ = self.queue('tasks', row)
        plan = self.expected_confirmed_plan
        svc.apple_begin_approved_delete(legacy['id'], legacy['document'], binding)
        with self.assertRaises(ValueError): self.verify('tasks', legacy, plan)
        svc, row, binding = self.linked('calendar')
        operation, _ = self.queue('calendar', row, confirmed=True)
        plan = self.expected_confirmed_plan
        svc.cancel_apple_operation(operation['id'])
        with self.assertRaises(ValueError): self.verify('calendar', operation, plan)
        svc, row, binding = self.linked('calendar')
        operation, _ = self.queue('calendar', row, confirmed=True)
        plan = self.expected_confirmed_plan
        svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
        mutations = [lambda p:p.update(operation_id='0'*64), lambda p:p.update(domain='tasks'),
                     lambda p:p.update(kind='reminders'), lambda p:p.update(extra='unexpected'),
                     lambda p:p['local_plan'].update(id='other'), lambda p:p['local_plan'].update(snapshot='0'*64),
                     lambda p:p['native'].update(source_id='other'), lambda p:p['native'].update(scope='future'),
                     lambda p:p['local_plan']['targets'][0].update(title='Changed display scope')]
        for mutation in mutations:
            bad = copy.deepcopy(plan); mutation(bad)
            bad['plan_fingerprint'] = digest({k:v for k,v in bad.items() if k != 'plan_fingerprint'})
            with self.assertRaises(ValueError): self.verify('calendar', operation, bad)
        self.assertTrue(self.verify('calendar', operation, plan))

    def test_stored_receipt_request_approval_and_marker_are_required(self):
        svc, row, binding = self.linked('tasks')
        operation, _ = self.queue('tasks', row, confirmed=True)
        plan = self.expected_confirmed_plan
        svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
        original = operation['document']
        for field in ('_confirmed_intent', '_approval', '_approved_request'):
            altered = copy.deepcopy(original); altered.pop(field)
            with svc._connect() as db: db.execute('UPDATE apple_operations SET document=? WHERE id=?', (json.dumps(altered), operation['id']))
            with self.assertRaises(ValueError): self.verify('tasks', operation, plan)
        with svc._connect() as db:
            db.execute('UPDATE apple_operations SET document=? WHERE id=?', (json.dumps(original), operation['id']))
            db.execute('DELETE FROM alcor_security_receipts WHERE operation_id=?', (operation['id'],))
        with self.assertRaises(ValueError): self.verify('tasks', operation, plan)

    def test_content_and_recreated_identity_are_preserved_by_finish(self):
        for domain in ('tasks', 'calendar'):
            for recreate in (False, True):
                svc, row, binding = self.linked(domain)
                operation, _ = self.queue(domain, row, confirmed=True)
                plan = self.expected_confirmed_plan
                svc.apple_begin_approved_delete(operation['id'], operation['document'], binding)
                with svc._connect() as db:
                    original = dict(db.execute(f'SELECT * FROM {svc.APPLE_ITEMS} WHERE id=?', (row['id'],)).fetchone())
                    if recreate:
                        db.execute(f'DELETE FROM {svc.APPLE_ITEMS} WHERE id=?', (row['id'],))
                        db.execute(f'INSERT INTO {svc.APPLE_ITEMS} ({",".join(original)}) VALUES ({",".join("?" for _ in original)})', tuple(original.values()))
                    else:
                        db.execute(f'UPDATE {svc.APPLE_ITEMS} SET title=? WHERE id=?', ('Local change after native send', row['id']))
                    current = dict(db.execute(f'SELECT * FROM {svc.APPLE_ITEMS} WHERE id=?', (row['id'],)).fetchone())
                self.assertTrue(self.verify(domain, operation, plan))
                self.assertEqual(svc.apple_finish_approved_delete(operation['id'])['state'], 'uncertain')
                with svc._connect() as db:
                    self.assertEqual(dict(db.execute(f'SELECT * FROM {svc.APPLE_ITEMS} WHERE id=?', (row['id'],)).fetchone()), current)
                    self.assertTrue(db.execute("SELECT 1 FROM apple_inbound_recovery WHERE status='operation_conflict'").fetchone())
                self.assertTrue(self.verify(domain, operation, plan))


del Fixture

if __name__ == '__main__': unittest.main()
