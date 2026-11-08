"""Private owner half of stopping one import and deleting its approved local copy."""
import json

from business.apple_recovery import capture_inbound_delete
from business.security_receipts import read_receipt, save_receipt, validate_operation_id


def _plan(svc, db, category_id):
    binding = svc._apple_binding(db, 'category', category_id)
    if not binding or binding.get('planned') or not binding.get('source_id') or not binding.get('apple_id'):
        raise ValueError('import category binding unavailable; review again')
    plan = svc._local_category_delete_snapshot(db, category_id, allow_apple=True)
    for target in plan['targets']:
        entity = 'category' if target.get('category') else 'item'
        if db.execute("SELECT 1 FROM apple_operations WHERE entity=? AND local_id=? AND state IN ('sending','uncertain')", (entity, target['id'])).fetchone():
            raise ValueError('Apple operation already started or uncertain; review before local deletion')
    return plan


def _replay(svc, db, category_id, receipt):
    result = {key:value for key,value in json.loads(receipt['targetId']).items() if not key.startswith('_')}
    present = db.execute(f'SELECT 1 FROM {svc.APPLE_CATEGORIES} WHERE id=?', (category_id,)).fetchone() is not None
    return {**result, 'replayed': True, 'targetConflict': present}


def prepare(svc, category_id, operation_id):
    validate_operation_id(operation_id)
    with svc._connect() as db:
        db.execute('BEGIN')
        receipt = read_receipt(db, operation_id, 'delete_import_category', {'id': category_id})
        if receipt: return _replay(svc, db, category_id, receipt)
        return _plan(svc, db, category_id)


def commit(svc, domain, category_id, operation_id, snapshot):
    validate_operation_id(operation_id)
    if not isinstance(snapshot, str): raise ValueError('invalid deletion snapshot')
    with svc._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        args = {'id': category_id}
        receipt = read_receipt(db, operation_id, 'delete_import_category', args)
        if receipt: return _replay(svc, db, category_id, receipt)
        plan = _plan(svc, db, category_id)
        if plan['snapshot'] != snapshot: raise ValueError('import category snapshot conflict; review again')
        recovery_id = capture_inbound_delete(db, domain, 'category', category_id)
        if not recovery_id: raise ValueError('unable to preserve import recovery')
        if domain == 'tasks':
            # Stopping an import must not suppress it after an explicit later reconnection.
            previous_tombstones = {}
            for target in plan['targets']:
                if target.get('category'): continue
                row = db.execute('SELECT source_type,source_key FROM todos WHERE id=?', (target['id'],)).fetchone()
                if row['source_type'] == 'apple_reminder' and row['source_key']:
                    previous_tombstones[row['source_key']] = db.execute('SELECT * FROM source_tombstones WHERE source_key=?', (row['source_key'],)).fetchone()
            roots = db.execute('SELECT id FROM todos WHERE category_id=?', (category_id,)).fetchall()
            for row in roots: svc._delete_todo_tree(db, row['id'])
            for key, previous in previous_tombstones.items():
                if previous is None: db.execute('DELETE FROM source_tombstones WHERE source_key=?', (key,))
                else: db.execute('INSERT OR REPLACE INTO source_tombstones VALUES (?,?)', tuple(previous))
        else:
            for row in db.execute('SELECT * FROM calendar_events WHERE category_id=?', (category_id,)).fetchall():
                svc._delete_event(db, row)
        db.execute(f'DELETE FROM {svc.APPLE_CATEGORIES} WHERE id=?', (category_id,))
        for target in plan['targets']:
            entity = 'category' if target.get('category') else 'item'
            db.execute('DELETE FROM apple_bindings WHERE entity=? AND local_id=?', (entity, target['id']))
            db.execute("UPDATE apple_operations SET state='cancelled',error='' WHERE entity=? AND local_id=? AND state='pending'", (entity, target['id']))
            if entity == 'item': db.execute('DELETE FROM apple_completion_history WHERE local_id=?', (target['id'],))
        result = {'id': category_id, 'state': 'done', 'deleted': True, 'replayed': False, 'recovery_id': recovery_id}
        proof={**result,'_snapshot':snapshot,'_targets':[{'id':row['id'],'entity':'category' if row.get('category') else 'item'} for row in plan['targets']]}
        save_receipt(db, operation_id, 'delete_import_category', args, json.dumps(proof, sort_keys=True))
    if domain == 'tasks': svc._sync_scheduler_links()
    return result


def verify(svc, operation_id, category_id, snapshot):
    """Read an exact committed local deletion; never retry it or infer from absence."""
    validate_operation_id(operation_id)
    with svc._connect() as db:
        db.execute('BEGIN')
        receipt=read_receipt(db,operation_id,'delete_import_category',{'id':category_id})
        if not receipt:return None
        proof=json.loads(receipt['targetId'])
        if proof.get('_snapshot')!=snapshot or not proof.get('_targets') or proof.get('id')!=category_id or proof.get('state')!='done':
            raise ValueError('import removal receipt does not prove this frozen plan')
        for target in proof['_targets']:
            table=svc.APPLE_CATEGORIES if target['entity']=='category' else svc.APPLE_ITEMS
            if (db.execute(f'SELECT 1 FROM {table} WHERE id=?',(target['id'],)).fetchone()
                    or db.execute('SELECT 1 FROM apple_bindings WHERE entity=? AND local_id=?',(target['entity'],target['id'])).fetchone()):
                raise ValueError('import removal target was recreated or rebound; keep current data')
        return _replay(svc,db,category_id,receipt)
