"""Private reminder inbound transactions; no native calls or public tool surface."""
import hashlib
import json
from datetime import datetime, timezone
from business.object_generations import generations_for
from business.apple_recovery import capture_inbound_delete


def _changed(message):
    raise ValueError('INBOUND_CHANGED: ' + message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _related(owner, db, identity, ignore_operation_id=None):
    plan = owner._local_delete_snapshot(db, identity, True, allow_apple=True, ignore_operation_id=ignore_operation_id)
    ids = [item['id'] for item in plan['targets']]
    marks = ','.join('?' for _ in ids)
    history = [dict(row) for row in db.execute(
        f'SELECT * FROM apple_completion_history WHERE local_id IN ({marks}) ORDER BY local_id,native_id,occurrence_on', ids)]
    tombstones = [dict(row) for row in db.execute(
        f'SELECT * FROM source_tombstones WHERE source_key IN (SELECT source_key FROM todos WHERE id IN ({marks})) ORDER BY source_key', ids)]
    return _digest({'plan': plan['snapshot'], 'history': history, 'tombstones': tombstones})


def _capture(owner, db, identity, target=None):
    raw = db.execute('SELECT * FROM todos WHERE id=?', (identity,)).fetchone()
    if raw is None:
        _changed('reminder no longer exists')
    row = dict(raw)
    categories = []
    category_ids = sorted({value for value in (row['category_id'], target) if value})
    for category_id in category_ids:
        category = db.execute('SELECT * FROM todo_categories WHERE id=?', (category_id,)).fetchone()
        if category is None:
            _changed('category no longer exists')
        categories.append({'row': dict(category), 'local': owner._apple_decorate(db, 'category', dict(category))})
    return {'version': 1, 'local_id': identity, 'target_category_id': target,
            'generation': generations_for(db, 'item', [identity])[0][1], 'row': row,
            'local': owner._apple_decorate(db, 'item', owner._with_progress(db, dict(row))),
            'categories': categories, 'category_generations': generations_for(db, 'category', category_ids),
            'related_snapshot': _related(owner, db, identity)}


def capture(owner, todo_id, target_category_id=None):
    with owner._connect() as db:
        db.execute('BEGIN')
        return _capture(owner, db, todo_id, target_category_id)


def _verify(owner, db, expected):
    if not isinstance(expected, dict) or expected.get('version') != 1:
        _changed('invalid capture')
    current = _capture(owner, db, expected['local_id'], expected.get('target_category_id'))
    if current != expected:
        _changed('owner state changed after capture')
    return current


def _identity(expected, native, previous=None, *, moving=False):
    binding = expected['local'].get('apple') or {}
    target = expected.get('target_category_id') or expected['row']['category_id']
    category = next((item['local'] for item in expected['categories'] if item['row']['id'] == target), None)
    if category is None:
        _changed('no captured reminder category')
    container = category.get('apple') or {}
    if not isinstance(native, dict) or not all(native.get(key) for key in ('reminder_id', 'list_id', 'source_id')):
        _changed('native reminder identity is incomplete')
    if container.get('apple_id') != native['list_id'] or container.get('source_id') != native['source_id']:
        _changed('native category/source differs from captured category')
    original = previous if previous is not None else native
    if not isinstance(original, dict) or binding.get('apple_id') != original.get('reminder_id') or binding.get('group_id') != original.get('list_id'):
        _changed('native reminder differs from captured binding')
    if moving:
        if not previous or binding.get('native')!=previous or not binding.get('source_id') or binding['source_id']!=previous.get('source_id'):
            _changed('move requires the exact captured original native identity')
    elif previous is not None:
        if not binding.get('source_id') or binding['source_id'] != previous.get('source_id'):
            _changed('completion requires a verified original source')
        if previous.get('source_id') != native['source_id'] or previous.get('list_id') != native['list_id']:
            _changed('completion successor changed source or list')
        if previous.get('reminder_id') != native['reminder_id'] and (not previous.get('external_id') or previous['external_id'] != native.get('external_id') or not previous.get('completed')):
            _changed('successor has no verified series identity')
    elif binding.get('source_id') not in (None, '', native['source_id']):
        _changed('native source differs from captured binding')
    # Legacy empty item source is accepted only for the exact bound item/group,
    # backed by the already-bound category and this native inventory row.
    return binding


def verify(owner, expected, native=None):
    with owner._connect() as db:
        db.execute('BEGIN')
        _verify(owner, db, expected)
        if native is not None:
            _identity(expected, native)
        return True


def apply(owner, expected, payload, native, metadata_only=False, previous_completion=None):
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _verify(owner, db, expected)
        result = _apply_checked(owner, db, expected, payload, native, metadata_only, previous_completion)
    owner._sync_scheduler_links()
    return result


def _apply_checked(owner, db, expected, payload, native, metadata_only, previous_completion, *, previous_move=None):
    binding = _identity(expected, native, previous_move if previous_move is not None else previous_completion, moving=previous_move is not None)
    pending = expected['local'].get('apple_pending', [])
    if pending or any(item['local'].get('apple_pending') for item in expected['categories']):
        _changed('pending owner operation requires reconciliation')
    old = expected['row']
    identity = old['id']
    if not metadata_only:
        fields = dict(payload)
        fields['category_id'] = expected.get('target_category_id') or old['category_id']
        if payload.get('category_id', fields['category_id']) != fields['category_id']:
            _changed('uncaptured target category')
        row = owner._validate_todo(fields, old)
        row['category_id'], row['list_name'] = owner._task_category(db, fields, row)
        if row['repeat_frequency'] != old['repeat_frequency'] and old['parent_id']:
            raise ValueError('subtasks cannot repeat')
        if row['repeat_frequency'] != 'none' and (db.execute('SELECT 1 FROM todos WHERE parent_id=?', (identity,)).fetchone() or db.execute('SELECT 1 FROM todo_dependencies WHERE task_id=? OR prerequisite_id=?', (identity, identity)).fetchone()):
            raise ValueError('tasks with subtasks or dependencies cannot repeat')
        columns = ('title notes due_on due_time timezone priority repeat_frequency repeat_days repeat_until repeat_interval planned_on list_name tags flagged checklist remind_at reminder_delivery category_id').split()
        completed = payload.get('completed', native.get('completed', False))
        if type(completed) is not bool:
            raise ValueError('completed must be boolean')
        status = old['status'] if old['status'] == 'cancelled' else ('completed' if completed else 'todo' if old['status'] == 'completed' else old['status'])
        completed_at = old['completed_at'] if status == 'cancelled' else payload.get('completed_at', native.get('completed_at')) if completed else None
        if completed_at:
            instant = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
            if instant.tzinfo is None:
                raise ValueError('completion timestamp requires timezone')
            completed_at = instant.astimezone(timezone.utc).isoformat()
        db.execute('UPDATE todos SET '+','.join(key+'=?' for key in columns)+',status=?,completed_at=?,user_modified=0,updated_at=? WHERE id=?',
                   [row[key] for key in columns] + [status, completed_at, datetime.now(timezone.utc).isoformat(), identity])
    metadata = {key: payload[key] for key in ('url','alarms','floating','native_recurrence','native_series_key','attendees') if key in payload}
    metadata.update(native_snapshot=native, native_managed=True)
    owner._apple_fields(db, identity, metadata, True)
    if previous_completion is not None:
        db.execute('INSERT OR IGNORE INTO apple_completion_history VALUES (?,?,?,?)',
                   (identity, previous_completion['reminder_id'], previous_completion.get('due_on') or '', json.dumps(previous_completion, ensure_ascii=False)))
    binding = {**binding, 'apple_id': native['reminder_id'], 'group_id': native['list_id'], 'source_id': native['source_id'], 'native': native}
    db.execute('INSERT OR REPLACE INTO apple_bindings VALUES (?,?,?)', ('item', identity, json.dumps(binding, ensure_ascii=False)))
    owner._cancel_pending_reminders(db, identity)
    owner._queue_scheduler_link(db, identity, None, old['status'])
    result = _capture(owner, db, identity, expected.get('target_category_id'))['local']
    return result


def apply_move(owner, expected, payload, native, previous_native):
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _verify(owner, db, expected)
        result=_apply_checked(owner,db,expected,payload,native,False,None,previous_move=previous_native)
        binding=owner._apple_binding(db,'item',expected['local_id'])
        binding.update(can_delete=bool(native.get('writable')),can_modify=bool(native.get('writable')) and not binding.get('issues'))
        db.execute('UPDATE apple_bindings SET document=? WHERE entity=? AND local_id=?',(json.dumps(binding,ensure_ascii=False),'item',expected['local_id']))
        result=_capture(owner,db,expected['local_id'],expected.get('target_category_id'))['local']
    owner._sync_scheduler_links()
    return result


def capture_missing(owner, todo_id):
    with owner._connect() as db:
        db.execute('BEGIN')
        expected = _capture(owner, db, todo_id)
        return {'version': 1, 'capture': expected, 'deletion_snapshot': expected['related_snapshot']}


def remove(owner, expected, source_id, group_id, reminder_id, expected_generation, deletion_snapshot):
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _verify(owner, db, expected)
        binding = expected['local'].get('apple') or {}
        if (not source_id or binding.get('source_id') != source_id or binding.get('group_id') != group_id or binding.get('apple_id') != reminder_id or expected['generation'] != expected_generation or expected['related_snapshot'] != deletion_snapshot or expected['local'].get('apple_pending')):
            _changed('deletion identity, generation or snapshot changed')
        identity = expected['local_id']
        capture_inbound_delete(db, 'tasks', 'item', identity)
        owner._delete_todo_tree(db, identity, preserve_children=True)
        db.execute("DELETE FROM apple_bindings WHERE entity='item' AND local_id=?", (identity,))
        db.execute('DELETE FROM apple_completion_history WHERE local_id=?', (identity,))
    owner._sync_scheduler_links()
    return True

def _completion_capture(owner, db, identity, operation):
    if not isinstance(operation, dict) or not isinstance(operation.get('id'), str) or not operation['id']:
        _changed('invalid completion operation')
    row = db.execute('SELECT * FROM apple_operations WHERE id=?', (operation['id'],)).fetchone()
    if row is None or row['state'] not in ('pending', 'sending', 'uncertain'):
        _changed('completion operation is not pending')
    raw = dict(row)
    if (raw['entity'] != 'item' or raw['action'] != 'complete' or raw['local_id'] != identity or
            any(operation.get(key) != raw[key] for key in ('entity', 'local_id', 'action')) or
            operation.get('document') != json.loads(raw['document']) or
            operation.get('binding') != owner._apple_binding(db, 'item', identity)):
        _changed('completion operation or binding changed')
    current = _capture(owner, db, identity)
    current['local']['apple_pending'] = [item for item in current['local']['apple_pending'] if item['id'] != operation['id']]
    if current['local']['apple_pending'] or any(item['local'].get('apple_pending') for item in current['categories']):
        _changed('another owner operation is pending')
    current['related_snapshot'] = _related(owner, db, identity, operation['id'])
    # Only the exact operation's transport state/error may advance after send.
    raw.pop('state')
    raw.pop('error')
    return {'version': 1, 'capture': current, 'operation_row': raw}


def capture_completion(owner, todo_id, operation):
    with owner._connect() as db:
        db.execute('BEGIN')
        return _completion_capture(owner, db, todo_id, operation)


def begin_completion(owner, expected, operation):
    if not isinstance(operation, dict):
        _changed('invalid completion operation')
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = _completion_capture(owner, db, operation.get('local_id'), operation)
        if current != expected:
            _changed('owner state changed before completion send')
        row = db.execute('SELECT state FROM apple_operations WHERE id=?', (operation['id'],)).fetchone()
        if row['state'] != 'pending':
            _changed('completion has already begun or has an unknown outcome')
        db.execute("UPDATE apple_operations SET state='sending',error='' WHERE id=? AND state='pending'", (operation['id'],))
    return True


def reject_completion(owner, expected, operation, error):
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if _completion_capture(owner,db,operation['local_id'],operation)!=expected:
            _changed('owner changed before definite native rejection')
        row=db.execute('SELECT state FROM apple_operations WHERE id=?',(operation['id'],)).fetchone()
        if not row or row['state']!='sending':
            _changed('completion state changed before definite rejection')
        db.execute("UPDATE apple_operations SET state='pending',error=? WHERE id=?",(str(error),operation['id']))
    return True


def finalize_completion(owner, expected, operation, payload, native, previous_completion=None):
    from business.security_receipts import read_receipt, save_receipt
    if not isinstance(operation, dict) or not isinstance(operation.get('id'), str):
        _changed('invalid completion operation')
    receipt_id = hashlib.sha256(('reminder-completion:' + operation['id']).encode()).hexdigest()
    arguments = {'expected': expected, 'operation': operation, 'payload': payload,
                 'native': native, 'previous_completion': previous_completion}
    result = {'committed': True, 'replayed': False, 'local_id': operation.get('local_id'), 'operation_id': operation['id']}
    with owner._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        receipt = read_receipt(db, receipt_id, 'reminder_completion', arguments)
        if receipt:
            return {**json.loads(receipt['targetId']), 'replayed': True}
        current = _completion_capture(owner, db, operation.get('local_id'), operation)
        if current != expected:
            _changed('owner state changed after completion capture')
        state = db.execute('SELECT state FROM apple_operations WHERE id=?', (operation['id'],)).fetchone()[0]
        if state not in ('sending', 'uncertain'):
            _changed('completion has not begun sending')
        wanted = operation['document'].get('completed')
        if type(wanted) is not bool:
            _changed('completion operation has no boolean outcome')
        observed = previous_completion if previous_completion is not None else native
        if observed.get('completed') is not wanted:
            _changed('native completion does not match requested outcome')
        if payload.get('completed', native.get('completed')) is not native.get('completed'):
            _changed('payload differs from native completion')
        _apply_checked(owner, db, current['capture'], payload, native, False, previous_completion)
        db.execute("UPDATE apple_operations SET state='done',error='' WHERE id=?", (operation['id'],))
        result['local'] = _capture(owner, db, operation['local_id'])['local']
        save_receipt(db, receipt_id, 'reminder_completion', arguments, json.dumps(result, ensure_ascii=False, sort_keys=True))
    owner._sync_scheduler_links()
    return result
