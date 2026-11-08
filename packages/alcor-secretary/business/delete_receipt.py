"""Read deletion receipts before resolving targets which may already be gone."""
import json

from business.apple_delete_approval import _request, _replay as apple_replay
from business.import_category_delete import _replay as import_replay
from business.object_generations import generations_for
from business.security_receipts import read_receipt, validate_operation_id


def prepare(svc, domain, method, arguments, operation_id):
    validate_operation_id(operation_id)
    entity, identity, args = _request(domain, method, arguments)
    with svc._connect() as db:
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alcor_security_receipts'").fetchone():
            return None
        stored = db.execute('SELECT method FROM alcor_security_receipts WHERE operation_id=?', (operation_id,)).fetchone()
        if not stored: return None
        kind = stored['method']
        if kind.startswith('apple_delete:'):
            receipt = read_receipt(db, operation_id, f'apple_delete:{domain}:{method}', args)
            return apple_replay(db, svc, entity, receipt['targetId'], operation_id)
        if method == 'delete_category' and kind in ('delete_category', 'delete_import_category'):
            canonical = {'id': identity}
        elif domain == 'tasks' and method == 'delete_todo' and kind == 'delete_todo':
            canonical = {'id': identity, 'preserve_children': args['preserve_children']}
        elif domain == 'calendar' and method == 'delete_event' and kind == 'delete_event' and not args.get('scope') and not args.get('occurrence_on'):
            canonical = {'id': identity}
        elif domain == 'calendar' and kind == 'delete_occurrence' and method in ('delete_event', 'exclude_occurrence'):
            scope = 'this' if method == 'exclude_occurrence' else args.get('scope') or 'this'
            if scope not in ('this', 'future') or not args.get('occurrence_on'):
                raise ValueError('idempotency conflict: different approved occurrence')
            canonical = {'id': identity, 'occurrence_on': args['occurrence_on'], 'scope': scope}
        else:
            raise ValueError('idempotency conflict: different approved operation')
        receipt = read_receipt(db, operation_id, kind, canonical)
        if kind == 'delete_import_category': return import_replay(svc, db, identity, receipt)
        table = svc.APPLE_CATEGORIES if entity == 'category' else svc.APPLE_ITEMS
        present = db.execute(f'SELECT 1 FROM {table} WHERE id=?', (identity,)).fetchone() is not None
        conflict = present
        if kind == 'delete_occurrence':
            original = json.loads(receipt['targetId'])
            conflict = bool(present and generations_for(db, 'item', [identity])[0][1] != original['generation'])
        return {'replayed': True, 'id': identity, 'receipt': receipt, 'targetConflict': conflict}
